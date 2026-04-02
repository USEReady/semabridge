"""
CLI Execution Configuration.

Defines the YAML configuration schema for CLI execution.
Authentication is NEVER inline - always resolved from environment variables.
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Literal, Optional, Set

import yaml
from pathlib import Path
from pydantic import BaseModel, Field, model_validator

from semabridge.core.behavior import ConnectorBehavior


class LoggingConfig(BaseModel):
    """
    Project-level logging configuration.
    
    Allows YAML-based control of logging behavior per project.
    Can be overridden by CLI arguments (--log-level takes precedence).
    """
    
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level"
    )
    format: Literal["text", "json"] = Field(
        default="text",
        description="Log output format (text for human-readable, json for structured)"
    )


class SourceConfig(BaseModel):
    """Source connector configuration."""
    
    type: Literal["fabric", "snowflake", "pbix"] = Field(
        ..., 
        description="Source connector type"
    )
    dataset_id: Optional[str] = Field(
        default=None,
        description="Fabric dataset ID (required for fabric source)"
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Fabric workspace ID (optional, uses env var if not set)"
    )
    database: Optional[str] = Field(
        default=None,
        description="Snowflake database (optional, uses env var if not set)"
    )
    schema_name: Optional[str] = Field(
        default=None,
        description="Snowflake schema (optional, uses env var if not set)"
    )
    pbix_path: Optional[str] = Field(
        default=None,
        description="Path to a local .pbix file",
    )
    source_path: Optional[str] = Field(
        default=None,
        description="Alias for pbix_path used by sync-oriented configs",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Alias for pbix_path used by local file configs",
    )
    
    @model_validator(mode='after')
    def validate_source_requirements(self) -> "SourceConfig":
        """Validate source-specific requirements."""
        if self.type == "fabric" and not self.dataset_id:
            raise ValueError("dataset_id is required for fabric source")
        if self.type == "pbix":
            resolved_path = self.pbix_path or self.source_path or self.file_path
            if not resolved_path:
                raise ValueError("pbix source requires one of: pbix_path, source_path, file_path")
            cleaned = str(resolved_path).strip()
            if len(cleaned) >= 2 and (
                (cleaned[0] == '"' and cleaned[-1] == '"')
                or (cleaned[0] == "'" and cleaned[-1] == "'")
            ):
                cleaned = cleaned[1:-1].strip()
            self.pbix_path = cleaned
            self.source_path = cleaned
            self.file_path = cleaned
        return self


class TargetConfig(BaseModel):
    """Target connector configuration (optional)."""
    
    type: Literal["fabric", "snowflake"] = Field(
        ...,
        description="Target connector type"
    )
    deploy: bool = Field(
        default=False,
        description="Whether to deploy to target"
    )
    workspace_id: Optional[str] = Field(
        default=None,
        description="Fabric workspace ID for target (if different from source)"
    )
    database: Optional[str] = Field(
        default=None,
        description="Snowflake database for target"
    )
    schema_name: Optional[str] = Field(
        default=None,
        description="Snowflake schema for target"
    )


class ExecutionConfig(BaseModel):
    """
    CLI Execution Configuration.
    
    Loaded from YAML, validated at Step 1.
    Each configuration corresponds to exactly one Project and one Run.
    """
    
    source: SourceConfig = Field(
        ...,
        description="Source connector configuration"
    )
    target: Optional[TargetConfig] = Field(
        default=None,
        description="Target connector configuration (optional)"
    )
    model_name: str = Field(
        ...,
        description="Name of the semantic model"
    )
    version_tag: Optional[str] = Field(
        default=None,
        description="Version tag for this run"
    )
    policy_path: Optional[str] = Field(
        default=None,
        description="Path to behavior policy YAML file"
    )
    sync_direction: Optional[Literal["source_to_target", "target_to_source"]] = Field(
        default=None,
        description="Direction of synchronization. If not set, inferred from source/target types."
    )
    logging: Optional[LoggingConfig] = Field(
        default=None,
        description="Project-level logging configuration"
    )
    
    # Internal: Parsed behavior object
    _behavior: Optional[ConnectorBehavior] = None

    @property
    def behavior(self) -> ConnectorBehavior:
        """Get the behavioral configuration (loaded or default)."""
        if self._behavior is None:
            if self.policy_path:
                self._behavior = ConnectorBehavior.from_yaml(Path(self.policy_path))
            else:
                self._behavior = ConnectorBehavior()
        return self._behavior
    
    # Supported connector types (ClassVar to avoid Pydantic field detection)
    SUPPORTED_SOURCE_TYPES: ClassVar[Set[str]] = {"fabric", "snowflake", "pbix"}
    SUPPORTED_TARGET_TYPES: ClassVar[Set[str]] = {"fabric", "snowflake"}
    
    @model_validator(mode='after')
    def validate_no_inline_secrets(self) -> "ExecutionConfig":
        """
        Reject any inline secrets in configuration.
        
        Authentication credentials must ALWAYS come from environment variables,
        never from the configuration file.
        """
        # Check for common secret field patterns in the raw config
        forbidden_patterns = [
            "password", "secret", "credential", "token", "api_key",
            "client_secret", "private_key"
        ]
        
        def check_dict_for_secrets(d: Dict[str, Any], path: str = "") -> None:
            for key, value in d.items():
                current_path = f"{path}.{key}" if path else key
                key_lower = key.lower()
                
                # Check if key looks like a secret
                for pattern in forbidden_patterns:
                    if pattern in key_lower:
                        raise ValueError(
                            f"Inline secret detected at '{current_path}'. "
                            f"Authentication credentials must be set via environment variables, "
                            f"not in configuration files."
                        )
                
                # Recurse into nested dicts
                if isinstance(value, dict):
                    check_dict_for_secrets(value, current_path)
        
        # Note: This validation runs on the Pydantic model, not raw YAML
        # Raw YAML validation happens in from_yaml()
        return self
    
    @model_validator(mode='after')
    def validate_connector_types(self) -> "ExecutionConfig":
        """Validate that connector types are supported."""
        if self.source.type not in self.SUPPORTED_SOURCE_TYPES:
            raise ValueError(
                f"Unsupported source connector type: '{self.source.type}'. "
                f"Supported types: {self.SUPPORTED_SOURCE_TYPES}"
            )
        
        if self.target and self.target.type not in self.SUPPORTED_TARGET_TYPES:
            raise ValueError(
                f"Unsupported target connector type: '{self.target.type}'. "
                f"Supported types: {self.SUPPORTED_TARGET_TYPES}"
            )
        
        return self
    
    @classmethod
    def from_yaml(cls, path: Path) -> "ExecutionConfig":
        """
        Load configuration from a YAML file.
        
        Performs additional validation for inline secrets in raw YAML
        before Pydantic validation. Allows _env suffix fields for
        environment variable references.
        """
        if not path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        
        with open(path, "r", encoding="utf-8") as f:
            raw_config = yaml.safe_load(f)
        
        if not isinstance(raw_config, dict):
            raise ValueError(f"Configuration file must contain a YAML object, got: {type(raw_config)}")
        
        # Check for inline secrets in raw YAML before Pydantic parsing
        # Exception: fields ending with _env are allowed as they reference env vars
        forbidden_patterns = [
            "password", "secret", "credential", "token", "api_key",
            "client_secret", "private_key"
        ]
        
        def check_raw_for_secrets(d: Dict[str, Any], path: str = "") -> None:
            for key, value in d.items():
                current_path = f"{path}.{key}" if path else key
                key_lower = key.lower()
                
                # Allow _env suffix fields - they reference env vars, not inline secrets
                if key_lower.endswith("_env"):
                    continue
                
                for pattern in forbidden_patterns:
                    if pattern in key_lower:
                        raise ValueError(
                            f"Inline secret detected in YAML at '{current_path}'. "
                            f"Authentication credentials must be set via environment variables. "
                            f"Use '{key}_env: ENV_VAR_NAME' to reference environment variables."
                        )
                
                if isinstance(value, dict):
                    check_raw_for_secrets(value, current_path)
        
        check_raw_for_secrets(raw_config)
        
        # Validate required keys
        required_keys = ["source", "model_name"]
        missing_keys = [k for k in required_keys if k not in raw_config]
        if missing_keys:
            raise ValueError(
                f"Missing required configuration keys: {missing_keys}"
            )
        
        return cls.model_validate(raw_config)
    
    @classmethod
    def from_yaml_files(cls, paths: List[Path]) -> "ExecutionConfig":
        """
        Load and merge configuration from multiple YAML files.
        
        Files are processed in order; later files override earlier ones.
        Environment variables are interpolated using ${VAR} syntax.
        
        Args:
            paths: List of paths to YAML files
            
        Returns:
            ExecutionConfig with merged and validated configuration
            
        Raises:
            FileNotFoundError: If any config file is not found
            ValueError: If configuration is invalid
        """
        from semabridge.core.config_loader import (
            load_and_validate_configs,
            ConfigLoadError,
            MissingEnvVarError,
            ValidationErrors,
        )
        
        try:
            # Load, validate (with line numbers), and merge configs
            merged_config, _ = load_and_validate_configs(paths, check_env_vars=False)
            
            return cls.model_validate(merged_config)
            
        except ValidationErrors:
            # Re-raise validation errors to preserve line-number info
            raise
        except MissingEnvVarError as e:
            raise ValueError(str(e)) from e
        except ConfigLoadError as e:
            raise ValueError(str(e)) from e
    
    def to_yaml(self, path: Path) -> None:
        """Save configuration to a YAML file."""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(exclude_none=True), f, default_flow_style=False)

