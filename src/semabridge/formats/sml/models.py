"""
SML compatibility shim.

This module provides a lightweight compatibility layer that maps the
legacy SML symbol names to the canonical OSI/Intermediate models.
Call sites importing `semabridge.formats.sml` will continue to work
while the codebase migrates to the official OSI representation in
`semabridge.intermediate.models`.
"""

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

# Backwards-compatible aliases (legacy SML names)
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
]
