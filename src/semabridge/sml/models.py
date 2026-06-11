"""
SML (Semantic Modeling Language) Pydantic Models.

Defines the data structures for the YAML-based semantic model representation.
Based on the open SML specification: https://github.com/semanticdatalayer/SML

These models serve as the intermediate format between:
- Source (Snowflake metadata)
- Target (Fabric TMSL/model.bim)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class AggregationType(str, Enum):
    """Aggregation types for metrics."""
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    NONE = "none"


class Cardinality(str, Enum):
    """Relationship cardinality types."""
    ONE_TO_ONE = "one-to-one"
    ONE_TO_MANY = "one-to-many"
    MANY_TO_ONE = "many-to-one"
    MANY_TO_MANY = "many-to-many"


class CrossFilterDirection(str, Enum):
    """Cross-filter direction for relationships."""
    SINGLE = "single"
    BOTH = "both"


class DataType(str, Enum):
    """Normalized data types across platforms."""
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
    
    @classmethod
    def from_snowflake(cls, sf_type: str) -> "DataType":
        """Map Snowflake data type to normalized type."""
        sf_upper = sf_type.upper().split("(")[0].strip()
        
        mapping = {
            # String types
            "VARCHAR": cls.STRING,
            "CHAR": cls.STRING,
            "CHARACTER": cls.STRING,
            "STRING": cls.STRING,
            "TEXT": cls.STRING,
            
            # Numeric types
            "NUMBER": cls.DECIMAL,
            "DECIMAL": cls.DECIMAL,
            "NUMERIC": cls.DECIMAL,
            "INT": cls.INTEGER,
            "INTEGER": cls.INTEGER,
            "BIGINT": cls.INTEGER,
            "SMALLINT": cls.INTEGER,
            "TINYINT": cls.INTEGER,
            "BYTEINT": cls.INTEGER,
            "FLOAT": cls.FLOAT,
            "FLOAT4": cls.FLOAT,
            "FLOAT8": cls.FLOAT,
            "DOUBLE": cls.FLOAT,
            "DOUBLE PRECISION": cls.FLOAT,
            "REAL": cls.FLOAT,
            
            # Boolean
            "BOOLEAN": cls.BOOLEAN,
            
            # Date/Time
            "DATE": cls.DATE,
            "DATETIME": cls.DATETIME,
            "TIME": cls.TIME,
            "TIMESTAMP": cls.DATETIME,
            "TIMESTAMP_LTZ": cls.DATETIME,
            "TIMESTAMP_NTZ": cls.DATETIME,
            "TIMESTAMP_TZ": cls.DATETIME,
            
            # Binary
            "BINARY": cls.BINARY,
            "VARBINARY": cls.BINARY,
            
            # Semi-structured
            "VARIANT": cls.VARIANT,
            "OBJECT": cls.VARIANT,
            "ARRAY": cls.VARIANT,
        }
        
        return mapping.get(sf_upper, cls.UNKNOWN)
    
    def to_powerbi(self) -> str:
        """Convert to Power BI/Fabric TMSL data type string."""
        # Fabric TMSL requires specific casing:
        # - Most types are lowercase (string, int64, double, decimal, boolean, binary)
        # - DateTime is camelCase: dateTime
        mapping = {
            self.STRING: "string",
            self.INTEGER: "int64",
            self.DECIMAL: "decimal",
            self.FLOAT: "double",
            self.BOOLEAN: "boolean",
            self.DATE: "dateTime",
            self.DATETIME: "dateTime",
            self.TIME: "dateTime",
            self.BINARY: "binary",
            self.VARIANT: "string",  # Variant maps to string in TMSL
            self.UNKNOWN: "string",
        }
        return mapping.get(self, "string")


class SMLColumn(BaseModel):
    """Column definition in a dataset."""
    
    unique_name: str = Field(..., description="Unique column identifier")
    label: str = Field(default="", description="Display name")
    data_type: DataType = Field(default=DataType.STRING, description="Normalized data type")
    source_type: str = Field(default="", description="Original source data type")
    description: str = Field(default="", description="Column description")
    is_hidden: bool = Field(default=False, description="Whether column is hidden")
    is_key: bool = Field(default=False, description="Whether column is a primary key")
    is_measure_candidate: bool = Field(default=False, description="Whether this numeric column should be a metric (not dimension)")
    source_expression: Optional[str] = Field(default=None, description="Source SQL expression if computed")
    default_aggregation: Optional[AggregationType] = Field(default=None, description="Default aggregation behavior")
    format_string: Optional[str] = Field(default=None, description="Display format")
    folder: Optional[str] = Field(default=None, description="Display folder")
    # Cortex Analyst advanced AI metadata
    synonyms: list[str] = Field(default_factory=list, description="Alternative names for NLP matching (e.g. ['Client', 'Account'])")
    is_enum: bool = Field(default=False, description="True when the column has a small, exhaustive set of values")
    cortex_search_service: Optional[str] = Field(default=None, description="Snowflake Cortex Search Service name linked to this text dimension")
    sample_values: list[str] = Field(default_factory=list, description="Representative sample values for NLP context")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            self.label = self.unique_name


class SMLDataset(BaseModel):
    """
    Dataset (table/query) in the semantic model.
    
    Represents a physical or logical table from the source system.
    """
    
    unique_name: str = Field(..., description="Unique dataset identifier")
    object_type: str = Field(default="dataset", description="SML object type")
    label: str = Field(default="", description="Display name")
    description: str = Field(default="", description="Dataset description")
    source_table: str = Field(default="", description="Source table name in Snowflake")
    source_schema: str = Field(default="", description="Source schema name")
    source_database: str = Field(default="", description="Source database name")
    columns: list[SMLColumn] = Field(default_factory=list, description="Column definitions")
    is_hidden: bool = Field(default=False, description="Whether dataset is hidden")
    is_fact: bool = Field(default=False, description="Whether this is a fact table")
    row_count: Optional[int] = Field(default=None, description="Approximate row count")
    
    def model_post_init(self, __context: Any) -> None:
        """Set defaults after initialization."""
        if not self.label:
            self.label = self.unique_name
        if not self.source_table:
            self.source_table = self.unique_name
    
    def get_column(self, name: str) -> Optional[SMLColumn]:
        """Get a column by name."""
        for col in self.columns:
            if col.unique_name.upper() == name.upper():
                return col
        return None
    
    def get_key_columns(self) -> list[SMLColumn]:
        """Get all key columns."""
        return [col for col in self.columns if col.is_key]
    
    def get_numeric_columns(self) -> list[SMLColumn]:
        """Get all numeric columns (candidates for measures)."""
        numeric_types = {DataType.INTEGER, DataType.DECIMAL, DataType.FLOAT}
        return [col for col in self.columns if col.data_type in numeric_types]


class SMLAttribute(BaseModel):
    """Attribute in a dimension."""
    
    unique_name: str = Field(..., description="Unique attribute identifier")
    label: str = Field(default="", description="Display name")
    dataset: str = Field(..., description="Source dataset name")
    dataset_column: str = Field(..., description="Source column name")
    description: str = Field(default="", description="Attribute description")
    is_hidden: bool = Field(default=False, description="Whether attribute is hidden")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            self.label = self.unique_name


class SMLLevel(BaseModel):
    """Level in a hierarchy."""
    
    unique_name: str = Field(..., description="Unique level identifier")
    label: str = Field(default="", description="Display name")
    attribute: str = Field(..., description="Source attribute name")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            self.label = self.unique_name


class SMLHierarchy(BaseModel):
    """Hierarchy defining drill-down paths."""
    
    unique_name: str = Field(..., description="Unique hierarchy identifier")
    label: str = Field(default="", description="Display name")
    levels: list[SMLLevel] = Field(default_factory=list, description="Hierarchy levels")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            self.label = self.unique_name


class SMLDimension(BaseModel):
    """
    Dimension (logical collection of attributes and hierarchies).
    
    Dimensions provide the context for analyzing facts.
    """
    
    unique_name: str = Field(..., description="Unique dimension identifier")
    object_type: str = Field(default="dimension", description="SML object type")
    label: str = Field(default="", description="Display name")
    description: str = Field(default="", description="Dimension description")
    dataset: str = Field(default="", description="Primary dataset for this dimension")
    attributes: list[SMLAttribute] = Field(default_factory=list, description="Dimension attributes")
    hierarchies: list[SMLHierarchy] = Field(default_factory=list, description="Dimension hierarchies")
    is_hidden: bool = Field(default=False, description="Whether dimension is hidden")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            self.label = self.unique_name


class SMLMetric(BaseModel):
    """
    Metric (measure) in the semantic model.
    
    Metrics define aggregated calculations over fact data.
    """
    
    unique_name: str = Field(..., description="Unique metric identifier")
    object_type: str = Field(default="metric", description="SML object type")
    label: str = Field(default="", description="Display name")
    description: str = Field(default="", description="Metric description")
    dataset: str = Field(..., description="Source dataset name")
    expression: str = Field(default="", description="DAX/SQL expression")
    sql_expression: Optional[str] = Field(default=None, description="Translated SQL expression for Snowflake")
    aggregation: AggregationType = Field(default=AggregationType.SUM, description="Aggregation type")
    source_column: Optional[str] = Field(default=None, description="Source column for simple aggregations")
    format_string: Optional[str] = Field(default=None, description="Display format")
    folder: Optional[str] = Field(default=None, description="Display folder")
    is_hidden: bool = Field(default=False, description="Whether metric is hidden")
    
    # DAX Sync Metadata (Complex Measure Support)
    complexity_tier: int = Field(default=1, description="DAX complexity tier: 1=simple agg, 2=arithmetic, 3=time intel, 4=complex")
    requires_time_intel: bool = Field(default=False, description="Uses Time Intelligence functions")
    group_by_dimensions: list[str] = Field(default_factory=list, description="Required dimensions for sync evaluation context")
    partition_dimension: Optional[str] = Field(default=None, description="Column for query partitioning to avoid row limits")
    depends_on_measures: list[str] = Field(default_factory=list, description="Names of measures this metric depends on")
    sync_enabled: bool = Field(default=True, description="Whether measure can be synced to Snowflake")
    sync_failure_reason: Optional[str] = Field(default=None, description="Reason if sync is disabled")
    confidence: float = Field(default=1.0, description="Confidence score for auto-detected measures")
    # Cortex Analyst advanced AI / access metadata
    access_modifier: Optional[str] = Field(
        default=None,
        description="Cortex access level: 'public_access' (visible to NL queries) or 'private_access' (internal helper)"
    )
    synonyms: list[str] = Field(default_factory=list, description="Alternative names for NLP matching")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label and generate expression if not provided."""
        if not self.label:
            self.label = self.unique_name
        
        # Generate expression if source_column is provided but expression is empty
        if self.source_column and not self.expression:
            self.expression = self._generate_expression()
    
    def _generate_expression(self) -> str:
        """Generate DAX expression from aggregation and source column."""
        if not self.source_column:
            return ""
        
        col_ref = f"[{self.source_column}]"
        
        expressions = {
            AggregationType.SUM: f"SUM({col_ref})",
            AggregationType.COUNT: f"COUNT({col_ref})",
            AggregationType.COUNT_DISTINCT: f"DISTINCTCOUNT({col_ref})",
            AggregationType.AVG: f"AVERAGE({col_ref})",
            AggregationType.MIN: f"MIN({col_ref})",
            AggregationType.MAX: f"MAX({col_ref})",
        }
        
        return expressions.get(self.aggregation, col_ref)


class SMLRelationship(BaseModel):
    """
    Relationship between datasets.
    
    Defines how tables are joined in the semantic model.
    """
    
    unique_name: str = Field(..., description="Unique relationship identifier")
    object_type: str = Field(default="relationship", description="SML object type")
    from_dataset: str = Field(..., description="Source (many) dataset name")
    from_columns: list[str] = Field(..., description="Source column names")
    to_dataset: str = Field(..., description="Target (one) dataset name")
    to_columns: list[str] = Field(..., description="Target column names")
    cardinality: Cardinality = Field(default=Cardinality.MANY_TO_ONE, description="Relationship cardinality")
    cross_filter: CrossFilterDirection = Field(default=CrossFilterDirection.SINGLE, description="Cross-filter direction")
    is_active: bool = Field(default=True, description="Whether relationship is active")
    
    @property
    def from_column(self) -> str:
        """Get the first (or only) from column."""
        return self.from_columns[0] if self.from_columns else ""
    
    @property
    def to_column(self) -> str:
        """Get the first (or only) to column."""
        return self.to_columns[0] if self.to_columns else ""


class SMLJoin(BaseModel):
    """
    Join definition for metric view YAML generation.
    
    Represents a single join in a Databricks metric view 'joins' property.
    Supports nested joins for snowflake schemas (e.g., orders -> customer -> nation).
    """
    
    name: str = Field(..., description="Join alias (used in expressions)")
    source: str = Field(..., description="Fully-qualified source table for the joined dataset")
    on: str = Field(default="", description="Join condition (e.g., 'o_custkey = c_custkey')")
    using: list[str] = Field(default_factory=list, description="USING columns for same-named join keys")
    joins: list['SMLJoin'] = Field(default_factory=list, description="Nested joins for snowflake schema patterns")
    
    def model_post_init(self, __context: Any) -> None:
        """Set defaults after initialization."""
        # Validate that the join has either an explicit ON clause or USING keys.
        if (not self.on or not self.on.strip()) and not self.using:
            raise ValueError(f"Join condition cannot be empty for join '{self.name}'")


class SourcePlatform(str, Enum):
    """Source platform for the semantic model."""
    SNOWFLAKE = "snowflake"
    FABRIC = "fabric"


class SMLModel(BaseModel):
    """
    Complete SML semantic model.
    
    This is the root object that contains all semantic model components.
    It serves as the intermediate representation between Snowflake and Fabric.
    """
    
    unique_name: str = Field(..., description="Unique model identifier")
    object_type: str = Field(default="model", description="SML object type")
    label: str = Field(default="", description="Display name")
    description: str = Field(default="", description="Model description")
    version: str = Field(default="1.0", description="Model version")
    
    # Model components
    datasets: list[SMLDataset] = Field(default_factory=list, description="Datasets (tables)")
    dimensions: list[SMLDimension] = Field(default_factory=list, description="Dimensions")
    metrics: list[SMLMetric] = Field(default_factory=list, description="Metrics (measures)")
    relationships: list[SMLRelationship] = Field(default_factory=list, description="Relationships")
    
    # Metadata
    source_system: str = Field(default="snowflake", description="Source system identifier")
    source_platform: SourcePlatform = Field(default=SourcePlatform.SNOWFLAKE, description="Platform where model was authored")
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat(), description="Creation timestamp")
    modified_at: Optional[str] = Field(default=None, description="Last modification timestamp")
    
    def model_post_init(self, __context: Any) -> None:
        """Set label from unique_name if not provided."""
        if not self.label:
            self.label = self.unique_name
    
    def get_dataset(self, name: str) -> Optional[SMLDataset]:
        """Get a dataset by name."""
        for ds in self.datasets:
            if ds.unique_name.upper() == name.upper():
                return ds
        return None
    
    def get_dimension(self, name: str) -> Optional[SMLDimension]:
        """Get a dimension by name."""
        for dim in self.dimensions:
            if dim.unique_name.upper() == name.upper():
                return dim
        return None
    
    def get_metric(self, name: str) -> Optional[SMLMetric]:
        """Get a metric by name."""
        for metric in self.metrics:
            if metric.unique_name.upper() == name.upper():
                return metric
        return None
    
    @property
    def dataset_count(self) -> int:
        """Get number of datasets."""
        return len(self.datasets)
    
    @property
    def dimension_count(self) -> int:
        """Get number of dimensions."""
        return len(self.dimensions)
    
    @property
    def metric_count(self) -> int:
        """Get number of metrics."""
        return len(self.metrics)
    
    @property
    def relationship_count(self) -> int:
        """Get number of relationships."""
        return len(self.relationships)
    
    @property
    def total_columns(self) -> int:
        """Get total number of columns across all datasets."""
        return sum(len(ds.columns) for ds in self.datasets)
    
    def get_summary(self) -> dict[str, Any]:
        """Get a summary of the model."""
        return {
            "name": self.unique_name,
            "label": self.label,
            "datasets": self.dataset_count,
            "dimensions": self.dimension_count,
            "metrics": self.metric_count,
            "relationships": self.relationship_count,
            "total_columns": self.total_columns,
            "source_system": self.source_system,
            "source_platform": self.source_platform.value,
            "version": self.version,
        }
