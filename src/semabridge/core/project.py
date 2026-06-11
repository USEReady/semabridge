"""
SemaBridge Project Configuration Models.

Defines Pydantic models for the "One Source, Many Targets" broadcasting framework.
Supports:
- Wildcards for model discovery (*, prefix*, *suffix)
- Multiple target definitions
- Model-level and granular semantic object exclusions
"""

from __future__ import annotations

import fnmatch
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class CoreConfig(BaseModel):
    """
    Core application settings.
    
    Attributes:
        repository_path: Path to the DuckDB repository
        default_intermediary: Default intermediate format (osi/sml)
    """
    repository_path: str = Field(default="~/.semabridge/semabridge.db")
    default_intermediary: str = "osi"


class LoggingConfig(BaseModel):
    """
    Logging configuration.
    
    Attributes:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format: Log format (text, json)
        dir: Directory for log files
        redact_secrets: Whether to redact secrets
    """
    level: str = "INFO"
    format: str = "text"
    dir: Optional[str] = None
    redact_secrets: bool = True


class SourceType(str, Enum):
    """Supported source connector types."""
    FABRIC = "fabric"
    SNOWFLAKE = "snowflake"


class TargetType(str, Enum):
    """Supported target connector types."""
    SNOWFLAKE_SEMANTIC_VIEW = "snowflake_semantic_view"
    TABLEAU_DATASOURCE = "tableau_datasource"
    FABRIC = "fabric"
    SNOWFLAKE = "snowflake"  # Legacy support


class SourceConfig(BaseModel):
    """
    Source connector configuration with wildcard support.
    
    Attributes:
        type: Source type (fabric, snowflake)
        workspace: Fabric workspace name (for fabric type)
        workspace_id: Fabric workspace GUID (for fabric type)
        dataset_id: Specific dataset/model ID (optional)
        model: Model name pattern with glob support (*, Sale*, *_Production)
        models: Explicit list of model names to include (overrides 'model' pattern)
        database: Snowflake database (for snowflake type)
        schema_name: Snowflake schema (for snowflake type)
    """
    type: SourceType
    workspace: Optional[str] = None
    workspace_id: Optional[str] = None
    dataset_id: Optional[str] = None
    model: str = Field(default="*", description="Model name pattern (glob syntax)")
    models: Optional[List[str]] = Field(default=None, description="Explicit list of model names")
    database: Optional[str] = None
    schema_name: Optional[str] = Field(default=None, alias="schema")
    
    class Config:
        populate_by_name = True
    
    @model_validator(mode='after')
    def validate_source_config(self) -> 'SourceConfig':
        """Ensure required fields are present based on source type."""
        if self.type == SourceType.FABRIC:
            if not self.workspace_id and not self.workspace:
                raise ValueError("Fabric source requires 'workspace' or 'workspace_id'")
        elif self.type == SourceType.SNOWFLAKE:
            if not self.database:
                raise ValueError("Snowflake source requires 'database'")
        return self
    
    def matches_model(self, model_name: str) -> bool:
        """
        Check if a model name matches the configured pattern or list.
        
        Uses explicit list if present, otherwise case-insensitive glob matching.
        """
        if self.models:
            return model_name in self.models
        return fnmatch.fnmatch(model_name.lower(), self.model.lower())


class TargetConfig(BaseModel):
    """
    Target connector configuration.
    
    Attributes:
        type: Target type
        database: Target database name
        schema_name: Target schema name  
        project: Target project (for Tableau)
        deploy: Whether to execute deployment
    """
    type: TargetType
    database: Optional[str] = None
    schema_name: Optional[str] = Field(default=None, alias="schema")
    project: Optional[str] = None
    deploy: bool = True
    
    class Config:
        populate_by_name = True


class ExclusionType(str, Enum):
    """Types of granular semantic object exclusions."""
    TABLE = "table"
    COLUMN = "column"
    MEASURE = "measure"


class SemanticExclusion(BaseModel):
    """
    Represents a single semantic object exclusion rule.
    
    Format: "type:pattern" (e.g., "table:Sys_Log_*", "column:PII_SSN")
    """
    exclusion_type: ExclusionType
    pattern: str
    
    @classmethod
    def parse(cls, rule: str) -> 'SemanticExclusion':
        """Parse a string rule like 'table:Sys_Log_*' into a SemanticExclusion."""
        if ':' not in rule:
            raise ValueError(f"Invalid exclusion format: '{rule}'. Expected 'type:pattern'")
        
        type_str, pattern = rule.split(':', 1)
        type_str = type_str.strip().lower()
        
        try:
            exclusion_type = ExclusionType(type_str)
        except ValueError:
            valid_types = [e.value for e in ExclusionType]
            raise ValueError(
                f"Invalid exclusion type: '{type_str}'. Must be one of: {valid_types}"
            )
        
        return cls(exclusion_type=exclusion_type, pattern=pattern.strip())
    
    def matches(self, object_name: str) -> bool:
        """Check if an object name matches this exclusion pattern (case-insensitive)."""
        return fnmatch.fnmatch(object_name.lower(), self.pattern.lower())


class ProjectOptions(BaseModel):
    """
    Project-level options for exclusions and processing.
    
    Attributes:
        exclude_model: List of model name patterns to exclude entirely
        exclude_semantics: List of granular exclusion rules (table:*, column:*, measure:*)
    """
    exclude_model: List[str] = Field(default_factory=list)
    exclude_semantics: List[str] = Field(default_factory=list)
    structural_dedup: bool = False
    skip_sml_conversion: bool = False
    
    def get_semantic_exclusions(self) -> List[SemanticExclusion]:
        """Parse exclude_semantics strings into SemanticExclusion objects."""
        return [SemanticExclusion.parse(rule) for rule in self.exclude_semantics]
    
    def is_model_excluded(self, model_name: str) -> bool:
        """
        Check if a model should be excluded.
        
        Uses case-insensitive glob matching against all exclude_model patterns.
        """
        model_lower = model_name.lower()
        return any(
            fnmatch.fnmatch(model_lower, pattern.lower()) 
            for pattern in self.exclude_model
        )
    
    def is_table_excluded(self, table_name: str) -> bool:
        """Check if a table should be excluded."""
        exclusions = self.get_semantic_exclusions()
        return any(
            exc.matches(table_name) 
            for exc in exclusions 
            if exc.exclusion_type == ExclusionType.TABLE
        )
    
    def is_column_excluded(self, column_name: str) -> bool:
        """Check if a column should be excluded (critical for PII protection)."""
        exclusions = self.get_semantic_exclusions()
        return any(
            exc.matches(column_name) 
            for exc in exclusions 
            if exc.exclusion_type == ExclusionType.COLUMN
        )
    
    def is_measure_excluded(self, measure_name: str) -> bool:
        """Check if a measure should be excluded."""
        exclusions = self.get_semantic_exclusions()
        return any(
            exc.matches(measure_name) 
            for exc in exclusions 
            if exc.exclusion_type == ExclusionType.MEASURE
        )


class ConcurrencyOptions(BaseModel):
    """
    Concurrency configuration for parallel model processing.

    Attributes:
        max_workers: Maximum number of parallel worker processes (0 = auto).
        execution_mode: 'best_effort' (continue on failure) or 'strict' (stop on first).
        max_retries: Maximum retry attempts for transient errors.
        base_delay: Base delay in seconds for exponential back-off.
        max_delay: Cap on back-off delay in seconds.
        memory_threshold_percent: Memory usage percentage that triggers worker reduction.
        disk_space_check: Whether to verify free disk space before starting.
        min_disk_space_mb: Minimum free disk space in MB.
        enable_parallel: Master switch for parallel processing.
    """
    max_workers: int = Field(default=0, ge=0)
    execution_mode: str = Field(default="best_effort")
    max_retries: int = Field(default=3, ge=0)
    base_delay: float = Field(default=1.0, gt=0)
    max_delay: float = Field(default=60.0, gt=0)
    memory_threshold_percent: float = Field(default=80.0, gt=0, le=100)
    disk_space_check: bool = True
    min_disk_space_mb: int = Field(default=1000, ge=0)
    enable_parallel: bool = True


class ProjectConfig(BaseModel):
    """
    Complete SemaBridge project configuration.
    
    Represents the full semabridge.yaml schema with support for:
    - One source with wildcard model selection
    - Multiple broadcast targets
    - Model-level and granular exclusions
    - Concurrency settings for parallel model processing
    
    Example YAML:
    ```yaml
    source:
      type: fabric
      workspace: "Sales_Workspace"
      model: "Sale*"
    
    targets:
      - type: snowflake_semantic_view
        database: "ANALYTICS_DB"
        schema: "SALES_L1"
      - type: tableau_datasource
        project: "Executive_Reporting"
    
    options:
      exclude_model:
        - "Sale_Archive_2020"
        - "Sale_Test_*"
      exclude_semantics:
        - "table:Sys_Log_*"
        - "column:PII_SSN"
        - "measure:Internal_Cost"
    
    concurrency:
      max_workers: 4
      execution_mode: best_effort
      max_retries: 3
    ```
    """
    source: SourceConfig
    targets: List[TargetConfig] = Field(default_factory=list)
    options: ProjectOptions = Field(default_factory=ProjectOptions)
    concurrency: ConcurrencyOptions = Field(default_factory=ConcurrencyOptions)
    
    # Legacy support single target field
    target: Optional[TargetConfig] = None
    
    # Additional fields from existing schema
    model_name: Optional[str] = None
    version_tag: Optional[str] = None
    logging: Optional[LoggingConfig] = None
    core: CoreConfig = Field(default_factory=CoreConfig)
    
    @model_validator(mode='after')
    def migrate_legacy_target(self) -> 'ProjectConfig':
        """Convert legacy single 'target' to 'targets' list."""
        if self.target is not None and not self.targets:
            self.targets = [self.target]
        return self
    
    @model_validator(mode='after')
    def default_model_name_and_dataset_id(self) -> 'ProjectConfig':
        """Apply smart defaults for model_name and dataset_id.
        
        - If model_name is not set, default to source.model (when not a wildcard).
        - If dataset_id is given but source.model is '*' or not set,
          default source.model to model_name or dataset_id.
        """
        # Default model_name from source.model
        if not self.model_name and self.source.model and self.source.model != '*':
            self.model_name = self.source.model
        
        # If dataset_id is given but model is still wildcard, use model_name
        if (
            self.source.dataset_id
            and self.source.model == '*'
            and self.model_name
        ):
            self.source.model = self.model_name
        
        return self
    
    def get_included_models(self, available_models: List[str]) -> List[str]:
        """
        Get the final list of models to process after applying inclusion/exclusion.
        
        Algorithm:
        1. Filter available_models by source.model pattern (inclusion)
        2. Remove any models matching options.exclude_model patterns (exclusion)
        
        Exclusion ALWAYS overrides inclusion.
        
        Args:
            available_models: List of all model names discovered from source
            
        Returns:
            Filtered list of model names to process
        """
        # Step 1: Include models matching the source pattern
        included = [
            model for model in available_models 
            if self.source.matches_model(model)
        ]
        
        # Step 2: Exclude models matching any exclusion pattern
        final = [
            model for model in included 
            if not self.options.is_model_excluded(model)
        ]
        
        return final
    
    @classmethod
    def from_yaml_dict(cls, data: Dict[str, Any]) -> 'ProjectConfig':
        """
        Create ProjectConfig from a parsed YAML dictionary.
        
        Handles both new multi-target format and legacy single-target format.
        """
        return cls.model_validate(data)


def load_project_config(path: Path) -> ProjectConfig:
    """
    Load and validate a project configuration from YAML file.
    
    Args:
        path: Path to semabridge.yaml
        
    Returns:
        Validated ProjectConfig instance
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        ValidationError: If config fails validation
    """
    import yaml
    
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    
    return ProjectConfig.from_yaml_dict(data or {})
