"""Normalized models and enumerations for the Drift Monitoring engine.

Platform adapters parse warehouse or fixture definitions into these unified
structures, allowing a single comparison engine to evaluate drift across
Snowflake, Databricks, and offline mock fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class DriftCategory(str, Enum):
    """The four canonical drift categories defined in the platform adapter architecture."""
    ADDITION = "ADDITION"
    DELETION = "DELETION"
    EXPRESSION_DRIFT = "EXPRESSION_DRIFT"
    DESCRIPTION_DRIFT = "DESCRIPTION_DRIFT"


class ObjectKind(str, Enum):
    """Kinds of objects inside a semantic or metric view."""
    METRIC = "METRIC"
    DIMENSION = "DIMENSION"
    FACT = "FACT"
    RELATIONSHIP = "RELATIONSHIP"
    UNKNOWN = "UNKNOWN"


ADDITION = DriftCategory.ADDITION.value
DELETION = DriftCategory.DELETION.value
EXPRESSION_DRIFT = DriftCategory.EXPRESSION_DRIFT.value
DESCRIPTION_DRIFT = DriftCategory.DESCRIPTION_DRIFT.value


@dataclass(frozen=True)
class Capabilities:
    """What one source path of one platform can actually report."""
    kinds: frozenset = frozenset({k.value for k in ObjectKind} | {k.value.lower() for k in ObjectKind})
    expressions: bool = True
    descriptions: bool = True

    def reports(self, kind: str) -> bool:
        k = kind.lower() if hasattr(kind, "lower") else str(kind)
        return k in self.kinds or str(kind).upper() in self.kinds

    def __iter__(self):
        d = {}
        for k in self.kinds:
            caps = set()
            if self.expressions:
                caps.add("expression")
            if self.descriptions:
                caps.add("description")
            d[k] = caps
        return iter(d.items())

    def get(self, key, default=None):
        if not self.reports(key):
            return default
        caps = set()
        if self.expressions:
            caps.add("expression")
        if self.descriptions:
            caps.add("description")
        return caps

    def __contains__(self, key):
        return self.reports(key)


class SourcePathMismatchError(ValueError):
    """Raised when comparing definitions introspected through incompatible source paths."""
    pass


class ParserOrBaselineMismatchError(ValueError):
    """Raised when 100% deletions + 100% additions with 0 expression matches indicate parser/naming mismatch."""
    pass


SourcePathMismatch = SourcePathMismatchError
SidesDoNotMatch = ParserOrBaselineMismatchError


@dataclass
class ObjectDef:
    """Normalized representation of a single object (metric, dimension, fact, relationship) in a view."""
    kind: str  # METRIC, DIMENSION, FACT, RELATIONSHIP
    table: str  # logical table inside the view or view scope
    name: str  # deployed identifier (canonical case)
    expression: str = ""  # formula / definition text
    description: Optional[str] = None  # documentation comment / description text
    properties: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        """Identity used for matching: canonical uppercase kind, table and name/relationship key."""
        from semabridge.governance.drift.comparison import _obj_key
        return _obj_key(self)


@dataclass
class ViewDefinition:
    """Platform-independent normalized representation of a view definition."""
    view_name: str  # fully qualified view name
    platform: str  # SNOWFLAKE, DATABRICKS, MOCK
    dialect: Optional[str] = None  # SQL dialect: snowflake, databricks, etc.
    source: str = "unknown"  # live | baseline | fixture
    retrieved_at: str = ""  # ISO timestamp
    objects: List[ObjectDef] = field(default_factory=list)
    raw_definition_text: Optional[str] = None
    not_deployed_objects: List[str] = field(default_factory=list)
    source_path: str = "unknown"  # yaml | describe_json | ddl | sql_ddl
    capabilities: Any = field(default_factory=Capabilities)
    not_deployed: Optional[List[str]] = None

    def __post_init__(self):
        if not self.dialect and self.platform:
            plat = self.platform.strip().lower()
            if plat not in ("unknown", "not_set", ""):
                self.dialect = plat
        if self.not_deployed is not None and not self.not_deployed_objects:
            self.not_deployed_objects = list(self.not_deployed)

    def by_key(self) -> Dict[tuple, ObjectDef]:
        out: Dict[tuple, ObjectDef] = {}
        for obj in self.objects:
            out.setdefault(obj.key, obj)
        return out


@dataclass
class DriftFinding:
    """Represents a single evaluated drift difference between baseline and live view."""
    category: DriftCategory
    kind: str
    table: str
    name: str
    baseline_value: Optional[str] = None
    live_value: Optional[str] = None
    baseline_description: Optional[str] = None
    live_description: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
