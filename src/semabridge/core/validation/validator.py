"""
Physical Validator.

Validates that an SML Model's physical dependencies (Tables, Columns)
actually exist in the target Snowflake environment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum

from semabridge.formats.sml.models import SMLModel
from semabridge.core.validation.inspector import SnowflakeInspector
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class ValidationStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"


@dataclass
class ValidationError:
    """Represents a single validation issue."""
    object_type: str  # "Table", "Column", "Namespace"
    object_name: str
    message: str
    severity: str = "ERROR" # ERROR or WARNING


@dataclass
class ValidationReport:
    """Complete validation report."""
    status: ValidationStatus
    errors: List[ValidationError] = field(default_factory=list)
    checked_tables: int = 0
    checked_columns: int = 0
    
    @property
    def has_errors(self) -> bool:
        return any(e.severity == "ERROR" for e in self.errors)
        
    def add_error(self, type_: str, name: str, msg: str):
        self.errors.append(ValidationError(type_, name, msg, "ERROR"))
        
    def add_warning(self, type_: str, name: str, msg: str):
        self.errors.append(ValidationError(type_, name, msg, "WARNING"))


class PhysicalValidator:
    """
    Validates SML Model against Snowflake physical schema.
    """
    
    def __init__(self, inspector: SnowflakeInspector):
        self.inspector = inspector

    def validate(self, sml_model: SMLModel) -> ValidationReport:
        """
        Run full validation suite.
        """
        report = ValidationReport(status=ValidationStatus.PASSED)
        
        logger.info(f"Starting physical validation for model: {sml_model.unique_name}")
        
        with self.inspector.inspector_context() as inspector:
            # 1. Namespace Check
            if not inspector.check_namespace():
                report.add_error(
                    "Namespace", 
                    f"{inspector.config.database}.{inspector.config.schema_name}",
                    "Target Database/Schema does not exist or is not accessible"
                )
                report.status = ValidationStatus.FAILED
                return report  # Critical failure, stop here
            
            # 2. Table & Column Check
            for dataset in sml_model.datasets:
                report.checked_tables += 1
                
                # Determine physical table name (source_table overrides unique_name)
                phys_table_name = dataset.source_table or dataset.unique_name
                
                # Check table existence
                schema = inspector.get_table_schema(phys_table_name)
                
                if not schema:
                    report.add_error(
                        "Table",
                        phys_table_name,
                        f"Physical table not found for dataset '{dataset.unique_name}'"
                    )
                    continue
                
                # Check columns
                for col in dataset.columns:
                    # Skip internal/computational cols
                    if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                        continue
                        
                    report.checked_columns += 1
                    
                    # Sanitize (uppercase for comparison)
                    col_key = col.unique_name.upper().strip('"')
                    
                    if col_key not in schema:
                        report.add_error(
                            "Column",
                            f"{phys_table_name}.{col.unique_name}",
                            f"Column '{col.unique_name}' not found in table '{phys_table_name}'"
                        )
                    # TODO: Add Type Check logic here (Future)

        if report.has_errors:
            report.status = ValidationStatus.FAILED
            
        logger.info(
            f"Validation complete: {report.status.value}. "
            f"Checked {report.checked_tables} tables, {report.checked_columns} columns."
        )
        return report
