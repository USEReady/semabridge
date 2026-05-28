"""
SML compatibility shim (lightweight).

Provides backwards-compatible symbol names for legacy imports that
expect `semabridge.sml.models`.  Where possible these aliases point to
the canonical OSI/Intermediate models. A small `SMLJoin` dataclass is
provided for join-building code that expects the legacy join shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIDimension,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIMetric,
    OSIRelationship,
    OSIAggregationType,
    OSIDataType,
    OSICardinality,
    OSICrossFilterDirection,
)
from enum import Enum


# Backwards-compatible aliases
SMLModel = OSIModel
SMLDataset = OSIDataset
SMLColumn = OSIColumn
SMLDimension = OSIDimension
SMLAttribute = OSIAttribute
SMLHierarchy = OSIHierarchy
SMLLevel = OSILevel
SMLMetric = OSIMetric
SMLRelationship = OSIRelationship
AggregationType = OSIAggregationType
DataType = OSIDataType
Cardinality = OSICardinality
CrossFilterDirection = OSICrossFilterDirection


def _data_type_from_snowflake(value: str) -> OSIDataType:
    normalized = str(value).upper()
    mapping = {
        "VARCHAR": OSIDataType.STRING,
        "STRING": OSIDataType.STRING,
        "TEXT": OSIDataType.STRING,
        "CHAR": OSIDataType.STRING,
        "CHARACTER": OSIDataType.STRING,
        "INT": OSIDataType.INTEGER,
        "INTEGER": OSIDataType.INTEGER,
        "BIGINT": OSIDataType.INTEGER,
        "SMALLINT": OSIDataType.INTEGER,
        "NUMBER": OSIDataType.DECIMAL,
        "DECIMAL": OSIDataType.DECIMAL,
        "NUMERIC": OSIDataType.DECIMAL,
        "FLOAT": OSIDataType.FLOAT,
        "DOUBLE": OSIDataType.FLOAT,
        "BOOLEAN": OSIDataType.BOOLEAN,
        "BOOL": OSIDataType.BOOLEAN,
        "DATE": OSIDataType.DATE,
        "TIMESTAMP": OSIDataType.DATETIME,
        "TIMESTAMP_NTZ": OSIDataType.DATETIME,
        "TIME": OSIDataType.TIME,
        "VARIANT": OSIDataType.VARIANT,
    }
    return mapping.get(normalized, OSIDataType.UNKNOWN)


def _data_type_from_fabric(value: str) -> OSIDataType:
    return _data_type_from_snowflake(value)


setattr(DataType, "from_snowflake", staticmethod(_data_type_from_snowflake))
setattr(DataType, "from_fabric", staticmethod(_data_type_from_fabric))


@dataclass
class SMLMetric:
    unique_name: str
    label: str = ""
    description: str = ""
    dataset: str = ""
    source_column: Optional[str] = None
    expression: Optional[str] = None
    sql_expression: Optional[str] = None
    aggregation: Optional[AggregationType] = None
    format_string: Optional[str] = None
    folder: Optional[str] = None
    is_hidden: bool = False
    complexity_tier: Optional[int] = None
    requires_time_intel: bool = False
    group_by_dimensions: List[str] = field(default_factory=list)
    depends_on_measures: List[str] = field(default_factory=list)
    sync_enabled: bool = False
    sync_failure_reason: Optional[str] = None
    partition_dimension: Optional[str] = None
    confidence: Optional[float] = None
    access_modifier: Optional[str] = None
    synonyms: List[str] = field(default_factory=list)
    translation_warning: List[str] = field(default_factory=list)


class SourcePlatform(str, Enum):
    FABRIC = "fabric"
    SNOWFLAKE = "snowflake"


@dataclass
class SMLJoin:
    """Lightweight join descriptor used by legacy join-builder code.

    Fields mirror the previous SMLJoin usage in the codebase and are
    intentionally minimal: `name`, `source`, `on`, `using`, `joins`.
    """
    name: str
    source: str
    on: str = ""
    using: List[str] = field(default_factory=list)
    joins: List["SMLJoin"] = field(default_factory=list)


@dataclass
class SMLModel:
    """Minimal SMLModel compatibility class.

    This lightweight class provides the legacy constructor/attribute shape
    expected by converter code while delegating dataset/column types to the
    canonical OSI models via the aliases above.
    """
    unique_name: str
    label: str = ""
    description: str = ""
    source_system: str = ""
    source_platform: SourcePlatform = SourcePlatform.SNOWFLAKE
    version: Optional[str] = None
    datasets: List[SMLDataset] = field(default_factory=list)
    dimensions: List[SMLDimension] = field(default_factory=list)
    metrics: List[SMLMetric] = field(default_factory=list)
    relationships: List[SMLRelationship] = field(default_factory=list)

    def model_dump(self, mode: str = "python", exclude_none: bool = False):
        """Pydantic-compatible dump used by existing persistence code.

        The compatibility shim keeps legacy call sites working without forcing
        every caller to learn about the dataclass transition.
        """
        def _dump(value):
            if hasattr(value, "model_dump"):
                try:
                    return value.model_dump(mode=mode, exclude_none=exclude_none)
                except TypeError:
                    return value.model_dump()
            if isinstance(value, list):
                return [_dump(item) for item in value]
            if isinstance(value, dict):
                return {key: _dump(item) for key, item in value.items()}
            if hasattr(value, "__dict__") and getattr(value, "__dataclass_fields__", None):
                data = {}
                for key, item in value.__dict__.items():
                    if exclude_none and item is None:
                        continue
                    data[key] = _dump(item)
                return data
            return value

        payload = {
            "unique_name": self.unique_name,
            "label": self.label,
            "description": self.description,
            "source_system": self.source_system,
            "source_platform": self.source_platform.value if hasattr(self.source_platform, "value") else self.source_platform,
            "version": self.version,
            "datasets": _dump(self.datasets),
            "dimensions": _dump(self.dimensions),
            "metrics": _dump(self.metrics),
            "relationships": _dump(self.relationships),
        }
        if exclude_none:
            payload = {key: value for key, value in payload.items() if value is not None}
        return payload

    def model_dump_json(self, *args, **kwargs):
        import json
        return json.dumps(self.model_dump(*args, **kwargs))

    def dict(self, *args, **kwargs):
        return self.model_dump(*args, **kwargs)

    @property
    def dataset_count(self) -> int:
        return len(self.datasets)

    @property
    def metric_count(self) -> int:
        return len(self.metrics)

    @property
    def dimension_count(self) -> int:
        return len(self.dimensions)

    @property
    def relationship_count(self) -> int:
        return len(self.relationships)

    @property
    def column_count(self) -> int:
        return sum(len(dataset.columns) for dataset in self.datasets)


@dataclass
class SMLAttribute:
    unique_name: str
    label: str = ""
    dataset: str = ""
    dataset_column: str = ""
    is_hidden: bool = False

__all__ = [
    "SMLModel",
    "SMLDataset",
    "SMLColumn",
    "SMLDimension",
    "SMLAttribute",
    "SMLHierarchy",
    "SMLLevel",
    "SMLMetric",
    "SMLRelationship",
    "AggregationType",
    "DataType",
    "Cardinality",
    "CrossFilterDirection",
    "SMLJoin",
]
