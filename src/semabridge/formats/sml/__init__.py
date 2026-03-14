"""
SML (Semantic Modeling Language) module — backward compatibility bridge.

This module re-exports all SML classes from the canonical location
(semabridge.sml) for imports that expect semabridge.formats.sml.

Usage (unchanged):
    from semabridge.formats.sml.models import SMLModel, SMLDataset
"""

# Re-export all SML models from canonical location
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLDimension,
    SMLAttribute,
    SMLHierarchy,
    SMLLevel,
    SMLMetric,
    SMLRelationship,
    AggregationType,
)
from semabridge.sml.serializer import SMLSerializer
from semabridge.sml.assembler import SMLAssembler

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
    "SMLSerializer",
    "SMLAssembler",
]
