"""
Semabridge - Snowflake to Fabric Semantic Model Pipeline

Automates building Fabric Power BI semantic models from Snowflake metadata.
Pipeline: Source (Snowflake) → Extract → SML (YAML) → Emit (Fabric TMSL)
"""

__version__ = "1.0.0"
__author__ = "Platform Engineering Team"

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
except ImportError:
    # For development when running directly
    from .sml.models import (
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
]
