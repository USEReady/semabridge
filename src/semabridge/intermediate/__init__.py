"""
OSI (Open Semantic Interchange) Intermediate Models.

This module provides the canonical intermediate representation for semantic models.
All conversions must pass through OSI:
    - Source → OSI (extraction)
    - OSI → Target (emission)

This package is SEPARATE from semabridge.sml, which handles SML-specific
YAML format. OSI provides vendor-neutral intermediate representation.

Usage:
    from semabridge.intermediate import OSIModel, OSIDataset, OSIMetric

    model = OSIModel(
        unique_name="sales_model",
        label="Sales Analytics Model",
        datasets=[...],
        metrics=[...]
    )
"""

from semabridge.intermediate.models import (
    # Enums
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
    OSIDataType,
    # Core Models
    OSIAttribute,
    OSIColumn,
    OSIDataset,
    OSIDimension,
    OSIHierarchy,
    OSILevel,
    OSIMetric,
    OSIModel,
    OSIRelationship,
    OSIExpressionDialect,
)

__all__ = [
    # Enums
    "OSIAggregationType",
    "OSICardinality",
    "OSICrossFilterDirection",
    "OSIDataType",
    # Core Models
    "OSIAttribute",
    "OSIColumn",
    "OSIDataset",
    "OSIDimension",
    "OSIHierarchy",
    "OSILevel",
    "OSIMetric",
    "OSIModel",
    "OSIRelationship",
    "OSIExpressionDialect",
]
