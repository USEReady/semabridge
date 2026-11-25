"""Data shapes of the planner: settings in, PlannerResult out."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class SourceId:
    """Structured source identity (never a joined string). Name-based today:
    SemaBridge does not carry Power BI lineage tags into the model; when it does,
    `lineage` takes precedence. A renamed source object therefore shows up as a
    new identity; resync reports it, it is not guessed."""

    kind: str                          # field | measure | implicit_measure | table
    table: str = ""
    name: str = ""
    aggregation: Optional[str] = None  # implicit measures: SUM / AVG / ... of the column
    lineage: Optional[str] = None

    def key(self) -> Tuple[str, str, str, str, str]:
        return (self.kind, self.table, self.name, self.aggregation or "", self.lineage or "")

    @property
    def identity_basis(self) -> str:
        return "lineage" if self.lineage else "name"


class PlannerError(ValueError):
    """The planner cannot run with these settings (e.g. primary_fact_only without a fact)."""


class PlannerInvariantError(RuntimeError):
    """The planner produced an inconsistent plan; raised instead of emitting it."""


class Capability:
    """Databricks features the planner uses only once a live probe (3.5) proved them."""

    ONE_TO_MANY_JOINS = "one_to_many_joins"     # Placement B (composed measures)
    MIXED_ROOT_VIEWS = "mixed_root_views"       # A and B measures in the same view
    CONSTANT_COALESCE = "constant_coalesce"     # COALESCE(MAX(c), c) for DAX constants

    ALL = frozenset({ONE_TO_MANY_JOINS, MIXED_ROOT_VIEWS, CONSTANT_COALESCE})


CALCULATE_MODES = ("approximate", "strict")
VIEW_MODES = ("separate_views", "selected_facts", "primary_fact_only")


@dataclass(frozen=True)
class PlannerSettings:
    calculate_mode: str = "approximate"
    view_mode: str = "separate_views"
    selected_tables: Tuple[str, ...] = ()          # selected_facts: source tables to build views for
    primary_fact: Optional[str] = None             # primary_fact_only: the user's chosen table
    excluded_measures: Tuple[str, ...] = ()        # the user unticked these (left out: user_excluded)
    capabilities: FrozenSet[str] = frozenset()
    # Names already deployed, kept on resync (3.8 reads them from the deployed views):
    # {view source table: {SourceId: deployed name}} and {view source table: view name}.
    reserved_names: Mapping[str, Mapping["SourceId", str]] = field(default_factory=dict)
    reserved_view_names: Mapping[str, str] = field(default_factory=dict)
    security_roles: Optional[int] = None           # roles found in the PBIX; None = not extracted

    def __post_init__(self) -> None:
        if self.calculate_mode not in CALCULATE_MODES:
            raise PlannerError(f"calculate_mode must be one of {CALCULATE_MODES}, got {self.calculate_mode!r}")
        if self.view_mode not in VIEW_MODES:
            raise PlannerError(f"view_mode must be one of {VIEW_MODES}, got {self.view_mode!r}")
        unknown = set(self.capabilities) - Capability.ALL
        if unknown:
            raise PlannerError(f"unknown planner capabilities: {sorted(unknown)}")

    def has(self, capability: str) -> bool:
        return capability in self.capabilities

    def as_dict(self) -> Dict[str, Any]:
        return {
            "calculate_mode": self.calculate_mode,
            "view_mode": self.view_mode,
            "selected_tables": list(self.selected_tables),
            "primary_fact": self.primary_fact,
            "excluded_measures": list(self.excluded_measures),
            "capabilities": sorted(self.capabilities),
            "security_roles": self.security_roles,
        }


# --- Stage 0 -----------------------------------------------------------------------------


@dataclass
class Aggregate:
    """One aggregate occurrence in a measure (after following measure references)."""

    function: str                    # SUM, AVERAGE, COUNTROWS, SUMX, ...
    grain: str                       # the table it counts over (dataset name)
    value_tables: FrozenSet[str]     # tables whose values it reads (grain + RELATED lookups)
    condition_tables: FrozenSet[str] # tables used only in conditions inside it
    row_count: bool = False          # COUNTROWS(T): the converter writes COUNT(*)
    via_measure: Optional[str] = None  # the referenced measure it came from, if any


@dataclass
class MeasureSemantics:
    name: str
    aggregates: List[Aggregate] = field(default_factory=list)
    condition_tables: FrozenSet[str] = frozenset()   # CALCULATE / FILTER / time-intelligence tables
    measure_refs: Tuple[str, ...] = ()               # direct references, in order
    uses_calculate: bool = False
    filter_removal: bool = False                     # ALL / REMOVEFILTERS / ALLEXCEPT as a filter
    time_intelligence: bool = False
    constant: Optional[str] = None                   # the literal, for a table-independent constant
    issue: Optional[Tuple[str, str]] = None          # (reason code, detail) when unclassifiable

    @property
    def grains(self) -> FrozenSet[str]:
        return frozenset(a.grain for a in self.aggregates)

    @property
    def tables(self) -> FrozenSet[str]:
        out = set(self.condition_tables)
        for a in self.aggregates:
            out |= a.value_tables | a.condition_tables | {a.grain}
        return frozenset(out)


# --- result ------------------------------------------------------------------------------


@dataclass
class JoinPlan:
    name: str                        # path used in expressions: "store" or "store.district"
    dataset: str
    uc_table: Optional[str]          # catalog.schema.table, None when unbound/stub
    parent: str                      # "source" or the parent join's name
    on: List[Tuple[str, str]]        # (parent column, this table's column), real names
    cardinality: str = "many_to_one"
    reason: str = "business_dimension"   # or measure_dependency
    readiness: str = "real"          # real | structure_only | unbound
    on_expr: str = ""                # the join condition as written in YAML (with any casts)
    # identity: the relationship, not the generated alias
    relationship: Tuple[str, Tuple[str, ...], str, Tuple[str, ...]] = ("", (), "", ())
    key_types: List[Tuple[str, str]] = field(default_factory=list)   # (parent type, child type) per key
    warnings: List[str] = field(default_factory=list)


@dataclass
class FieldPlan:
    name: str
    expr: str
    display_name: str
    dataset: str
    column: str
    path: str                        # "source" or a join name
    synonyms: List[str] = field(default_factory=list)
    readiness: str = "real"
    source_verification: str = "verified"    # verified | unverified (name-matched calculated column)
    source_id: Optional[SourceId] = None
    match_method: Optional[str] = None       # exact | case_insensitive | cleaned (bound tables)
    source_format: Optional[str] = None      # kept internally; emitted only where Databricks supports it
    name_generated: bool = True              # False for a preserved (deployed) name


@dataclass
class MeasurePlan:
    name: str
    source_name: str                 # the Power BI measure name
    expr: str
    display_name: str
    placement: str                   # A | B
    grain: str
    semantic_status: str = "converted"       # converted | approximate
    source_verification: str = "verified"    # verified | unverified
    readiness: str = "real"                  # real | structure_only | unbound
    trust: str = "pending_checks"            # becomes trusted only after 3.9 passes
    warnings: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)
    synonyms: List[str] = field(default_factory=list)
    comment: Optional[str] = None
    source_id: Optional[SourceId] = None
    source_format: Optional[str] = None
    name_generated: bool = True
    notes: List[str] = field(default_factory=list)


@dataclass
class ViewPlan:
    name: str
    source_dataset: str
    source_table: Optional[str]
    version: str = "1.1"
    readiness: str = "real"
    joins: List[JoinPlan] = field(default_factory=list)
    fields: List[FieldPlan] = field(default_factory=list)
    measures: List[MeasurePlan] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def placements(self) -> List[str]:
        return sorted({m.placement for m in self.measures})


@dataclass
class LeftOut:
    measure: str
    reason: str
    detail: str = ""
    root_cause: Optional[str] = None
    components: List[str] = field(default_factory=list)


@dataclass
class Rename:
    view: str
    kind: str                        # measure | field | join | view
    original: str
    final: str
    why: str
    source_id: Optional[SourceId] = None


@dataclass
class PlannerResult:
    settings: PlannerSettings
    views: List[ViewPlan] = field(default_factory=list)
    left_out: List[LeftOut] = field(default_factory=list)
    renames: List[Rename] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def view(self, name: str) -> Optional[ViewPlan]:
        return next((v for v in self.views if v.name == name), None)

    def placed(self, source_name: str) -> Optional[Tuple[ViewPlan, MeasurePlan]]:
        for v in self.views:
            for m in v.measures:
                if m.source_name == source_name:
                    return v, m
        return None

    def left_out_reason(self, source_name: str) -> Optional[LeftOut]:
        return next((lo for lo in self.left_out if lo.measure == source_name), None)

    def as_dict(self) -> Dict[str, Any]:
        def view_dict(v: ViewPlan) -> Dict[str, Any]:
            d = asdict(v)
            d["placements"] = v.placements
            for j in d["joins"]:
                j["on"] = [list(p) for p in j["on"]]
            return d

        placed = sum(len(v.measures) for v in self.views)
        return {
            "platform": "databricks",
            "naming_policy_revision": 2,
            "settings": self.settings.as_dict(),
            "summary": {
                "views": len(self.views),
                "measures_placed": placed,
                "measures_left_out": len(self.left_out),
                "approximate": sum(1 for v in self.views for m in v.measures if m.semantic_status == "approximate"),
                "composed": sum(1 for v in self.views for m in v.measures if m.placement == "B"),
            },
            "views": [view_dict(v) for v in self.views],
            "left_out": [asdict(x) for x in self.left_out],
            "renames": [asdict(x) for x in self.renames],
            "notes": list(self.notes),
        }
