"""Normalised shapes shared by every platform adapter and the comparison engine.

Nothing here is platform specific: an adapter turns its own definition text into
these objects, and the engine only ever sees these.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

METRIC = "metric"
DIMENSION = "dimension"
FACT = "fact"
RELATIONSHIP = "relationship"

ADDITION = "ADDITION"
DELETION = "DELETION"
EXPRESSION_DRIFT = "EXPRESSION_DRIFT"
DESCRIPTION_DRIFT = "DESCRIPTION_DRIFT"


@dataclass(frozen=True)
class Capabilities:
    """What one source path of one platform can actually report.

    The engine never produces a finding for something the source could not have
    reported, so a platform that does not expose comments cannot raise
    description drift.
    """

    kinds: frozenset = frozenset({METRIC, DIMENSION, FACT, RELATIONSHIP})
    expressions: bool = True
    descriptions: bool = True

    def reports(self, kind: str) -> bool:
        return kind in self.kinds


@dataclass
class ObjectDef:
    kind: str
    table: str
    name: str
    expression: str = ""
    description: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        """Identity used for matching: case-insensitive kind, table and name."""
        return (self.kind.casefold(), (self.table or "").casefold(), (self.name or "").casefold())


@dataclass
class ViewDefinition:
    view_name: str
    platform: str
    source_path: str
    objects: List[ObjectDef] = field(default_factory=list)
    capabilities: Capabilities = Capabilities()
    not_deployed: List[str] = field(default_factory=list)
    retrieved_at: Optional[str] = None

    def by_key(self) -> Dict[tuple, ObjectDef]:
        out: Dict[tuple, ObjectDef] = {}
        for obj in self.objects:
            out.setdefault(obj.key, obj)  # duplicates: first wins, deterministic
        return out


@dataclass
class DriftFinding:
    category: str
    kind: str
    table: str
    name: str
    baseline_value: str = ""
    live_value: str = ""

    @property
    def sort_key(self) -> tuple:
        return (self.kind, self.category, self.table.casefold(), self.name.casefold())


class SourcePathMismatch(Exception):
    """Baseline and live were read in different forms, so they cannot be compared."""


class SidesDoNotMatch(Exception):
    """Every baseline object looked deleted and every live object looked added.

    That is the signature of a broken parser, not of a changed warehouse, so the
    caller reports "not checked" instead of storing the findings.
    """
