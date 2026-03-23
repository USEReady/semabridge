"""
OSI (Open Semantic Interchange) Intermediate Models.

This module defines the canonical intermediate representation for semantic models.
All conversions must pass through these OSI models:
    - Source → OSI (extraction)
    - OSI → Target (emission)

These models serve as the "Rosetta Stone" between platforms like:
    - Microsoft Fabric (Power BI / TMSL)
    - Snowflake (Semantic Views / Cortex Analyst)

Note:
    This module is SEPARATE from sml/models.py. SML models handle
    the SML-specific YAML format, while OSI models provide the
    vendor-neutral intermediate representation.
"""

import logging
import os
import re
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


logger = logging.getLogger("semabridge.intermediate.models")


def _get_int_env(name: str, default: int) -> int:
    """Read positive integer from environment with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value < 1:
            raise ValueError("must be >= 1")
        return value
    except Exception:
        logger.warning(
            "Invalid environment value for %s=%r; using default %s",
            name,
            raw,
            default,
        )
        return default


RECOMMENDED_DATASET_COLUMN_LIMIT = _get_int_env(
    "SEMABRIDGE_RECOMMENDED_DATASET_COLUMN_LIMIT", 75
)
RECOMMENDED_MODEL_TOTAL_COLUMN_LIMIT = _get_int_env(
    "SEMABRIDGE_RECOMMENDED_MODEL_TOTAL_COLUMN_LIMIT", 100
)


class OSIAggregationType(str, Enum):
    """OSI-standard aggregation types for metrics."""

    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    NONE = "none"


class OSIDataType(str, Enum):
    """OSI-standard normalized data types."""

    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    BINARY = "binary"
    VARIANT = "variant"
    UNKNOWN = "unknown"


class OSICardinality(str, Enum):
    """OSI-standard relationship cardinality types."""

    ONE_TO_ONE = "one-to-one"
    ONE_TO_MANY = "one-to-many"
    MANY_TO_ONE = "many-to-one"
    MANY_TO_MANY = "many-to-many"


class OSICrossFilterDirection(str, Enum):
    """OSI-standard cross-filter direction for relationships."""

    SINGLE = "single"
    BOTH = "both"


# =============================================================================
# Base Model with Common Validation
# =============================================================================


class OSIBaseModel(BaseModel):
    """
    Base model with common OSI validation and configuration.

    All OSI models inherit from this to ensure consistent
    validation behavior and serialization settings.
    """

    model_config = {
        "strict": True,
        "validate_assignment": True,
        "extra": "forbid",
    }


# =============================================================================
# Column / Field Definition
# =============================================================================


class OSIColumn(OSIBaseModel):
    """
    OSI Column (Field) definition.

    Represents a single column/attribute in a dataset,
    with normalized typing and metadata.

    Attributes:
        unique_name: Unique identifier within the dataset.
        label: Human-readable display name.
        data_type: Normalized OSI data type.
        description: Optional description of the column.
        is_key: Whether this is a primary/foreign key column.
        is_hidden: Whether column should be hidden from end users.
        format_string: Optional format pattern for display.
    """

    unique_name: str = Field(..., min_length=1, description="Unique column identifier")
    label: str = Field(default="", description="Display name")
    data_type: OSIDataType = Field(default=OSIDataType.STRING, description="Data type")
    description: Optional[str] = Field(default=None, description="Column description")
    is_key: bool = Field(default=False, description="Primary/foreign key indicator")
    is_hidden: bool = Field(default=False, description="Hidden from end users")
    format_string: Optional[str] = Field(default=None, description="Display format")
    source_expression: Optional[str] = Field(
        default=None, description="Source SQL expression if computed"
    )
    # Cortex Analyst advanced AI metadata
    synonyms: List[str] = Field(default_factory=list, description="Alternative names for NLP matching")
    is_enum: bool = Field(default=False, description="Exhaustive categorical values indicator")
    cortex_search_service: Optional[str] = Field(default=None, description="Linked Cortex Search Service name")
    sample_values: List[str] = Field(default_factory=list, description="Representative sample values")

    @field_validator("unique_name")
    @classmethod
    def validate_unique_name(cls, v: str) -> str:
        """Ensure column name is not empty or whitespace-only."""
        if not v or v.isspace():
            raise ValueError("Column unique_name cannot be empty")
        return v

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)


# =============================================================================
# Dataset / Entity Definition
# =============================================================================


class OSIDataset(OSIBaseModel):
    """
    OSI Dataset (Table/Entity) definition.

    Represents a physical or logical table from the source system,
    containing columns and metadata.

    Attributes:
        unique_name: Unique identifier for the dataset.
        label: Human-readable display name.
        description: Optional description.
        source_table: Physical table name in source system.
        source_schema: Schema containing the source table.
        columns: List of column definitions.
        is_fact: Whether this is a fact table (vs dimension).
        is_hidden: Whether dataset should be hidden from end users.
    """

    unique_name: str = Field(..., min_length=1, description="Unique dataset identifier")
    label: str = Field(default="", description="Display name")
    description: Optional[str] = Field(default=None, description="Dataset description")
    source_table: Optional[str] = Field(
        default=None, description="Physical table name"
    )
    source_schema: Optional[str] = Field(
        default=None, description="Schema containing the table"
    )
    columns: List[OSIColumn] = Field(
        default_factory=list, description="Column definitions"
    )
    is_fact: bool = Field(default=False, description="Fact table indicator")
    is_hidden: bool = Field(default=False, description="Hidden from end users")

    @field_validator("columns")
    @classmethod
    def validate_columns_count(cls, v: List[OSIColumn]) -> List[OSIColumn]:
        """Log warning when dataset exceeds recommended per-dataset column count."""
        if len(v) > RECOMMENDED_DATASET_COLUMN_LIMIT:
            logger.warning(
                "Dataset has %s columns, exceeding recommended %s-column design boundary",
                len(v),
                RECOMMENDED_DATASET_COLUMN_LIMIT,
            )
        return v

    def model_post_init(self, __context: Any) -> None:
        """Set defaults after initialization."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)
        if not self.source_table:
            object.__setattr__(self, "source_table", self.unique_name)

    def get_column(self, name: str) -> Optional[OSIColumn]:
        """Get a column by unique_name."""
        for col in self.columns:
            if col.unique_name == name:
                return col
        return None

    def get_key_columns(self) -> List[OSIColumn]:
        """Get all key columns."""
        return [col for col in self.columns if col.is_key]

    def get_numeric_columns(self) -> List[OSIColumn]:
        """Get all numeric columns (candidates for measures)."""
        numeric_types = {OSIDataType.INTEGER, OSIDataType.DECIMAL, OSIDataType.FLOAT}
        return [col for col in self.columns if col.data_type in numeric_types]


# =============================================================================
# Metric / Measure Definition
# =============================================================================


class OSIMetric(OSIBaseModel):
    """
    OSI Metric (Measure) definition.

    Represents a numeric calculation that spans datasets,
    with aggregation rules and business metadata.

    Attributes:
        unique_name: Unique identifier for the metric.
        label: Human-readable display name.
        dataset: Source dataset containing the measure column.
        source_column: Column to aggregate (if simple aggregation).
        expression: Full expression (if calculated metric).
        aggregation: Aggregation type (sum, count, avg, etc.).
        description: Business description of the metric.
        format_string: Display format pattern.
        business_owner: Contact for metric ownership (OSI attribute).
        is_hidden: Whether metric should be hidden from end users.
    """

    unique_name: str = Field(..., min_length=1, description="Unique metric identifier")
    label: str = Field(default="", description="Display name")
    dataset: str = Field(..., description="Source dataset name")
    source_column: Optional[str] = Field(
        default=None, description="Column to aggregate"
    )
    expression: Optional[str] = Field(
        default=None, description="Full calculation expression"
    )
    aggregation: OSIAggregationType = Field(
        default=OSIAggregationType.SUM, description="Aggregation type"
    )
    description: Optional[str] = Field(default=None, description="Metric description")
    format_string: Optional[str] = Field(default=None, description="Display format")
    business_owner: Optional[str] = Field(
        default=None, description="Metric ownership contact (OSI attribute)"
    )
    is_hidden: bool = Field(default=False, description="Hidden from end users")
    # Cortex Analyst access and NLP metadata
    access_modifier: Optional[str] = Field(
        default=None,
        description="Cortex Analyst access: 'public_access' or 'private_access'"
    )
    synonyms: List[str] = Field(default_factory=list, description="Alternative names for NLP matching")

    @field_validator("unique_name")
    @classmethod
    def validate_unique_name(cls, v: str) -> str:
        """Ensure metric name is not empty or whitespace-only."""
        if not v or v.isspace():
            raise ValueError("Metric unique_name cannot be empty")
        return v

    @model_validator(mode="after")
    def validate_source_or_expression(self) -> "OSIMetric":
        """Ensure either source_column or expression is provided."""
        if not self.source_column and not self.expression:
            raise ValueError(
                "Metric must have either source_column or expression defined"
            )
        return self

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)


# =============================================================================
# Relationship Definition
# =============================================================================


class OSIRelationship(OSIBaseModel):
    """
    OSI Relationship definition.

    Defines the join path between two datasets, including
    cardinality and cross-filter settings.

    Attributes:
        unique_name: Unique identifier for the relationship.
        from_dataset: Source dataset (typically fact table).
        from_columns: Join column(s) in source dataset.
        to_dataset: Target dataset (typically dimension).
        to_columns: Join column(s) in target dataset.
        cardinality: Relationship cardinality type.
        cross_filter_direction: Cross-filtering behavior.
        is_active: Whether relationship is active by default.
    """

    unique_name: str = Field(
        ..., min_length=1, description="Unique relationship identifier"
    )
    from_dataset: str = Field(..., description="Source dataset name")
    from_columns: List[str] = Field(..., min_length=1, description="Source columns")
    to_dataset: str = Field(..., description="Target dataset name")
    to_columns: List[str] = Field(..., min_length=1, description="Target columns")
    cardinality: OSICardinality = Field(
        default=OSICardinality.MANY_TO_ONE, description="Cardinality type"
    )
    cross_filter_direction: OSICrossFilterDirection = Field(
        default=OSICrossFilterDirection.SINGLE, description="Cross-filter direction"
    )
    is_active: bool = Field(default=True, description="Active relationship indicator")

    @field_validator("unique_name")
    @classmethod
    def validate_unique_name(cls, v: str) -> str:
        """Enforce strict relationship naming and reject system or GUID generated names."""
        upper_v = v.upper()
        if upper_v.startswith("SYS_"):
            raise ValueError(f"System generated relationship names starting with SYS_ are rejected: {v}")
        if "GUID" in upper_v or re.search(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}', v):
            raise ValueError(f"GUIDs are not allowed in relationship names: {v}")
        if not re.match(r"^REL_[A-Z0-9_]+_[A-Z0-9_]+__[A-Z0-9_]+_[A-Z0-9_]+(?:_[0-9]+)?$", upper_v):
            raise ValueError(
                f"Relationship name '{v}' does not match required format "
                "REL_<FROM_TABLE>_<FROM_COLUMN>__<TO_TABLE>_<TO_COLUMN>"
            )
        return v

    @model_validator(mode="after")
    def validate_column_count_match(self) -> "OSIRelationship":
        """Ensure from_columns and to_columns have same count."""
        if len(self.from_columns) != len(self.to_columns):
            raise ValueError(
                f"Relationship column count mismatch: "
                f"from_columns has {len(self.from_columns)}, "
                f"to_columns has {len(self.to_columns)}"
            )
        return self


# =============================================================================
# Dimension Hierarchy Components
# =============================================================================


class OSILevel(OSIBaseModel):
    """Level in a dimension hierarchy."""

    unique_name: str = Field(..., min_length=1, description="Unique level identifier")
    label: str = Field(default="", description="Display name")
    attribute: str = Field(..., description="Source attribute/column name")

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)


class OSIHierarchy(OSIBaseModel):
    """Hierarchy defining drill-down paths in a dimension."""

    unique_name: str = Field(
        ..., min_length=1, description="Unique hierarchy identifier"
    )
    label: str = Field(default="", description="Display name")
    levels: List[OSILevel] = Field(
        default_factory=list, description="Hierarchy levels (ordered)"
    )

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)


class OSIAttribute(OSIBaseModel):
    """Attribute in a dimension."""

    unique_name: str = Field(
        ..., min_length=1, description="Unique attribute identifier"
    )
    label: str = Field(default="", description="Display name")
    dataset: str = Field(..., description="Source dataset name")
    source_column: str = Field(..., description="Source column name")
    is_hidden: bool = Field(default=False, description="Hidden from end users")

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)


class OSIDimension(OSIBaseModel):
    """
    OSI Dimension definition.

    Represents a logical collection of attributes and hierarchies
    that provide context for analyzing facts.

    Attributes:
        unique_name: Unique identifier for the dimension.
        label: Human-readable display name.
        description: Optional description.
        dataset: Primary dataset backing this dimension.
        attributes: List of dimension attributes.
        hierarchies: List of drill-down hierarchies.
        is_hidden: Whether dimension should be hidden.
    """

    unique_name: str = Field(
        ..., min_length=1, description="Unique dimension identifier"
    )
    label: str = Field(default="", description="Display name")
    description: Optional[str] = Field(
        default=None, description="Dimension description"
    )
    dataset: str = Field(..., description="Primary backing dataset")
    attributes: List[OSIAttribute] = Field(
        default_factory=list, description="Dimension attributes"
    )
    hierarchies: List[OSIHierarchy] = Field(
        default_factory=list, description="Drill-down hierarchies"
    )
    is_hidden: bool = Field(default=False, description="Hidden from end users")

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)


# =============================================================================
# Top-Level Semantic Model
# =============================================================================


class OSIModel(OSIBaseModel):
    """
    OSI Semantic Model - top-level container.

    This is the canonical intermediate representation for all
    semantic model conversions. All data must pass through this
    format when moving between platforms.

    Attributes:
        unique_name: Unique identifier for the model.
        label: Human-readable display name.
        description: Model description.
        version: Semantic version string.
        datasets: List of dataset definitions.
        metrics: List of metric definitions.
        dimensions: List of dimension definitions.
        relationships: List of relationship definitions.
        source_platform: Platform the model was extracted from.
        created_at: Timestamp when model was created.
        metadata: Additional platform-specific metadata.
    """

    unique_name: str = Field(..., min_length=1, description="Unique model identifier")
    label: str = Field(default="", description="Display name")
    description: Optional[str] = Field(default=None, description="Model description")
    version: str = Field(default="1.0.0", description="Semantic version")
    datasets: List[OSIDataset] = Field(
        default_factory=list, description="Dataset definitions"
    )
    metrics: List[OSIMetric] = Field(
        default_factory=list, description="Metric definitions"
    )
    dimensions: List[OSIDimension] = Field(
        default_factory=list, description="Dimension definitions"
    )
    relationships: List[OSIRelationship] = Field(
        default_factory=list, description="Relationship definitions"
    )
    source_platform: Optional[str] = Field(
        default=None, description="Platform model was extracted from"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Creation timestamp"
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )

    model_config = {
        "strict": False,  # Allow datetime serialization
        "validate_assignment": True,
        "extra": "forbid",
    }

    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            object.__setattr__(self, "label", self.unique_name)

    @model_validator(mode="after")
    def validate_prd_boundaries(self) -> "OSIModel":
        """Validate exact architectural boundaries defined in the PRD."""
        if len(self.datasets) > 75:
            raise ValueError(f"OSIModel exceeds maximum 75 tables per model (has {len(self.datasets)})")
        
        if len(self.relationships) > 75:
            raise ValueError(f"OSIModel exceeds maximum 75 relationships per model (has {len(self.relationships)})")
            
        total_columns = sum(len(ds.columns) for ds in self.datasets)
        if total_columns > RECOMMENDED_MODEL_TOTAL_COLUMN_LIMIT:
            # PRD notes Snowflake Cortex LLM context recommendations may vary by workload.
            logger.warning(
                f"[RunID: N/A] OSIModel '{self.unique_name}' has {total_columns} total columns, "
                f"which exceeds the configured recommended maximum of "
                f"{RECOMMENDED_MODEL_TOTAL_COLUMN_LIMIT} total columns "
                f"for semantic views. This may cause LLM context window exhaustion."
            )
            
        return self

    def get_dataset(self, name: str) -> Optional[OSIDataset]:
        """Get a dataset by unique_name."""
        for ds in self.datasets:
            if ds.unique_name == name:
                return ds
        return None

    def get_metric(self, name: str) -> Optional[OSIMetric]:
        """Get a metric by unique_name."""
        for m in self.metrics:
            if m.unique_name == name:
                return m
        return None

    def get_dimension(self, name: str) -> Optional[OSIDimension]:
        """Get a dimension by unique_name."""
        for d in self.dimensions:
            if d.unique_name == name:
                return d
        return None

    def get_relationship(self, name: str) -> Optional[OSIRelationship]:
        """Get a relationship by unique_name."""
        for r in self.relationships:
            if r.unique_name == name:
                return r
        return None

    def find_relationships_for_dataset(self, dataset_name: str) -> List[OSIRelationship]:
        """Find all relationships involving a dataset."""
        return [
            r
            for r in self.relationships
            if r.from_dataset == dataset_name or r.to_dataset == dataset_name
        ]

    def validate_integrity(self) -> List[str]:
        """
        Validate semantic integrity of the model.

        Checks for:
        - Metrics referencing non-existent datasets
        - Relationships referencing non-existent datasets/columns
        - Dimensions referencing non-existent datasets

        Returns:
            List of validation error messages (empty if valid).
        """
        errors: List[str] = []
        dataset_names = {ds.unique_name for ds in self.datasets}

        # Validate metrics reference valid datasets
        for metric in self.metrics:
            if metric.dataset not in dataset_names:
                errors.append(
                    f"Metric '{metric.unique_name}' references unknown dataset "
                    f"'{metric.dataset}'"
                )

        # Validate relationships reference valid datasets
        for rel in self.relationships:
            if rel.from_dataset not in dataset_names:
                errors.append(
                    f"Relationship '{rel.unique_name}' from_dataset "
                    f"'{rel.from_dataset}' not found"
                )
            if rel.to_dataset not in dataset_names:
                errors.append(
                    f"Relationship '{rel.unique_name}' to_dataset "
                    f"'{rel.to_dataset}' not found"
                )

        # Validate dimensions reference valid datasets
        for dim in self.dimensions:
            if dim.dataset not in dataset_names:
                errors.append(
                    f"Dimension '{dim.unique_name}' references unknown dataset "
                    f"'{dim.dataset}'"
                )

        return errors


# =============================================================================
# Expression Dialects — Multi-Dialect Expression Support
# =============================================================================


class OSIExpressionDialect(OSIBaseModel):
    """
    Multi-dialect expression holder for metric translations.

    Stores the same semantic expression in multiple SQL/functional dialects:
    - DAX (Data Analysis Expressions)
    - T-SQL (SQL Server)
    - Snowflake SQL
    - ANSI SQL
    - Python PySpark UDF

    This enables semantic model expressions to be rendered in any target platform.

    Attributes:
        dialect: The expression dialect/language (e.g., 'DAX', 'SNOWFLAKE_SQL')
        expression: Expression text in that dialect
    """

    dialect: str = Field(
        ...,
        description="Expression dialect name (DAX, T_SQL, SNOWFLAKE_SQL, ANSI_SQL, PYSPARK)"
    )
    expression: str = Field(
        ...,
        description="Expression text in the specified dialect"
    )

