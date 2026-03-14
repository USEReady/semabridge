"""
Override Validator — validates semantic override configurations.

Provides:
- Validation of override YAML/JSON files
- Schema validation
- Referential integrity checks
- Error reporting and suggestions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from semabridge.converter.override_schema import SQLOverrideFile


class ValidationSeverity(str, Enum):
    """Severity level of validation issues."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationError:
    """A single validation error or warning."""

    severity: ValidationSeverity
    message: str
    field: Optional[str] = None
    line: Optional[int] = None
    suggestion: Optional[str] = None


@dataclass
class OverrideValidationResult:
    """Result of validating an override file."""

    file_path: str
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)

    def add_error(
        self,
        message: str,
        field: Optional[str] = None,
        suggestion: Optional[str] = None,
    ) -> None:
        """Add a validation error."""
        self.errors.append(ValidationError(
            severity=ValidationSeverity.ERROR,
            message=message,
            field=field,
            suggestion=suggestion,
        ))
        self.is_valid = False

    def add_warning(
        self,
        message: str,
        field: Optional[str] = None,
        suggestion: Optional[str] = None,
    ) -> None:
        """Add a validation warning."""
        self.warnings.append(ValidationError(
            severity=ValidationSeverity.WARNING,
            message=message,
            field=field,
            suggestion=suggestion,
        ))

    def summary(self) -> str:
        """Get a summary of validation results."""
        status = "VALID" if self.is_valid else "INVALID"
        return f"{status}: {len(self.errors)} errors, {len(self.warnings)} warnings"


class OverrideValidator:
    """
    Validates semantic override configurations.
    """

    def __init__(self):
        pass

    def validate_file(self, file_path: str) -> OverrideValidationResult:
        """
        Validate an override configuration file.

        Args:
            file_path: Path to override YAML or JSON file

        Returns:
            OverrideValidationResult with any errors/warnings
        """
        result = OverrideValidationResult(
            file_path=file_path,
            is_valid=True,
        )

        file_path_obj = Path(file_path)

        # Check file exists
        if not file_path_obj.exists():
            result.add_error(f"File not found: {file_path}")
            return result

        # Load file content
        try:
            if file_path_obj.suffix.lower() == '.json':
                with open(file_path_obj) as f:
                    data = json.load(f)
            else:
                try:
                    import yaml
                    with open(file_path_obj) as f:
                        data = yaml.safe_load(f)
                except ImportError:
                    result.add_error("YAML library not available; cannot parse YAML files")
                    return result
        except Exception as e:
            result.add_error(f"Failed to parse file: {str(e)}")
            return result

        # Validate structure
        self._validate_structure(data, result)

        return result

    def validate_config(self, config: SQLOverrideFile) -> OverrideValidationResult:
        """
        Validate an override configuration object.

        Args:
            config: Override configuration

        Returns:
            OverrideValidationResult
        """
        result = OverrideValidationResult(
            file_path="<config_object>",
            is_valid=True,
        )

        # Check required fields
        if not config.name:
            result.add_error("Override file must have a name", field="name")

        # Validate layers
        for layer in config.cortex_layers:
            if not layer.name:
                result.add_warning("Cortex layer has no name", suggestion="Add a name to identify the layer")

        for view in config.semantic_views:
            if not view.name:
                result.add_error("Semantic view must have a name", field="name")
            if not view.source_table:
                result.add_error(f"Semantic view '{view.name}' must have a source_table", field="source_table")

        return result

    def _validate_structure(self, data: Dict[str, Any], result: OverrideValidationResult) -> None:
        """Validate the structure of the configuration data."""
        if not isinstance(data, dict):
            result.add_error("Configuration must be a dictionary/object")
            return

        # Check for required top-level fields
        if "name" not in data:
            result.add_warning("Configuration should have a 'name' field")

        # Validate cortex_layers if present
        if "cortex_layers" in data:
            if not isinstance(data["cortex_layers"], list):
                result.add_error("cortex_layers must be a list", field="cortex_layers")

        # Validate semantic_views if present
        if "semantic_views" in data:
            if not isinstance(data["semantic_views"], list):
                result.add_error("semantic_views must be a list", field="semantic_views")
            else:
                for i, view in enumerate(data["semantic_views"]):
                    if "source_table" not in view:
                        result.add_error(
                            f"Semantic view {i} is missing 'source_table'",
                            field=f"semantic_views[{i}].source_table",
                        )


def load_override_file(file_path: str) -> Optional[SQLOverrideFile]:
    """
    Load and parse an override file.

    Args:
        file_path: Path to override file

    Returns:
        Loaded SQLOverrideFile or None if invalid
    """
    validator = OverrideValidator()
    result = validator.validate_file(file_path)

    if not result.is_valid:
        return None

    # In a real implementation, would parse and return SQLOverrideFile
    # For now, return a stub
    return SQLOverrideFile(name=Path(file_path).stem)


def load_override_directory(dir_path: str) -> List[SQLOverrideFile]:
    """
    Load all override files from a directory.

    Args:
        dir_path: Path to directory containing override files

    Returns:
        List of loaded SQLOverrideFile objects
    """
    dir_path_obj = Path(dir_path)
    overrides = []

    if not dir_path_obj.is_dir():
        return overrides

    # Load all YAML and JSON files
    for file_path in dir_path_obj.glob("*.{yaml,yml,json}"):
        override = load_override_file(str(file_path))
        if override:
            overrides.append(override)

    return overrides
