"""
Centralized Configuration Loader.

Provides:
- Environment variable interpolation (${ENV_VAR} syntax)
- Multiple YAML file merging (later files override earlier)
- Fail-fast for missing environment variables
- Sensitive value protection (never logs credentials)
- Strict validation with line-number error reporting
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

import yaml

from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    from semabridge.formats.yaml_validator import ValidationError

logger = get_logger(__name__)

# Pattern to match ${ENV_VAR} or ${ENV_VAR:-default}
ENV_VAR_PATTERN = re.compile(r'\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}')

# Fields that should have their values masked in logs
SENSITIVE_FIELD_PATTERNS = [
    "password", "secret", "credential", "token", "api_key",
    "client_secret", "private_key", "auth"
]


class ConfigLoadError(Exception):
    """Raised when configuration loading fails."""
    pass


class ValidationErrors(ConfigLoadError):
    """
    Raised when YAML validation fails with one or more errors.
    
    Contains detailed error information including file, line, and column.
    """
    def __init__(self, errors: List["ValidationError"], warnings: List = None):
        self.errors = errors
        self.warnings = warnings or []
        
        # Build message from errors
        from semabridge.formats.yaml_validator import format_validation_errors
        self.formatted_message = format_validation_errors(errors, warnings)
        
        super().__init__(self.formatted_message)


class MissingEnvVarError(ConfigLoadError):
    """Raised when a required environment variable is missing."""
    def __init__(self, var_name: str, yaml_path: str = ""):
        self.var_name = var_name
        self.yaml_path = yaml_path
        context = f" in '{yaml_path}'" if yaml_path else ""
        super().__init__(
            f"Missing required environment variable: {var_name}{context}. "
            f"Please set this variable in your .env file or environment."
        )


def interpolate_env_vars(
    value: str,
    yaml_path: str = "",
    fail_on_missing: bool = True,
) -> str:
    """
    Replace ${ENV_VAR} patterns with environment variable values.
    
    Supports:
    - ${VAR} - Required variable, fails if not set
    - ${VAR:-default} - Variable with default value
    
    Args:
        value: String containing ${ENV_VAR} patterns
        yaml_path: Path within YAML for error messages
        fail_on_missing: If True, raise error for missing vars without defaults
        
    Returns:
        String with environment variables interpolated
        
    Raises:
        MissingEnvVarError: If required variable is missing and fail_on_missing is True
    """
    def replace_match(match: re.Match) -> str:
        var_name = match.group(1)
        default_value = match.group(2)
        
        env_value = os.environ.get(var_name)
        
        if env_value is not None:
            return env_value
        elif default_value is not None:
            return default_value
        elif fail_on_missing:
            raise MissingEnvVarError(var_name, yaml_path)
        else:
            return match.group(0)  # Return original pattern
    
    return ENV_VAR_PATTERN.sub(replace_match, value)


def resolve_env_ref_fields(
    data: Dict[str, Any],
    path: str = "",
) -> Dict[str, Any]:
    """
    Resolve fields ending with '_env' that reference environment variables.
    
    For example:
        password_env: SNOWFLAKE_PASSWORD
    Becomes:
        password: <value of SNOWFLAKE_PASSWORD>
        
    Args:
        data: Dictionary to process
        path: Current path in YAML for error messages
        
    Returns:
        Dictionary with _env fields resolved
    """
    result = {}
    
    for key, value in data.items():
        current_path = f"{path}.{key}" if path else key
        
        # Handle _env suffix pattern (e.g., password_env: VAR_NAME)
        if key.endswith("_env") and isinstance(value, str):
            # Get the actual field name (remove _env suffix)
            actual_key = key[:-4]  # Remove "_env"
            
            # Get the environment variable
            env_value = os.environ.get(value)
            if env_value is None:
                raise MissingEnvVarError(value, current_path)
            
            result[actual_key] = env_value
            logger.debug(f"Resolved {current_path} from environment variable {value}")
        elif isinstance(value, dict):
            result[key] = resolve_env_ref_fields(value, current_path)
        elif isinstance(value, list):
            result[key] = [
                resolve_env_ref_fields(item, f"{current_path}[{i}]") 
                if isinstance(item, dict) else item
                for i, item in enumerate(value)
            ]
        elif isinstance(value, str):
            # Interpolate ${VAR} patterns in string values
            result[key] = interpolate_env_vars(value, current_path)
        else:
            result[key] = value
    
    return result


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries, with override values taking precedence.
    
    - Nested dicts are merged recursively
    - Lists in override replace lists in base (not appended)
    - Scalar values in override replace base values
    
    Args:
        base: Base dictionary
        override: Override dictionary (values take precedence)
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            result[key] = deep_merge(result[key], value)
        else:
            # Override value (including lists and scalars)
            result[key] = value
    
    return result


def mask_sensitive_value(key: str, value: Any) -> Any:
    """
    Mask sensitive values for safe logging.
    
    Args:
        key: Field name
        value: Field value
        
    Returns:
        Masked value if sensitive, original value otherwise
    """
    key_lower = key.lower()
    for pattern in SENSITIVE_FIELD_PATTERNS:
        if pattern in key_lower:
            if isinstance(value, str) and len(value) > 0:
                return "********"
            elif isinstance(value, dict):
                return {k: mask_sensitive_value(k, v) for k, v in value.items()}
            return value
    
    if isinstance(value, dict):
        return {k: mask_sensitive_value(k, v) for k, v in value.items()}
    elif isinstance(value, list):
        return [mask_sensitive_value(key, item) for item in value]
    
    return value


def load_yaml_file(path: Path) -> Dict[str, Any]:
    """
    Load a single YAML file.
    
    Args:
        path: Path to YAML file
        
    Returns:
        Parsed YAML content as dictionary
        
    Raises:
        ConfigLoadError: If file doesn't exist or is invalid
    """
    if not path.exists():
        raise ConfigLoadError(f"Configuration file not found: {path}")
    
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigLoadError(f"Invalid YAML in {path}: {e}")
    
    if content is None:
        return {}
    
    if not isinstance(content, dict):
        raise ConfigLoadError(
            f"Configuration file must contain a YAML object, got: {type(content).__name__}"
        )
    
    return content


def load_and_merge_configs(
    paths: List[Path],
    resolve_env: bool = True,
) -> Tuple[Dict[str, Any], List[Path]]:
    """
    Load and merge multiple YAML configuration files.
    
    Files are processed in order; later files override earlier ones.
    Environment variables are interpolated after merging.
    
    Args:
        paths: List of paths to YAML files
        resolve_env: Whether to resolve environment variables
        
    Returns:
        Tuple of (merged config dict, list of loaded file paths)
        
    Raises:
        ConfigLoadError: If any file fails to load
        MissingEnvVarError: If required environment variable is missing
    """
    if not paths:
        raise ConfigLoadError("No configuration files provided")
    
    merged: Dict[str, Any] = {}
    loaded_paths: List[Path] = []
    
    for path in paths:
        logger.info(f"Loading configuration: {path}")
        config = load_yaml_file(path)
        merged = deep_merge(merged, config)
        loaded_paths.append(path)
    
    if resolve_env:
        merged = resolve_env_ref_fields(merged)
    
    # Log merged config with sensitive values masked
    masked_config = mask_sensitive_value("root", merged)
    logger.debug(f"Merged configuration: {masked_config}")
    
    return merged, loaded_paths


def get_default_config_path() -> Optional[Path]:
    """
    Get the default configuration file path.
    
    Looks for:
    1. ./semabridge.yaml
    2. ./semabridge.yml
    3. ./config/semabridge.yaml
    
    Returns:
        Path to default config file if found, None otherwise
    """
    candidates = [
        Path("config/semabridge.yaml"),
        Path("config/semabridge.yml"),
        Path("semabridge.yaml"),
        Path("semabridge.yml"),
    ]
    
    for candidate in candidates:
        if candidate.exists():
            return candidate
    
    return None


def get_project_file_path(filename: str) -> Path:
    """Resolve project-scoped files with config-first compatibility.

    Search order:
    1. ./config/<filename>
    2. ./<filename>
    3. ./config/<filename> (default target path when file doesn't exist yet)
    """
    config_candidate = Path("config") / filename
    root_candidate = Path(filename)

    if config_candidate.exists():
        return config_candidate
    if root_candidate.exists():
        return root_candidate
    return config_candidate


def validate_config_schema(config: Dict[str, Any]) -> List[str]:
    """
    Validate configuration against expected schema.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        List of validation error messages (empty if valid)
    """
    errors = []
    
    # Required top-level keys
    if "source" not in config:
        errors.append("Missing required key: 'source'")
    else:
        source = config["source"]
        if not isinstance(source, dict):
            errors.append("'source' must be an object")
        elif "type" not in source:
            errors.append("Missing required key: 'source.type'")
        elif source["type"] not in ("snowflake", "fabric", "pbix"):
            errors.append(f"Invalid source.type: '{source['type']}'. Must be 'snowflake', 'fabric' or 'pbix'")
        elif source["type"] == "pbix":
            pbix_path = source.get("pbix_path") or source.get("source_path") or source.get("file_path")
            if not pbix_path:
                errors.append("PBIX source requires one of: source.pbix_path, source.source_path, source.file_path")
    
    if "model_name" not in config:
        errors.append("Missing required key: 'model_name'")
    
    # Validate sync_direction if present
    if "sync_direction" in config:
        valid_directions = ("source_to_target", "target_to_source")
        if config["sync_direction"] not in valid_directions:
            errors.append(
                f"Invalid sync_direction: '{config['sync_direction']}'. "
                f"Must be one of: {valid_directions}"
            )
    
    # Validate logging section if present
    if "logging" in config:
        logging_config = config["logging"]
        if not isinstance(logging_config, dict):
            errors.append("'logging' must be an object")
        else:
            if "level" in logging_config:
                valid_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
                level = logging_config["level"].upper() if isinstance(logging_config["level"], str) else logging_config["level"]
                if level not in valid_levels:
                    errors.append(
                        f"Invalid logging.level: '{logging_config['level']}'. "
                        f"Must be one of: {valid_levels}"
                    )
            
            if "format" in logging_config:
                valid_formats = ("text", "json")
                if logging_config["format"] not in valid_formats:
                    errors.append(
                        f"Invalid logging.format: '{logging_config['format']}'. "
                        f"Must be one of: {valid_formats}"
                    )
    
    # Handle 'targets' list from UI (convert first item to 'target')
    if "targets" in config and isinstance(config.get("targets"), list) and len(config["targets"]) > 0:
        config["target"] = config["targets"][0]
        # Auto-enable deploy so Snowflake DDL is executed
        if "deploy" not in config["target"]:
            config["target"]["deploy"] = True

    # Validate target if present
    if "target" in config and config["target"] is not None:
        target = config["target"]
        if not isinstance(target, dict):
            errors.append("'target' must be an object")
        elif "type" not in target:
            errors.append("Missing required key: 'target.type'")
        elif target["type"] not in ("snowflake", "fabric"):
            errors.append(f"Invalid target.type: '{target['type']}'. Must be 'snowflake' or 'fabric'")
    
    return errors


def load_and_validate_configs(
    paths: List[Path],
    strict: bool = True,
    check_env_vars: bool = True,
) -> Tuple[Dict[str, Any], List[Path]]:
    """
    Load, merge, and validate multiple YAML configuration files.
    
    Uses ruamel.yaml for line-number preserving parsing.
    Validates against strict schema with detailed error messages.
    
    Args:
        paths: List of paths to YAML files
        strict: If True, fail on unknown keys (default)
        check_env_vars: Whether to verify env vars are set
        
    Returns:
        Tuple of (merged config dict, list of loaded file paths)
        
    Raises:
        ValidationErrors: If validation fails with detailed errors
        ConfigLoadError: If file loading fails
    """
    from semabridge.formats.yaml_validator import (
        YAMLValidator,
        ValidationError,
        ValidationWarning,
    )
    
    if not paths:
        raise ConfigLoadError("No configuration files provided")
    
    validator = YAMLValidator()
    all_errors: List[ValidationError] = []
    all_warnings: List[ValidationWarning] = []
    configs: List[Dict[str, Any]] = []
    loaded_paths: List[Path] = []
    
    # Phase 1: Load and validate each file independently
    for path in paths:
        logger.info(f"Loading configuration: {path}")
        
        # Load with syntax validation
        config, syntax_errors = validator.load_yaml_with_positions(path)
        
        if syntax_errors:
            all_errors.extend(syntax_errors)
            continue
        
        # Schema validation per file
        errors, warnings = validator.validate(
            config, 
            str(path), 
            check_env_vars=check_env_vars,
        )
        
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        
        # Convert CommentedMap to regular dict for merging
        configs.append(dict(config))
        loaded_paths.append(path)
    
    # If there are errors, fail fast
    if all_errors:
        raise ValidationErrors(all_errors, all_warnings)
    
    # Log warnings (but don't fail)
    for warning in all_warnings:
        logger.warning(warning.format())
    
    # Phase 2: Merge configurations
    merged: Dict[str, Any] = {}
    for config in configs:
        merged = deep_merge(merged, config)
    
    # Phase 3: Resolve environment variables
    merged = resolve_env_ref_fields(merged)
    
    # Log merged config with sensitive values masked
    masked_config = mask_sensitive_value("root", merged)
    logger.debug(f"Merged configuration: {masked_config}")
    
    return merged, loaded_paths

