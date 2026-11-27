"""Read-only, target-specific facts for the ontology screens (see base.py)."""

from semabridge.ontology.targets.base import (
    ItemFacts,
    Occurrence,
    OccurrenceState,
    OntologyTargetAdapter,
    TargetFacts,
    item_key,
)
from semabridge.ontology.targets.registry import UnsupportedOntologyTarget, ontology_adapter_for

__all__ = [
    "ItemFacts",
    "Occurrence",
    "OccurrenceState",
    "OntologyTargetAdapter",
    "TargetFacts",
    "UnsupportedOntologyTarget",
    "item_key",
    "ontology_adapter_for",
]
