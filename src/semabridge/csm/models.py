"""CSM Pydantic models - preserves ALL OSI fields and supports Multi-Dialect Expressions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Literal
from enum import Enum

from pydantic import BaseModel, Field


class CSMAggregationType(str, Enum):
    SUM = "sum"
    COUNT = "count"
    DISTINCTCOUNT = "distinctcount"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    NONE = "none"


class CSMCardinality(str, Enum):
    ONE_TO_ONE = "one-to-one"
    ONE_TO_MANY = "one-to-many"
    MANY_TO_ONE = "many-to-one"
    MANY_TO_MANY = "many-to-many"


class CSMColumn(BaseModel):
    """Column - preserves ALL OSI fields."""
    
    unique_name: str
    label: str
    data_type: str
    description: str = ""
    is_key: bool = False
    is_hidden: bool = False
    is_measure_candidate: bool = False
    
    default_aggregation: Optional[CSMAggregationType] = None
    source_expression: Optional[str] = None
    format_string: Optional[str] = None
    cortex_search_service: Optional[str] = None
    sample_values: List[Any] = Field(default_factory=list)
    
    source_column: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    is_enum: bool = False
    
    extensions: Dict[str, Any] = Field(default_factory=dict)


class CSMMetric(BaseModel):
    """Metric - Lossless Multi-Dialect Expression Store."""
    
    unique_name: str
    label: str
    description: str = ""
    dataset: str
    
    expression_dialects: Dict[str, str] = Field(default_factory=dict)
    aggregation: Optional[CSMAggregationType] = CSMAggregationType.NONE
    source_column: Optional[str] = None
    format_string: Optional[str] = None
    
    complexity_tier: int = 1
    depends_on_measures: List[str] = Field(default_factory=list)
    
    business_owner: Optional[str] = None
    is_hidden: bool = False
    synonyms: List[str] = Field(default_factory=list)
    
    sync_enabled: bool = True
    sync_failure_reason: Optional[str] = None

    extensions: Dict[str, Any] = Field(default_factory=dict)


class CSMAttribute(BaseModel):
    """Dimension attribute."""
    
    unique_name: str
    label: str
    description: str = ""
    dataset: str
    dataset_column: str
    is_hidden: bool = False


class CSMHierarchy(BaseModel):
    """Dimension hierarchy."""
    
    unique_name: str
    label: str
    source_column: Optional[str] = None


class CSMDimension(BaseModel):
    """Dimension definition."""
    
    unique_name: str
    label: str
    description: str = ""
    dataset: str
    attributes: List[CSMAttribute] = Field(default_factory=list)
    hierarchies: List[CSMHierarchy] = Field(default_factory=list)
    is_hidden: bool = False
    extensions: Dict[str, Any] = Field(default_factory=dict)


class CSMDataset(BaseModel):
    """Dataset."""
    
    unique_name: str
    label: str
    description: str = ""
    source_table: str
    source_schema: Optional[str] = None
    source_database: Optional[str] = None
    columns: List[CSMColumn] = Field(default_factory=list)
    is_fact: bool = False
    is_hidden: bool = False
    row_count: Optional[int] = None
    
    source_expression: Optional[str] = None
    format_string: Optional[str] = None

    extensions: Dict[str, Any] = Field(default_factory=dict)


class CSMRelationship(BaseModel):
    """Relationship definition."""
    
    unique_name: str
    from_dataset: str
    from_columns: List[str]
    to_dataset: str
    to_columns: List[str]
    cardinality: CSMCardinality = CSMCardinality.MANY_TO_ONE
    cross_filter_direction: str = "single"
    is_active: bool = True
    extensions: Dict[str, Any] = Field(default_factory=dict)


def _default_now():
    return datetime.now(timezone.utc)

class CSMModel(BaseModel):
    """Canonical Semantic Model - Lossless."""
    
    unique_name: str
    label: str
    description: str = ""
    version: str = "1.0.0"
    
    datasets: List[CSMDataset] = Field(default_factory=list)
    metrics: List[CSMMetric] = Field(default_factory=list)
    dimensions: List[CSMDimension] = Field(default_factory=list)
    relationships: List[CSMRelationship] = Field(default_factory=list)
    
    source_platform: Literal["fabric", "pbix", "snowflake"] = "fabric"
    
    extensions: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_default_now)
    updated_at: datetime = Field(default_factory=_default_now)
    
    def get_dataset(self, name: str) -> Optional[CSMDataset]:
        for ds in self.datasets:
            if ds.unique_name == name:
                return ds
        return None
    
    def get_metric(self, name: str) -> Optional[CSMMetric]:
        for m in self.metrics:
            if m.unique_name == name:
                return m
        return None
