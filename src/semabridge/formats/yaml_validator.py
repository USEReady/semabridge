"""
YAML Validator with Line Number Support.

Provides strict validation of YAML configuration files with precise
error messages including file, line, and column information.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from semabridge.formats.schema import (
    CONFIG_SCHEMA,
    FieldSchema,
    SENSITIVE_PATTERNS,
    ALL_KNOWN_KEYS,
    suggest_similar_key,
    get_schema_for_path,
    get_known_keys_at_level,
)


@dataclass
class ValidationError:
    """
    Validation error with precise location information.
    
    Attributes:
        file: Path to the file containing the error
        line: Line number (1-indexed)
        column: Column number (1-indexed, optional)
        message: Human-readable error message
        key_path: Dot-separated path to the problematic key
        expected: What was expected (for enum/type errors)
        found: What was actually found
        suggestion: Suggested fix (e.g., "Did you mean 'logging'?")
        example: Example of correct usage
        severity: "error" or "warning"
    """
    file: str
    line: int
    column: Optional[int]
    message: str
    key_path: str = ""
    expected: Optional[str] = None
    found: Optional[str] = None
    suggestion: Optional[str] = None
    example: Optional[str] = None
    severity: str = "error"

    def format(self, show_example: bool = True) -> str:
        """Format error as a human-readable string."""
        location = f"(line {self.line}"
        if self.column:
            location += f", column {self.column}"
        location += ")"
        
        parts = [f"Error in {self.file} {location}:"]
        parts.append(f"  {self.message}")
        
        if self.expected:
            parts.append(f"  Expected: {self.expected}")
        if self.found:
            parts.append(f"  Found: {self.found}")
        if self.suggestion:
            parts.append(f"  {self.suggestion}")
        if show_example and self.example:
            parts.append("")
            parts.append("Example of correct structure:")
            for line in self.example.split("\n"):
                parts.append(f"  {line}")
        
        return "\n".join(parts)


@dataclass
class ValidationWarning(ValidationError):
    """Warning (non-fatal) with location information."""
    severity: str = "warning"
    
    def format(self, show_example: bool = False) -> str:
        location = f"(line {self.line}"
        if self.column:
            location += f", column {self.column}"
        location += ")"
        
        return f"Warning in {self.file} {location}: {self.message}"


class YAMLValidator:
    """
    Validates YAML configuration files with line-number error reporting.
    
    Uses ruamel.yaml to preserve line/column information during parsing.
    """
    
    def __init__(self):
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
    
    def load_yaml_with_positions(
        self, 
        path: Path,
    ) -> Tuple[CommentedMap, List[ValidationError]]:
        """
        Load a YAML file, preserving line/column information.
        
        Args:
            path: Path to YAML file
            
        Returns:
            Tuple of (parsed content, list of syntax errors)
        """
        errors = []
        
        if not path.exists():
            errors.append(ValidationError(
                file=str(path),
                line=1,
                column=None,
                message=f"Configuration file not found: {path}",
            ))
            return CommentedMap(), errors
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = self.yaml.load(f)
        except YAMLError as e:
            # Extract line/column from ruamel error
            line = getattr(e, 'problem_mark', None)
            if line:
                errors.append(ValidationError(
                    file=str(path),
                    line=line.line + 1,
                    column=line.column + 1,
                    message=f"YAML syntax error: {e.problem if hasattr(e, 'problem') else str(e)}",
                ))
            else:
                errors.append(ValidationError(
                    file=str(path),
                    line=1,
                    column=None,
                    message=f"YAML syntax error: {e}",
                ))
            return CommentedMap(), errors
        
        if content is None:
            content = CommentedMap()
        
        if not isinstance(content, (CommentedMap, dict)):
            errors.append(ValidationError(
                file=str(path),
                line=1,
                column=None,
                message=f"Configuration must be a YAML object (mapping), got: {type(content).__name__}",
            ))
            return CommentedMap(), errors
        
        return content, errors
    
    def get_line_col(
        self, 
        data: Union[CommentedMap, Any], 
        key: str = None,
    ) -> Tuple[int, Optional[int]]:
        """
        Get line and column for a key in CommentedMap.
        
        Args:
            data: CommentedMap or dict
            key: Key to look up (None for the map itself)
            
        Returns:
            Tuple of (line, column) - both 1-indexed
        """
        try:
            if isinstance(data, CommentedMap) and hasattr(data, 'lc'):
                lc = data.lc
                if key is not None and key in data:
                    # Try to get key location from lc.data
                    if hasattr(lc, 'data') and isinstance(lc.data, dict) and key in lc.data:
                        line, col = lc.data[key][:2]
                        return line + 1, col + 1
                    # Try lc.key() method
                    if hasattr(lc, 'key') and callable(lc.key):
                        try:
                            line, col = lc.key(key)
                            return line + 1, col + 1
                        except (KeyError, TypeError):
                            pass
                # Fall back to start of map
                if hasattr(lc, 'line') and hasattr(lc, 'col'):
                    return lc.line + 1, lc.col + 1
        except Exception:
            pass
        
        return 1, None
    
    def validate_required_fields(
        self,
        config: CommentedMap,
        file_path: str,
        schema: Dict[str, FieldSchema] = None,
        parent_path: str = "",
    ) -> List[ValidationError]:
        """
        Validate that required fields are present.
        
        Args:
            config: Parsed YAML content
            file_path: Path to file (for error messages)
            schema: Schema to validate against
            parent_path: Parent path for nested validation
            
        Returns:
            List of validation errors
        """
        if schema is None:
            schema = CONFIG_SCHEMA
        
        errors = []
        
        for key, field_schema in schema.items():
            full_path = f"{parent_path}.{key}" if parent_path else key
            
            if field_schema.required and key not in config:
                line, col = self.get_line_col(config)
                
                errors.append(ValidationError(
                    file=file_path,
                    line=line,
                    column=col,
                    message=f"Missing required key: '{full_path}'",
                    key_path=full_path,
                    expected=f"Required field '{key}'",
                    example=field_schema.example or f"{key}: <value>",
                ))
        
        return errors
    
    def validate_enum_values(
        self,
        config: CommentedMap,
        file_path: str,
        schema: Dict[str, FieldSchema] = None,
        parent_path: str = "",
    ) -> List[ValidationError]:
        """
        Validate enum field values.
        
        Args:
            config: Parsed YAML content
            file_path: Path to file (for error messages)
            schema: Schema to validate against
            parent_path: Parent path for nested validation
            
        Returns:
            List of validation errors
        """
        if schema is None:
            schema = CONFIG_SCHEMA
        
        errors = []
        
        for key, field_schema in schema.items():
            full_path = f"{parent_path}.{key}" if parent_path else key
            
            if key not in config:
                continue
            
            value = config[key]
            line, col = self.get_line_col(config, key)
            
            # Check enum values
            if field_schema.enum and isinstance(value, str):
                if value not in field_schema.enum:
                    suggested = suggest_similar_key(value, set(field_schema.enum))
                    suggestion = f"Did you mean '{suggested}'?" if suggested else None
                    
                    errors.append(ValidationError(
                        file=file_path,
                        line=line,
                        column=col,
                        message=f"Invalid value for '{full_path}'",
                        key_path=full_path,
                        expected=f"one of {list(field_schema.enum)}",
                        found=f'"{value}"',
                        suggestion=suggestion,
                        example=field_schema.example,
                    ))
            
            # Recurse into nested objects
            if field_schema.fields and isinstance(value, (CommentedMap, dict)):
                errors.extend(self.validate_enum_values(
                    value, file_path, field_schema.fields, full_path
                ))
        
        return errors
    
    def validate_unknown_keys(
        self,
        config: CommentedMap,
        file_path: str,
        schema: Dict[str, FieldSchema] = None,
        parent_path: str = "",
    ) -> List[ValidationError]:
        """
        Detect unknown keys at any level.
        
        Args:
            config: Parsed YAML content
            file_path: Path to file (for error messages)
            schema: Schema to validate against
            parent_path: Parent path for nested validation
            
        Returns:
            List of validation errors for unknown keys
        """
        if schema is None:
            schema = CONFIG_SCHEMA
        
        errors = []
        known_keys = set(schema.keys())
        
        for key in config.keys():
            full_path = f"{parent_path}.{key}" if parent_path else key
            line, col = self.get_line_col(config, key)
            
            if key not in known_keys:
                # Try to suggest a similar key
                suggested = suggest_similar_key(full_path)
                if not suggested:
                    # Try just the key name at this level
                    suggested = suggest_similar_key(key, known_keys)
                
                suggestion = f"Did you mean '{suggested}'?" if suggested else None
                
                errors.append(ValidationError(
                    file=file_path,
                    line=line,
                    column=col,
                    message=f"Unknown key: '{full_path}'",
                    key_path=full_path,
                    suggestion=suggestion,
                ))
            else:
                # Recurse into nested objects
                field_schema = schema.get(key)
                value = config[key]
                
                if field_schema and field_schema.fields and isinstance(value, (CommentedMap, dict)):
                    errors.extend(self.validate_unknown_keys(
                        value, file_path, field_schema.fields, full_path
                    ))
        
        return errors
    
    def validate_auth_security(
        self,
        config: CommentedMap,
        file_path: str,
    ) -> List[ValidationError]:
        """
        Validate auth section security.
        
        - Only env var references allowed (keys ending with _env)
        - No plain-text credentials
        - Referenced env vars must be set
        
        Args:
            config: Parsed YAML content
            file_path: Path to file
            
        Returns:
            List of validation errors
        """
        errors = []
        
        # Check for plain-text credentials anywhere
        errors.extend(self._check_plaintext_credentials(config, file_path))
        
        # Validate auth section env var references
        if "auth" in config and isinstance(config["auth"], (CommentedMap, dict)):
            auth = config["auth"]
            
            for key, value in auth.items():
                line, col = self.get_line_col(auth, key)
                
                # Only _env keys allowed in auth
                if not key.endswith("_env"):
                    errors.append(ValidationError(
                        file=file_path,
                        line=line,
                        column=col,
                        message=f"Auth key '{key}' must end with '_env' to reference environment variable",
                        key_path=f"auth.{key}",
                        suggestion=f"Use '{key}_env' with an environment variable name as value",
                        example=f"{key}_env: MY_ENV_VAR_NAME",
                    ))
                elif isinstance(value, str):
                    # Check if env var is set
                    if not os.environ.get(value):
                        errors.append(ValidationError(
                            file=file_path,
                            line=line,
                            column=col,
                            message=f"Environment variable '{value}' is not set",
                            key_path=f"auth.{key}",
                            expected=f"Environment variable '{value}' to be set",
                            found="Variable not found in environment",
                        ))
        
        return errors
    
    def _check_plaintext_credentials(
        self,
        config: Union[CommentedMap, dict],
        file_path: str,
        parent_path: str = "",
    ) -> List[ValidationError]:
        """Recursively check for plain-text credential values."""
        errors = []
        
        for key, value in config.items():
            full_path = f"{parent_path}.{key}" if parent_path else key
            key_lower = key.lower()
            
            # Skip _env keys - they're supposed to have env var names
            if key.endswith("_env"):
                continue
            
            # Check if key looks like a credential field
            for pattern in SENSITIVE_PATTERNS:
                if pattern in key_lower:
                    if isinstance(value, str) and not value.startswith("${"):
                        line, col = self.get_line_col(config, key)
                        
                        errors.append(ValidationError(
                            file=file_path,
                            line=line,
                            column=col,
                            message=f"Plain-text credential detected: '{full_path}'",
                            key_path=full_path,
                            suggestion="Use environment variable reference with ${VAR} or use auth section with _env suffix",
                            example=f"# Option 1: Use env var interpolation\n{key}: ${{MY_SECRET_VAR}}\n\n# Option 2: Use auth section\nauth:\n  {key}_env: MY_SECRET_VAR",
                        ))
                    break
            
            # Recurse into nested objects
            if isinstance(value, (CommentedMap, dict)):
                errors.extend(self._check_plaintext_credentials(value, file_path, full_path))
        
        return errors
    
    def validate(
        self,
        config: CommentedMap,
        file_path: str,
        check_env_vars: bool = True,
    ) -> Tuple[List[ValidationError], List[ValidationWarning]]:
        """
        Perform full validation on a configuration.
        
        Args:
            config: Parsed YAML content
            file_path: Path to file for error messages
            check_env_vars: Whether to check if env vars are set
            
        Returns:
            Tuple of (errors, warnings)
        """
        errors = []
        warnings = []
        
        # 1. Required fields
        errors.extend(self.validate_required_fields(config, file_path))
        
        # 2. Enum values
        errors.extend(self.validate_enum_values(config, file_path))
        
        # 3. Unknown keys
        errors.extend(self.validate_unknown_keys(config, file_path))
        
        # 4. Auth security
        if check_env_vars:
            errors.extend(self.validate_auth_security(config, file_path))
        
        # 5. Check for deprecated keys
        warnings.extend(self._check_deprecated_keys(config, file_path))
        
        return errors, warnings
    
    def _check_deprecated_keys(
        self,
        config: CommentedMap,
        file_path: str,
        schema: Dict[str, FieldSchema] = None,
        parent_path: str = "",
    ) -> List[ValidationWarning]:
        """Check for deprecated keys (warnings only)."""
        if schema is None:
            schema = CONFIG_SCHEMA
        
        warnings = []
        
        for key, field_schema in schema.items():
            if key in config and field_schema.deprecated:
                full_path = f"{parent_path}.{key}" if parent_path else key
                line, col = self.get_line_col(config, key)
                
                warnings.append(ValidationWarning(
                    file=file_path,
                    line=line,
                    column=col,
                    message=f"Deprecated key: '{full_path}'. {field_schema.deprecated_message}",
                    key_path=full_path,
                ))
        
        return warnings


def validate_yaml_file(
    path: Path,
    check_env_vars: bool = True,
) -> Tuple[Optional[CommentedMap], List[ValidationError], List[ValidationWarning]]:
    """
    Validate a YAML configuration file.
    
    Args:
        path: Path to YAML file
        check_env_vars: Whether to verify env vars are set
        
    Returns:
        Tuple of (parsed config if valid, errors, warnings)
    """
    validator = YAMLValidator()
    
    # Load with syntax checking
    config, syntax_errors = validator.load_yaml_with_positions(path)
    
    if syntax_errors:
        return None, syntax_errors, []
    
    # Full validation
    errors, warnings = validator.validate(config, str(path), check_env_vars)
    
    if errors:
        return None, errors, warnings
    
    return config, [], warnings


def format_validation_errors(
    errors: List[ValidationError],
    warnings: List[ValidationWarning] = None,
    show_examples: bool = True,
) -> str:
    """
    Format validation errors and warnings for display.
    
    Args:
        errors: List of validation errors
        warnings: List of validation warnings
        show_examples: Whether to show example correct usage
        
    Returns:
        Formatted string for display
    """
    parts = []
    
    if warnings:
        for warning in warnings:
            parts.append(warning.format())
        parts.append("")
    
    if errors:
        for i, error in enumerate(errors):
            if i > 0:
                parts.append("")
            parts.append(error.format(show_example=show_examples))
    
    return "\n".join(parts)
