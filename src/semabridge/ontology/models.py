"""Domain Ontology Data Models and Schemas.

Represents federated, team-owned domain ontologies (e.g., finance.ontology.yaml,
marketing.ontology.yaml) conforming to Enterprise Data Mesh architecture.

All user-authored models forbid unknown keys: a typo such as ``synonymz`` is a
validation error naming the key, never a silently dropped value.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator


class ConceptType(str, Enum):
    ENTITY = "ENTITY"
    DIMENSION = "DIMENSION"
    ATTRIBUTE = "ATTRIBUTE"


class CardinalityType(str, Enum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    MANY_TO_MANY = "N:N"


class RelationshipType(str, Enum):
    JOIN = "JOIN"
    DERIVES_FROM = "DERIVES_FROM"
    IS_A = "IS_A"
    APPLIES_TO = "APPLIES_TO"
    GOVERNED_BY = "GOVERNED_BY"


# "bi"   -> item derived from the BI source by a (re-)sync; refreshed on re-sync.
# "user" -> item authored by a person (YAML upload / editor); never touched by re-sync.
Origin = Literal["bi", "user"]

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def slugify_domain(name: str) -> str:
    """Stable, filesystem-safe id for a domain display name ('' if none)."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return slug[:64]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_AGG_CALL_RE = re.compile(
    r"^(APPROX_COUNT_DISTINCT|COUNT_IF|COUNT|SUM|AVG|AVERAGE|MIN|MAX)\s*\(", re.I
)


def infer_aggregation_from_sql(expr: Optional[str]) -> str:
    """Derive the aggregation of a metric from its SQL expression.

    Returns COUNT / COUNT_DISTINCT / SUM / AVG / MIN / MAX only when the whole
    expression is a single top-level aggregate call (e.g. ``COUNT(T.ID)``,
    ``COUNT(DISTINCT T.ID)``, ``SUM(T.AMT)``). Anything else -- ratios,
    arithmetic over several aggregates, window functions, bare columns or an
    empty expression -- is ``NONE``: an aggregation is never invented.
    """
    s = str(expr or "").strip().rstrip(";").strip()
    # Peel redundant outer parentheses: "(SUM(x))" -> "SUM(x)".
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        wraps = True
        for i, ch in enumerate(s):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i < len(s) - 1:
                wraps = False
                break
        if not wraps:
            break
        s = s[1:-1].strip()
    m = _AGG_CALL_RE.match(s)
    if not m:
        return "NONE"
    depth = 0
    in_str = False
    close_at = -1
    for i in range(m.end() - 1, len(s)):
        ch = s[i]
        if ch == "'":
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close_at = i
                    break
    if close_at != len(s) - 1:
        return "NONE"  # e.g. SUM(a) / COUNT(b), SUM(x) OVER (...)
    fn = m.group(1).upper()
    inner = s[m.end():close_at].strip()
    if fn == "APPROX_COUNT_DISTINCT" or (fn == "COUNT" and re.match(r"(?i)^DISTINCT\b", inner)):
        return "COUNT_DISTINCT"
    if fn in {"COUNT", "COUNT_IF"}:
        return "COUNT"
    if fn == "AVERAGE":
        return "AVG"
    return fn


def _dedupe_names(items: List[Any], kind: str, where: str) -> None:
    seen: Dict[str, str] = {}
    for item in items:
        key = str(getattr(item, "name", "") or "").casefold()
        if not key:
            continue
        if key in seen:
            raise ValueError(f"Duplicate {kind} name '{item.name}' in {where} (also declared as '{seen[key]}').")
        seen[key] = item.name


def _clean_list(values: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for v in values or []:
        text = str(v or "").strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            out.append(text)
    return out


CleanList = Annotated[List[str], AfterValidator(_clean_list)]


class DomainAttribute(_StrictModel):
    name: str = Field(..., min_length=1)
    logical_name: Optional[str] = None
    data_type: str = "VARCHAR"
    description: Optional[str] = None
    is_primary_key: bool = False
    is_hidden: bool = False
    format_string: Optional[str] = None
    synonyms: CleanList = Field(default_factory=list)
    sample_values: CleanList = Field(default_factory=list)
    # Explicit binding to model column names ("Column" or "Table.Column").
    maps_to: CleanList = Field(default_factory=list)
    origin: Optional[Origin] = None
    stale: bool = False
    bi_description: Optional[str] = None


class DomainHierarchyLevel(_StrictModel):
    name: str = Field(..., min_length=1)
    label: Optional[str] = None
    attribute: str = Field(..., min_length=1)


class DomainHierarchy(_StrictModel):
    name: str = Field(..., min_length=1)
    label: Optional[str] = None
    description: Optional[str] = None
    is_hidden: bool = False
    levels: List[DomainHierarchyLevel] = Field(default_factory=list)
    origin: Optional[Origin] = None
    stale: bool = False


class DomainConcept(_StrictModel):
    name: str = Field(..., min_length=1)
    logical_name: Optional[str] = None
    label: Optional[str] = None
    concept_type: ConceptType = ConceptType.ENTITY
    domain: Optional[str] = None
    description: Optional[str] = None
    synonyms: CleanList = Field(default_factory=list)
    business_rules: CleanList = Field(default_factory=list)
    # Optional SQL predicate enforced when querying this concept (e.g. "is_deleted = FALSE").
    filter_expression: Optional[str] = None
    data_steward: Optional[str] = None
    source_dataset: Optional[str] = None
    physical_table: Optional[str] = None
    attributes: List[DomainAttribute] = Field(default_factory=list)
    hierarchies: List[DomainHierarchy] = Field(default_factory=list)
    tags: CleanList = Field(default_factory=list)
    # Explicit binding to model table names (dataset unique_name / label / source table).
    maps_to: CleanList = Field(default_factory=list)
    origin: Optional[Origin] = None
    stale: bool = False
    bi_description: Optional[str] = None


    @model_validator(mode="after")
    def _unique_attributes(self) -> "DomainConcept":
        _dedupe_names(self.attributes, "attribute", f"concept '{self.name}'")
        return self


class DomainMetric(_StrictModel):
    name: str = Field(..., min_length=1)
    logical_name: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    # Optional: an ontology entry may only annotate an existing BI measure.
    # sql_expression: Compiled target SQL (Snowflake/Databricks) used by the query engine.
    sql_expression: Optional[str] = None
    # dax_expression: Preserves the original Power BI DAX formula separately so DAX syntax does not leak into SQL.
    dax_expression: Optional[str] = None
    deploy_reason: Optional[str] = None
    format_string: Optional[str] = None
    is_hidden: bool = False
    # Unknown by default: never invent SUM. When not given explicitly it is
    # derived from ``sql_expression`` (see ``_derive_aggregation``).
    aggregation_type: str = "NONE"
    grain: List[str] = Field(default_factory=list)
    synonyms: CleanList = Field(default_factory=list)
    business_rules: CleanList = Field(default_factory=list)
    # Optional SQL WHERE predicate enforced when calculating this metric (e.g. "order_status = 'COMPLETED'").
    filter_expression: Optional[str] = None
    # Upstream metric names this composite metric is derived from (e.g. [gross_revenue, discounts]).
    derives_from: CleanList = Field(default_factory=list)
    data_steward: Optional[str] = None
    domain: Optional[str] = None
    # Optional explicit binding to the concept (dataset) this metric is computed over.
    concept: Optional[str] = None
    semantic_view_name: Optional[str] = None
    tags: CleanList = Field(default_factory=list)
    # Explicit binding to model measure names ("Measure" or "Table.Measure").
    maps_to: CleanList = Field(default_factory=list)
    origin: Optional[Origin] = None
    stale: bool = False
    bi_description: Optional[str] = None

    @model_validator(mode="after")
    def _derive_aggregation(self) -> "DomainMetric":
        # Only fill in what the caller did not state; derive it from the SQL
        # rather than defaulting to SUM.
        if "aggregation_type" not in self.model_fields_set and self.sql_expression:
            object.__setattr__(self, "aggregation_type", infer_aggregation_from_sql(self.sql_expression))
        return self


class DomainRelationship(_StrictModel):
    name: Optional[str] = None
    from_concept: str = Field(..., min_length=1)
    to_concept: str = Field(..., min_length=1)
    cardinality: CardinalityType = CardinalityType.MANY_TO_ONE
    relationship_type: RelationshipType = RelationshipType.JOIN
    join_expression: Optional[str] = None
    predicate_label: Optional[str] = None
    cross_filter_direction: Optional[str] = None
    # is_active: Preserves whether the relationship is active in the source BI model.
    # Inactive relationships are skipped during automated join generation to prevent phantom/cycle joins.
    is_active: bool = True
    origin: Optional[Origin] = None
    stale: bool = False


class DomainPolicy(_StrictModel):
    """First-class governance policy with an enforceable SQL filter predicate."""
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    filter_expression: str = Field(..., min_length=1)
    # Names of concepts or metrics this policy governs; empty or ["*"] means domain-wide.
    applies_to: CleanList = Field(default_factory=list)
    data_steward: Optional[str] = None
    domain: Optional[str] = None


class DomainOntology(_StrictModel):
    """Represents a single team or domain-level ontology file (e.g. finance.ontology.yaml).

    ``domain`` is the display name (source of truth, from the YAML); ``id`` is a
    stable slug used for the file name and project attachments, kept across renames.
    """
    domain: str = Field(..., min_length=1)
    id: Optional[str] = None
    owner: Optional[str] = None
    version: str = "1.0.0"
    description: Optional[str] = None
    # Project whose BI model this domain was bootstrapped from; used by
    # "sync with OSI" to re-merge the latest model into this domain.
    source_project_id: Optional[str] = None
    semantic_view_name: Optional[str] = None
    deployment_error: Optional[str] = None
    concepts: List[DomainConcept] = Field(default_factory=list)
    metrics: List[DomainMetric] = Field(default_factory=list)
    relationships: List[DomainRelationship] = Field(default_factory=list)
    policies: List[DomainPolicy] = Field(default_factory=list)

    @field_validator("domain")
    @classmethod
    def _valid_domain(cls, v: str) -> str:
        text = str(v or "").strip()
        if not text:
            raise ValueError("'domain' must be a non-empty name")
        if len(text) > 120:
            raise ValueError("'domain' must be at most 120 characters")
        if "/" in text or "\\" in text or ".." in text or re.search(r"[\x00-\x1f]", text):
            raise ValueError(f"'domain' contains illegal characters (path separators, '..' or control chars): {text!r}")
        if not slugify_domain(text):
            raise ValueError(f"'domain' must contain at least one letter or digit: {text!r}")
        return text

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not _SLUG_RE.match(v):
            raise ValueError(f"'id' must match {_SLUG_RE.pattern} (lowercase letters, digits, '_' or '-'): {v!r}")
        return v

    @model_validator(mode="after")
    def _unique_and_ids(self) -> "DomainOntology":
        _dedupe_names(self.concepts, "concept", f"domain '{self.domain}'")
        _dedupe_names(self.metrics, "metric", f"domain '{self.domain}'")
        _dedupe_names(self.policies, "policy", f"domain '{self.domain}'")
        named_rels = [r for r in self.relationships if r.name]
        _dedupe_names(named_rels, "relationship", f"domain '{self.domain}'")
        if not self.id:
            self.id = slugify_domain(self.domain)
        return self


class OntologyCollision(BaseModel):
    """Represents a conflict or collision detected across domains."""
    item_type: str  # 'metric' or 'concept'
    item_name: str
    conflicting_domains: List[str]
    descriptions: Dict[str, Optional[str]] = Field(default_factory=dict)
    expressions: Dict[str, Optional[str]] = Field(default_factory=dict)
    severity: str = "WARNING"  # 'WARNING' or 'ERROR'
    message: str


class FederatedEnterpriseOntology(BaseModel):
    """Unified enterprise ontology composed of multiple domain ontologies."""
    domains: Dict[str, DomainOntology] = Field(default_factory=dict)
    concepts: Dict[str, DomainConcept] = Field(default_factory=dict)
    metrics: Dict[str, DomainMetric] = Field(default_factory=dict)
    relationships: List[DomainRelationship] = Field(default_factory=list)
    policies: Dict[str, DomainPolicy] = Field(default_factory=dict)
    collisions: List[OntologyCollision] = Field(default_factory=list)
    unresolved_references: List[str] = Field(default_factory=list)
