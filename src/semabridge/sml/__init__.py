"""
SML (Semantic Modeling Language) module.

Defines the YAML-based intermediate representation for semantic models.
"""

try:
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
    )
    from semabridge.sml.serializer import SMLSerializer
    from semabridge.sml.assembler import SMLAssembler
except ImportError:
    # For development when running directly
    from .models import (
        SMLModel,
        SMLDataset,
        SMLColumn,
        SMLDimension,
        SMLAttribute,
        SMLHierarchy,
        SMLLevel,
        SMLMetric,
        SMLRelationship,
    )
    from .serializer import SMLSerializer
    from .assembler import SMLAssembler

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
    "SMLSerializer",
    "SMLAssembler",
]
