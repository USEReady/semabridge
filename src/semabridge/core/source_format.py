"""
Source Format artifact model.

Defines the normalized extraction output before SML conversion (Step 4-5).
This is the intermediate format between raw source data and canonical SML.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TableInfo(BaseModel):
    """Information about a source table."""
    name: str
    description: str = ""
    row_count: Optional[int] = None
    schema_name: str = ""
    database_name: str = ""


class ColumnInfo(BaseModel):
    """Information about a source column."""
    name: str
    data_type: str
    is_nullable: bool = True
    is_primary_key: bool = False
    description: str = ""
    ordinal_position: int = 0


class ForeignKeyInfo(BaseModel):
    """Foreign key relationship information."""
    name: str
    from_table: str
    from_columns: List[str]
    to_table: str
    to_columns: List[str]


class ValidationIssue(BaseModel):
    """A validation issue found during format validation."""
    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    location: Optional[str] = None


class SourceFormat(BaseModel):
    """
    Normalized extraction output before SML conversion.
    
    This is the Source Format artifact produced at Step 4 and validated at Step 5.
    It provides a platform-agnostic representation of the extracted semantic model.
    """
    
    format_version: str = "1.0"
    source_type: Literal["snowflake", "fabric", "pbix"]
    extraction_timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    
    # Execution context
    project_id: str
    run_id: str
    
    # For Snowflake source
    database: str = ""
    schema_name: str = ""
    tables: Dict[str, TableInfo] = Field(default_factory=dict)
    columns: Dict[str, List[ColumnInfo]] = Field(default_factory=dict)
    foreign_keys: List[ForeignKeyInfo] = Field(default_factory=list)
    primary_keys: Dict[str, List[str]] = Field(default_factory=dict)
    semantic_view_name: Optional[str] = None
    semantic_view_ddl: Optional[str] = None
    
    # For Fabric source
    tmsl_definition: Optional[Dict[str, Any]] = None
    workspace_id: Optional[str] = None
    dataset_id: Optional[str] = None
    dataset_name: Optional[str] = None
    
    # For PBIX source
    pbix_path: Optional[str] = None
    field_aliases: List[Dict[str, Any]] = Field(default_factory=list)
    # Report-layer field references that don't match any current column/
    # measure (e.g. the model was renamed after the report was built).
    # Table-scoped only, not entity-scoped — see LocalPBIXConnector
    # docstring for why this is never auto-attached to a specific field.
    unresolved_report_field_references: List[Dict[str, Any]] = Field(default_factory=list)
    # Report-layer aliases claimed by more than one field (e.g. two visuals
    # coincidentally titled the same thing over different measures/columns)
    # — excluded from synonyms so Cortex Analyst never has to guess which
    # field a business user meant. See LocalPBIXConnector.discover().
    ambiguous_report_aliases: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Row counts for classification
    row_counts: Dict[str, int] = Field(default_factory=dict)
    
    def validate_format(self) -> List[ValidationIssue]:
        """
        Apply format definition validation (Step 5).
        
        Returns a list of validation issues. Empty list means valid.
        """
        issues: List[ValidationIssue] = []
        
        # Required field validation
        if not self.project_id:
            issues.append(ValidationIssue(
                severity="error",
                code="MISSING_PROJECT_ID",
                message="project_id is required",
            ))
        
        if not self.run_id:
            issues.append(ValidationIssue(
                severity="error",
                code="MISSING_RUN_ID",
                message="run_id is required",
            ))
        
        # Source-specific validation
        if self.source_type == "snowflake":
            issues.extend(self._validate_snowflake_source())
        elif self.source_type == "fabric":
            issues.extend(self._validate_fabric_source())
        elif self.source_type == "pbix":
            issues.extend(self._validate_pbix_source())
        
        return issues
    
    def _validate_snowflake_source(self) -> List[ValidationIssue]:
        """Validate Snowflake-specific source format."""
        issues = []
        
        if not self.tables:
            issues.append(ValidationIssue(
                severity="error",
                code="NO_TABLES",
                message="No tables found in extraction",
            ))
        
        # Check that all tables have columns
        for table_name in self.tables:
            if table_name not in self.columns or not self.columns[table_name]:
                issues.append(ValidationIssue(
                    severity="warning",
                    code="TABLE_NO_COLUMNS",
                    message=f"Table '{table_name}' has no columns defined",
                    location=table_name,
                ))
        
        # Validate foreign key references
        for fk in self.foreign_keys:
            if fk.from_table not in self.tables:
                issues.append(ValidationIssue(
                    severity="error",
                    code="FK_INVALID_FROM_TABLE",
                    message=f"Foreign key '{fk.name}' references non-existent table '{fk.from_table}'",
                    location=fk.name,
                ))
            if fk.to_table not in self.tables:
                issues.append(ValidationIssue(
                    severity="error",
                    code="FK_INVALID_TO_TABLE",
                    message=f"Foreign key '{fk.name}' references non-existent table '{fk.to_table}'",
                    location=fk.name,
                ))
        
        return issues
    
    def _validate_fabric_source(self) -> List[ValidationIssue]:
        """Validate Fabric-specific source format."""
        issues = []
        
        if not self.tmsl_definition:
            issues.append(ValidationIssue(
                severity="error",
                code="NO_TMSL",
                message="TMSL definition is required for Fabric source",
            ))
        
        if not self.workspace_id:
            issues.append(ValidationIssue(
                severity="warning",
                code="NO_WORKSPACE_ID",
                message="workspace_id is recommended for Fabric source",
            ))
        
        if not self.dataset_id:
            issues.append(ValidationIssue(
                severity="warning",
                code="NO_DATASET_ID",
                message="dataset_id is recommended for Fabric source",
            ))
        
        return issues
    
    def _validate_pbix_source(self) -> List[ValidationIssue]:
        """Validate PBIX-specific source format."""
        issues = []
        
        if not self.tmsl_definition:
            issues.append(ValidationIssue(
                severity="error",
                code="NO_TMSL",
                message="TMSL/DataModelSchema is required for PBIX source",
            ))
        
        if not self.pbix_path:
            issues.append(ValidationIssue(
                severity="warning",
                code="NO_PBIX_PATH",
                message="pbix_path is recommended for traceability",
            ))
        
        return issues

    def has_errors(self) -> bool:
        """Check if there are any error-level validation issues."""
        return any(issue.severity == "error" for issue in self.validate_format())
    
    def get_diagnostic_message(self) -> str:
        """Get actionable diagnostic message for validation failures."""
        issues = self.validate_format()
        if not issues:
            return "Source format is valid."
        
        lines = ["Source format validation failed:"]
        for issue in issues:
            icon = {"error": "✗", "warning": "⚠", "info": "ℹ"}.get(issue.severity, "?")
            loc = f" at {issue.location}" if issue.location else ""
            lines.append(f"  {icon} [{issue.code}] {issue.message}{loc}")
        
        return "\n".join(lines)


def from_snowflake_metadata(
    project_id: str,
    run_id: str,
    metadata: Dict[str, Any],
    semantic_view_name: Optional[str] = None,
    semantic_view_ddl: Optional[str] = None,
) -> SourceFormat:
    """
    Create SourceFormat from Snowflake extractor output.
    
    This is the parsing instruction for Snowflake source format.
    """
    tables = {}
    for name, info in metadata.get("tables", {}).items():
        tables[name] = TableInfo(
            name=name,
            description=info.get("description", ""),
            row_count=info.get("row_count"),
            schema_name=metadata.get("schema", ""),
            database_name=metadata.get("database", ""),
        )
    
    columns = {}
    for table_name, col_list in metadata.get("columns", {}).items():
        columns[table_name] = [
            ColumnInfo(
                name=col.get("COLUMN_NAME", col.get("name", "")),
                data_type=col.get("DATA_TYPE", col.get("data_type", "VARCHAR")),
                is_nullable=col.get("IS_NULLABLE", "YES") == "YES",
                is_primary_key=col.get("COLUMN_NAME", col.get("name", "")) in metadata.get("primary_keys", {}).get(table_name, []),
                ordinal_position=col.get("ORDINAL_POSITION", 0),
            )
            for col in col_list
        ]
    
    foreign_keys = []
    for fk in metadata.get("foreign_keys", []):
        foreign_keys.append(ForeignKeyInfo(
            name=fk.get("name", f"{fk.get('from_table', '')}_fk"),
            from_table=fk.get("from_table", ""),
            from_columns=[fk.get("from_column", "")] if isinstance(fk.get("from_column"), str) else fk.get("from_columns", []),
            to_table=fk.get("to_table", ""),
            to_columns=[fk.get("to_column", "")] if isinstance(fk.get("to_column"), str) else fk.get("to_columns", []),
        ))
    
    return SourceFormat(
        source_type="snowflake",
        project_id=project_id,
        run_id=run_id,
        database=metadata.get("database", ""),
        schema_name=metadata.get("schema", ""),
        tables=tables,
        columns=columns,
        foreign_keys=foreign_keys,
        primary_keys=metadata.get("primary_keys", {}),
        semantic_view_name=semantic_view_name,
        semantic_view_ddl=semantic_view_ddl,
    )


def from_fabric_tmsl(
    project_id: str,
    run_id: str,
    tmsl: Dict[str, Any],
    workspace_id: str,
    dataset_id: str,
    row_counts: Optional[Dict[str, int]] = None,
) -> SourceFormat:
    """
    Create SourceFormat from Fabric TMSL extraction.
    
    This is the parsing instruction for Fabric source format.
    """
    # Extract dataset name from TMSL
    model = tmsl.get("model", {})
    dataset_name = model.get("name", "")
    
    return SourceFormat(
        source_type="fabric",
        project_id=project_id,
        run_id=run_id,
        tmsl_definition=tmsl,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        row_counts=row_counts or {},
    )


def from_pbix_tmsl(
    project_id: str,
    run_id: str,
    tmsl: Dict[str, Any],
    pbix_path: str,
    field_aliases: Optional[List[Dict[str, Any]]] = None,
    unresolved_report_field_references: Optional[List[Dict[str, Any]]] = None,
    ambiguous_report_aliases: Optional[List[Dict[str, Any]]] = None,
) -> SourceFormat:
    """
    Create SourceFormat from local PBIX DataModelSchema extraction.

    The PBIX DataModelSchema is structurally identical to Fabric TMSL,
    so the same TMSLTransformer can convert it to SML.

    Args:
        project_id: Project identifier (model name).
        run_id: Unique run identifier.
        tmsl: Raw DataModelSchema dict extracted from the .pbix archive.
        pbix_path: Absolute path to the source .pbix file.
        field_aliases: Optional list of report layout aliases (measures and columns).
        unresolved_report_field_references: Optional list of report-layer
            field references that don't match any current column/measure.
        ambiguous_report_aliases: Optional list of report-layer aliases
            excluded from synonyms because more than one field claimed them.
    """
    model = tmsl.get("model", {})
    dataset_name = model.get("name", "")

    return SourceFormat(
        source_type="pbix",
        project_id=project_id,
        run_id=run_id,
        tmsl_definition=tmsl,
        dataset_name=dataset_name,
        pbix_path=pbix_path,
        field_aliases=field_aliases or [],
        unresolved_report_field_references=unresolved_report_field_references or [],
        ambiguous_report_aliases=ambiguous_report_aliases or [],
    )
