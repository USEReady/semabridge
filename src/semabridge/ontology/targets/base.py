"""Target-specific facts for the ontology screens (read-only).

The ontology YAML holds business MEANING (DAX, descriptions, synonyms...). Where
an item ended up on a target platform -- which view(s), under what name, with
which final expression, planned vs last verified deployment, or why it was left
out -- is derived output of the planner and the deploy. Those facts are READ on
demand from the artifacts that already exist and returned as one common read
model; nothing is written to the ontology, the plan or the deploy records.

One adapter per platform (Strategy + registry, like target_rules). Adapters
never mutate anything and hold no project state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class OccurrenceState:
    """One state of an occurrence: as currently planned, or as last verified live."""

    revision: Optional[int] = None
    expression: Optional[str] = None
    approximate: bool = False
    readiness: Optional[str] = None
    status: str = "planned"          # planned | verified | applied_unverified | unavailable
    run_id: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class Occurrence:
    """An item inside one view. One measure or column can have several."""

    occurrence_id: str               # "<view>/<name>", stable across revisions
    view: str                        # fully qualified view name
    name: str                        # the item's name inside the view
    planned: Optional[OccurrenceState] = None
    verified: Optional[OccurrenceState] = None


@dataclass
class ItemFacts:
    """Everything known on the target about one source (Power BI) item."""

    kind: str                        # measure | field
    table: str                       # Power BI table ("" for measures)
    name: str                        # Power BI measure / column name
    occurrences: List[Occurrence] = field(default_factory=list)
    left_out: Optional[Dict[str, Any]] = None    # reason / detail / root_cause

    @property
    def key(self) -> str:
        return item_key(self.kind, self.table, self.name)


@dataclass
class TargetFacts:
    """The read model for one project/model on one target."""

    target: str
    available: bool                  # False: nothing planned/deployed to read (yet)
    plan_revision: Optional[int] = None
    plan_status: Optional[str] = None
    destination: Optional[str] = None
    items: Dict[str, ItemFacts] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["items"] = {k: {**asdict(v), "key": k} for k, v in self.items.items()}
        return out


def item_key(kind: str, table: str, name: str) -> str:
    """Stable, case-insensitive key: 'measure::revenue won', 'field:accounts:account name'."""
    def norm(v: str) -> str:
        return " ".join(str(v or "").split()).casefold()

    return f"{norm(kind)}:{norm(table)}:{norm(name)}"


class OntologyTargetAdapter(ABC):
    """Reads one platform's facts for the ontology screens. Read-only."""

    target: str = ""

    @abstractmethod
    def facts(self, project_id: str, model_key: str = "") -> TargetFacts:
        """Never raises for missing artifacts: returns available=False / notes instead."""
