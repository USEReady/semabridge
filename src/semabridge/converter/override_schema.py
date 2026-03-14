"""
Override Schema — semantic model override layer definitions.

Defines the data structures for storing and managing semantic overrides:
- Cortex metadata layers
- Semantic view layers  
- Dynamic table layers
- SQL overrides
- Window function specifications

These allow semantic definitions to augment or modify physical table behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ───────────────────────────────────────────────────────────────────────────
# Enumerations
# ───────────────────────────────────────────────────────────────────────────

class OverrideStatus(str, Enum):
    """Status of an override configuration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DEPRECATED = "deprecated"
    REVIEW_REQUIRED = "review_required"


# ───────────────────────────────────────────────────────────────────────────
# Data Classes — Override Layers
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class CortexSynonym:
    """A synonym mapping in Cortex metadata."""

    logical_name: str
    physical_name: str
    is_primary: bool = False


@dataclass
class WindowFunctionSpec:
    """Specification for a window function."""

    function_name: str
    partition_by: List[str] = field(default_factory=list)
    order_by: List[str] = field(default_factory=list)
    frame: str = "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW"


@dataclass
class CortexMetadataLayer:
    """Cortex metadata override layer."""

    name: str
    description: str = ""
    synonyms: List[CortexSynonym] = field(default_factory=list)
    status: OverrideStatus = OverrideStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticViewLayer:
    """Semantic view override layer."""

    name: str
    source_table: str
    transformation_sql: str
    description: str = ""
    status: OverrideStatus = OverrideStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DynamicTableLayer:
    """Dynamic table override layer."""

    name: str
    source_table: str
    refresh_interval: str = "1 HOUR"
    description: str = ""
    status: OverrideStatus = OverrideStatus.ACTIVE
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SemanticMetricSpec:
    """Specification for a semantic metric."""

    name: str
    definition: str
    aggregation_type: str = "sum"
    description: str = ""


@dataclass
class SemanticDimension:
    """Specification for a semantic dimension."""

    name: str
    column_name: str
    data_type: str = "string"
    description: str = ""
    is_time_dim: bool = False


@dataclass
class SQLOverrideFile:
    """Root structure for SQL override files."""

    name: str
    version: str = "1.0"
    description: str = ""
    cortex_layers: List[CortexMetadataLayer] = field(default_factory=list)
    semantic_views: List[SemanticViewLayer] = field(default_factory=list)
    dynamic_tables: List[DynamicTableLayer] = field(default_factory=list)
    metrics: List[SemanticMetricSpec] = field(default_factory=list)
    dimensions: List[SemanticDimension] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_cortex_layer(self, layer: CortexMetadataLayer) -> None:
        """Add a Cortex metadata layer."""
        self.cortex_layers.append(layer)

    def add_semantic_view(self, view: SemanticViewLayer) -> None:
        """Add a semantic view."""
        self.semantic_views.append(view)

    def add_dynamic_table(self, table: DynamicTableLayer) -> None:
        """Add a dynamic table."""
        self.dynamic_tables.append(table)

    def add_metric(self, metric: SemanticMetricSpec) -> None:
        """Add a metric specification."""
        self.metrics.append(metric)

    def add_dimension(self, dim: SemanticDimension) -> None:
        """Add a dimension specification."""
        self.dimensions.append(dim)
