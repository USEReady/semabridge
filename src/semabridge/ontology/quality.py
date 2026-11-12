"""Ontology quality checks: duplicate formulas, conflicts, glossary coverage, health score.

Two consumers:

* the new-project wizard's Ontology step (``analyze_source_metrics``): BEFORE the
  dry run, compare the measures read from the selected source models with each
  other and with the existing federated ontology, so duplicates get merged and
  conflicting definitions get resolved once instead of being migrated N times;
* the Projects list (``project_ontology_health``): a per-project 0-100 score
  built from the domains attached to (or bootstrapped from) that project.

Everything here is pure (no I/O) so it can be unit-tested directly.
"""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from semabridge.ontology.models import DomainMetric, DomainOntology, FederatedEnterpriseOntology

# ---------------------------------------------------------------------------
# Formula normalisation
# ---------------------------------------------------------------------------

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(//|--)[^\n]*")
# 'Sales Table'[Amount] -> sales table[amount] (quotes around a DAX table name are optional)
_QUOTED_TABLE = re.compile(r"'([^']+)'\s*\[")
_WS = re.compile(r"\s+")

# Formulas too generic to call two metrics "the same business metric".
_TRIVIAL = {"", '""', "''", "count(*)", "countrows()", "1", "0", "blank()"}


def normalize_expression(expr: Optional[str]) -> str:
    """Canonical form of a DAX/SQL formula for equality checks.

    Removes comments, a leading '=', optional quotes around DAX table names,
    all whitespace, and case. Two formulas with the same normal form compute
    the same thing for the purpose of duplicate detection.
    """
    text = str(expr or "")
    text = _BLOCK_COMMENT.sub("", text)
    text = _LINE_COMMENT.sub("", text)
    text = text.strip()
    if text.startswith("="):
        text = text[1:]
    text = _QUOTED_TABLE.sub(lambda m: m.group(1) + "[", text)
    text = _WS.sub("", text)
    return text.casefold()


def is_trivial(normalized: str) -> bool:
    return normalized in _TRIVIAL


# ---------------------------------------------------------------------------
# Source metric records
# ---------------------------------------------------------------------------


@dataclass
class SourceMetric:
    """One measure as read from one source model (e.g. one .pbix file)."""

    name: str
    expression: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    dataset: Optional[str] = None
    source: Optional[str] = None  # model / file the measure came from
    normalized: str = field(init=False)

    def __post_init__(self) -> None:
        self.normalized = normalize_expression(self.expression)

    @property
    def display(self) -> str:
        return self.label or self.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.display,
            "expression": self.expression,
            "description": self.description,
            "dataset": self.dataset,
            "source": self.source,
        }


def source_metrics_from_osi(model: Any, source: Optional[str] = None) -> List[SourceMetric]:
    """Flatten an OSIModel's metrics into SourceMetric records."""
    out: List[SourceMetric] = []
    src = source or getattr(model, "label", None) or getattr(model, "unique_name", None)
    for m in getattr(model, "metrics", None) or []:
        expr = getattr(m, "expression", None)
        if not expr and getattr(m, "source_column", None):
            agg = str(getattr(m, "aggregation", None) or "sum").upper()
            expr = f"{agg}({getattr(m, 'dataset', '')}[{m.source_column}])"
        out.append(
            SourceMetric(
                name=m.unique_name,
                expression=expr,
                label=getattr(m, "label", None) or None,
                description=getattr(m, "description", None) or None,
                dataset=getattr(m, "dataset", None),
                source=src,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Existing-ontology index
# ---------------------------------------------------------------------------


@dataclass
class _Governed:
    domain: str
    owner: Optional[str]
    metric: DomainMetric


def _index_ontology(fed: Optional[FederatedEnterpriseOntology]) -> Dict[str, _Governed]:
    """casefolded name / synonym / maps_to -> governing metric (first domain wins)."""
    index: Dict[str, _Governed] = {}
    if fed is None:
        return index
    for dom in fed.domains.values():
        for met in dom.metrics:
            if met.stale:
                continue
            entry = _Governed(domain=dom.domain, owner=dom.owner, metric=met)
            keys = [met.name, met.label or "", *met.synonyms]
            for binding in met.maps_to:
                keys.append(binding)
                keys.append(binding.split(".")[-1])
            for key in keys:
                k = str(key or "").strip().casefold()
                if k and k not in index:
                    index[k] = entry
    return index


def _lookup(index: Dict[str, _Governed], metric: SourceMetric) -> Optional[_Governed]:
    for key in (metric.name, metric.label or ""):
        hit = index.get(str(key or "").strip().casefold())
        if hit is not None:
            return hit
    return None


def suggest_metric_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return slug or "metric"


# ---------------------------------------------------------------------------
# Wizard analysis
# ---------------------------------------------------------------------------


def suggest_metric_description(name: str, expression: str = "", source: str = "") -> str:
    """Generate a concise, editable business description from a metric's name and formula."""
    raw_name = (name or "").strip()
    human = re.sub(r"[_\-.]+", " ", raw_name)
    human = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", human).strip()
    human = re.sub(r"\s+", " ", human)
    expr = (expression or "").strip()
    upper = expr.upper()

    agg = "Calculated metric"
    if "DISTINCTCOUNT" in upper or "COUNTD(" in upper or "COUNT(DISTINCT" in upper:
        agg = "Distinct count"
    elif "DIVIDE(" in upper or "/" in expr:
        agg = "Ratio / rate"
    elif "AVERAGE" in upper or "AVG(" in upper:
        agg = "Average value"
    elif "SUM(" in upper or "SUMX(" in upper:
        agg = "Total sum"
    elif "COUNT" in upper:
        agg = "Total count"
    elif "MIN(" in upper:
        agg = "Minimum value"
    elif "MAX(" in upper:
        agg = "Maximum value"

    col_match = re.search(r"\[([^\]]+)\]", expr)
    col_hint = col_match.group(1).replace("_", " ").strip() if col_match else ""
    src_hint = f" (from {source})" if source else ""

    if col_hint and col_hint.lower() not in human.lower():
        return f"{agg} of {col_hint} for {human}{src_hint}."
    return f"{agg} for {human}{src_hint}."


def analyze_source_metrics(
    metrics: Sequence[SourceMetric],
    fed: Optional[FederatedEnterpriseOntology] = None,
) -> Dict[str, Any]:
    """Compare source measures with each other and with the existing ontology.

    Returns a JSON-friendly dict with ``summary``, ``duplicates``, ``conflicts``,
    ``glossary_gaps``, ``governed`` and ``unassigned``.

    * duplicate group: >1 measure with the same (non-trivial) normalised formula.
      ``kind`` is ``same_name`` when every copy shares one name (the same measure
      copied into several reports) or ``different_names`` otherwise;
    * conflict: the same name with different formulas, either between source
      models (``kind='source'``) or against a governed ontology metric
      (``kind='ontology'``);
    * governed: the measure matches an ontology metric with the same formula
      (or the ontology entry has no formula and only annotates it);
    * glossary gap: a business metric with no description in the source and none
      inherited from the ontology.
    """
    index = _index_ontology(fed)

    # -- duplicate groups (by formula) ------------------------------------
    by_formula: "OrderedDict[str, List[SourceMetric]]" = OrderedDict()
    for m in metrics:
        if not is_trivial(m.normalized):
            by_formula.setdefault(m.normalized, []).append(m)

    duplicates: List[Dict[str, Any]] = []
    for norm, members in by_formula.items():
        if len(members) < 2:
            continue
        names = [m.display for m in members]
        distinct = list(OrderedDict.fromkeys(n.casefold() for n in names))
        most_common = Counter(names).most_common(1)[0][0]
        duplicates.append(
            {
                "id": f"dup_{len(duplicates) + 1}",
                "kind": "same_name" if len(distinct) == 1 else "different_names",
                "expression": members[0].expression,
                "suggested_name": most_common,
                "suggested_id": suggest_metric_id(most_common),
                "members": [m.to_dict() for m in members],
                "exact": True,
            }
        )

    # -- business metrics = one per formula group, plus trivial ones by name --
    business: "OrderedDict[str, SourceMetric]" = OrderedDict()
    for norm, members in by_formula.items():
        # Represent the group by a described copy when there is one.
        business[f"f:{norm}"] = next((m for m in members if m.description), members[0])
    for m in metrics:
        if is_trivial(m.normalized):
            business.setdefault(f"n:{m.name.casefold()}", m)

    # -- conflicts --------------------------------------------------------
    conflicts: List[Dict[str, Any]] = []
    by_name: "OrderedDict[str, List[SourceMetric]]" = OrderedDict()
    for m in metrics:
        by_name.setdefault(m.display.casefold(), []).append(m)
    for _key, members in by_name.items():
        formulas = OrderedDict()
        for m in members:
            formulas.setdefault(m.normalized, m)
        if len(formulas) > 1:
            conflicts.append(
                {
                    "id": f"conf_{len(conflicts) + 1}",
                    "kind": "source",
                    "name": members[0].display,
                    "variants": [m.to_dict() for m in formulas.values()],
                }
            )

    governed: List[Dict[str, Any]] = []
    governed_keys: set = set()
    conflicted_keys: set = set()
    seen_names: set = set()
    for m in metrics:
        key = m.display.casefold()
        if key in seen_names:
            continue
        seen_names.add(key)
        hit = _lookup(index, m)
        if hit is None:
            continue
        existing_norm = normalize_expression(hit.metric.sql_expression)
        if not existing_norm or existing_norm == m.normalized:
            governed_keys.add(key)
            governed.append(
                {
                    "name": m.display,
                    "domain": hit.domain,
                    "metric": hit.metric.name,
                    "has_description": bool(hit.metric.description or hit.metric.bi_description),
                }
            )
        else:
            conflicted_keys.add(key)
            conflicts.append(
                {
                    "id": f"conf_{len(conflicts) + 1}",
                    "kind": "ontology",
                    "name": m.display,
                    "existing": {
                        "domain": hit.domain,
                        "owner": hit.owner or hit.metric.data_steward,
                        "metric": hit.metric.name,
                        "expression": hit.metric.sql_expression,
                        "description": hit.metric.description,
                    },
                    "incoming": m.to_dict(),
                    "suggested_new_name": f"{suggest_metric_id(m.display)}_{suggest_metric_id(m.source or 'alt')}"[:64],
                }
            )

    # -- glossary gaps & unassigned (per business metric) -----------------
    glossary_gaps: List[Dict[str, Any]] = []
    unassigned: List[str] = []
    described = 0
    for m in business.values():
        key = m.display.casefold()
        hit = _lookup(index, m)
        inherited = bool(hit and (hit.metric.description or hit.metric.bi_description))
        if m.description or inherited:
            described += 1
        else:
            gap = m.to_dict()
            gap["suggested_description"] = suggest_metric_description(m.display, m.expression, m.source)
            glossary_gaps.append(gap)
        if key not in governed_keys and key not in conflicted_keys:
            unassigned.append(m.display)

    total_business = len(business)
    return {
        "summary": {
            "measures_found": len(metrics),
            "business_metrics": total_business,
            "duplicate_groups": len(duplicates),
            "duplicate_measures": sum(len(d["members"]) - 1 for d in duplicates),
            "conflicts": len(conflicts),
            "governed": len(governed),
            "glossary_described": described,
            "glossary_coverage_pct": round(100.0 * described / total_business, 1) if total_business else 100.0,
            "unassigned": len(unassigned),
            "sources": sorted({m.source for m in metrics if m.source}),
        },
        "duplicates": duplicates,
        "conflicts": conflicts,
        "governed": governed,
        "glossary_gaps": glossary_gaps,
        "unassigned": unassigned,
    }


# ---------------------------------------------------------------------------
# Applying wizard decisions to a draft domain
# ---------------------------------------------------------------------------


def apply_review_decisions(
    domain: DomainOntology,
    *,
    merges: Iterable[Dict[str, Any]] = (),
    conflicts: Iterable[Dict[str, Any]] = (),
    descriptions: Optional[Dict[str, str]] = None,
) -> Tuple[DomainOntology, Dict[str, List[str]]]:
    """Return a copy of ``domain`` with the Ontology-step decisions applied.

    * merge ``{"name": canonical, "members": [measure names]}``: one metric named
      ``name`` survives; it is bound (``maps_to``) to every member measure and the
      other names become synonyms, so every copy gets the same business name and
      description when the ontology is applied during a run;
    * conflict ``{"name": measure, "resolution": "use_existing"}``: the incoming
      metric is dropped from this domain (the governing domain's definition wins);
      ``"keep_new"`` + ``new_name`` renames it (bound to the original measure);
      ``"defer"`` leaves it (the cross-domain collision stays visible in Health);
    * descriptions ``{metric name: text}`` set business descriptions.
    """
    report: Dict[str, List[str]] = {"merged": [], "dropped": [], "renamed": [], "described": [], "skipped": []}
    metrics: List[DomainMetric] = [m.model_copy(deep=True) for m in domain.metrics]

    def find(name: str) -> Optional[int]:
        key = str(name or "").casefold()
        for i, met in enumerate(metrics):
            if met.name.casefold() == key or (met.label or "").casefold() == key:
                return i
        return None

    for merge in merges or ():
        canonical = str(merge.get("name") or "").strip()
        members = [str(x) for x in (merge.get("members") or []) if str(x).strip()]
        positions = [p for p in (find(n) for n in members) if p is not None]
        clash = find(canonical) if canonical else None
        if not canonical or not positions or (clash is not None and clash not in positions):
            report["skipped"].append(f"merge {canonical or members}")
            continue
        keep_pos = positions[0]
        keeper = metrics[keep_pos]
        bound = list(keeper.maps_to) or [keeper.name]
        synonyms = list(keeper.synonyms)
        description = keeper.description
        for p in positions[1:]:
            other = metrics[p]
            bound.extend(other.maps_to or [other.name])
            synonyms.extend([other.name, *(other.synonyms or []), *([other.label] if other.label else [])])
            description = description or other.description
        if keeper.name != canonical:
            synonyms.append(keeper.name)
        synonyms = [s for s in synonyms if s and s.casefold() != canonical.casefold()]
        metrics[keep_pos] = keeper.model_copy(
            update={
                "name": canonical,
                "label": keeper.label if keeper.name == canonical else canonical,
                "maps_to": list(OrderedDict.fromkeys(bound)),
                "synonyms": list(OrderedDict.fromkeys(synonyms)),
                "description": description,
                "origin": "user",
            }
        )
        for p in sorted(positions[1:], reverse=True):
            metrics.pop(p)
        report["merged"].append(f"{canonical} <- {', '.join(members)}")

    for conflict in conflicts or ():
        name = str(conflict.get("name") or "").strip()
        resolution = str(conflict.get("resolution") or "defer").strip().lower()
        pos = find(name)
        if pos is None:
            report["skipped"].append(f"conflict {name}")
            continue
        if resolution == "use_existing":
            metrics.pop(pos)
            report["dropped"].append(name)
        elif resolution == "keep_new":
            new_name = str(conflict.get("new_name") or "").strip()
            if not new_name or find(new_name) is not None:
                report["skipped"].append(f"rename {name} -> {new_name or '?'}")
                continue
            met = metrics[pos]
            metrics[pos] = met.model_copy(
                update={"name": new_name, "maps_to": list(met.maps_to) or [met.name], "origin": "user"}
            )
            report["renamed"].append(f"{name} -> {new_name}")

    def find_bound(name: str) -> Optional[int]:
        """By name, else the metric a merge/rename bound this measure name to."""
        pos = find(name)
        if pos is not None:
            return pos
        key = str(name or "").casefold()
        for i, met in enumerate(metrics):
            if key in {b.casefold() for b in met.maps_to} or key in {s.casefold() for s in met.synonyms}:
                return i
        return None

    for name, text in (descriptions or {}).items():
        pos = find_bound(name)
        if pos is None or not str(text or "").strip():
            continue
        metrics[pos] = metrics[pos].model_copy(update={"description": str(text).strip()})
        report["described"].append(name)

    return domain.model_copy(update={"metrics": metrics}), report


# ---------------------------------------------------------------------------
# Per-project health score
# ---------------------------------------------------------------------------


def project_ontology_health(
    domains: Sequence[DomainOntology],
    fed: Optional[FederatedEnterpriseOntology] = None,
) -> Dict[str, Any]:
    """Health of the ontology behind one project (its attached/bootstrapped domains).

    score (0-100) = 70% glossary coverage + 30% stewardship coverage, minus
    5 points per duplicate-formula group (max 25), 10 per conflicting definition
    (max 30) and 2 per broken reference (max 10). ``None`` when there is no
    ontology yet.
    """
    if not domains:
        return {
            "status": "none",
            "score": None,
            "domains": [],
            "metric_count": 0,
            "described": 0,
            "glossary_coverage_pct": None,
            "duplicate_groups": 0,
            "duplicate_examples": [],
            "conflicts": 0,
            "conflict_examples": [],
            "unresolved": 0,
            "stale": 0,
        }

    names = {d.domain for d in domains}
    live: List[Tuple[DomainOntology, DomainMetric]] = [
        (d, m) for d in domains for m in d.metrics if not m.stale
    ]
    total = len(live)
    described = sum(1 for _d, m in live if (m.description or m.bi_description))
    stewarded = sum(1 for d, m in live if (m.data_steward or d.owner))
    stale = sum(1 for d in domains for m in d.metrics if m.stale)

    groups: Dict[str, List[str]] = {}
    for _d, m in live:
        norm = normalize_expression(m.sql_expression)
        if not is_trivial(norm):
            groups.setdefault(norm, []).append(m.label or m.name)
    dup_groups = [members for members in groups.values() if len(members) > 1]

    conflict_items: List[str] = []
    unresolved = 0
    if fed is not None:
        for c in fed.collisions:
            if c.item_type == "metric" and c.severity == "ERROR" and names.intersection(c.conflicting_domains):
                conflict_items.append(c.item_name)
        own_metrics = {m.name for _d, m in live}
        own_concepts = {c.name for d in domains for c in d.concepts}
        for ref in fed.unresolved_references:
            if any(f"'{n}'" in ref for n in own_metrics | own_concepts):
                unresolved += 1

    coverage = described / total if total else 1.0
    stewardship = stewarded / total if total else 1.0
    score = 100.0 * (0.7 * coverage + 0.3 * stewardship)
    score -= min(25, 5 * len(dup_groups))
    score -= min(30, 10 * len(conflict_items))
    score -= min(10, 2 * unresolved)
    score = int(round(max(0.0, min(100.0, score))))

    return {
        "status": "good" if score >= 85 else ("fair" if score >= 60 else "poor"),
        "score": score,
        "domains": [{"id": d.id, "domain": d.domain, "owner": d.owner} for d in domains],
        "metric_count": total,
        "described": described,
        "glossary_coverage_pct": round(100.0 * coverage, 1),
        "stewardship_pct": round(100.0 * stewardship, 1),
        "duplicate_groups": len(dup_groups),
        "duplicate_examples": [" = ".join(g[:3]) for g in dup_groups[:3]],
        "conflicts": len(conflict_items),
        "conflict_examples": conflict_items[:3],
        "unresolved": unresolved,
        "stale": stale,
    }
