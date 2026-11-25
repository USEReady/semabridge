"""Stage 7: what must be proven after deploy (3.9), hand-off notes (3.8, 3.11),
and the final self-check that refuses to emit an inconsistent plan."""

from __future__ import annotations

from typing import Any, Dict, List, Set

from semabridge.target_backends.databricks.planner import sqltext
from semabridge.target_backends.databricks.planner.anchors import Decision
from semabridge.target_backends.databricks.planner.names import check_name
from semabridge.target_backends.databricks.planner.model import (
    PlannerInvariantError,
    PlannerResult,
    PlannerSettings,
    ViewPlan,
)

_PROPAGATED = {"conversion_failed", "unrecognised_pattern", "analysis_mismatch", "filter_removal_unsupported",
               "filter_semantics_differ", "dependency_cycle", "depends_on"}


def view_checks(view: ViewPlan) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    for j in view.joins:
        keys = [c for _, c in j.on] if j.cardinality == "many_to_one" else [p for p, _ in j.on]
        one_side = j.dataset if j.cardinality == "many_to_one" else view.source_dataset
        checks.append({"kind": "key_unique", "table": one_side, "columns": keys, "join": j.name})
        checks.append({"kind": "orphan_keys", "join": j.name})
        checks.append({"kind": "null_keys", "join": j.name})
        checks.append({"kind": "key_types", "join": j.name, "types": [list(t) for t in j.key_types]})
    for m in view.measures:
        checks.append({"kind": "grand_total_vs_reference", "measure": m.name, "grain": m.grain})
        checks.append({"kind": "grouped_total_vs_reference", "measure": m.name})
        if m.placement == "B":
            checks.append({"kind": "composed_equals_parts", "measure": m.name})
        if m.semantic_status == "approximate":
            checks.append({"kind": "conflicting_filter", "measure": m.name})
        if m.readiness == "structure_only" or "MAX(" in m.expr and not sqltext.REF.search(m.expr):
            checks.append({"kind": "zero_row_behaviour", "measure": m.name})
    return checks


def result_notes(result: PlannerResult, settings: PlannerSettings, two_way: List[str], skipped: List[str]) -> List[str]:
    notes: List[str] = []
    if settings.security_roles is None:
        notes.append("security: Power BI roles were not read for this run; deploy (3.8) must confirm there are none "
                     "or have a Databricks access design before publishing")
    elif settings.security_roles > 0:
        notes.append(f"security: {settings.security_roles} Power BI role(s) found and NOT migrated; deploy (3.8) "
                     "is blocked until a Databricks access design exists")
    notes += [f"relationship skipped: {s}" for s in skipped]
    notes += [f"two-way filtering: {s}" for s in two_way]
    approx = sum(1 for v in result.views for m in v.measures if m.semantic_status == "approximate")
    if approx:
        notes.append(f"genie: {approx} approximate measure(s) are exposed with a warning comment; the user can "
                     "untick them or switch to strict mode and re-plan")
    hidden = sum(1 for v in result.views for m in v.measures if m.readiness == "structure_only" or m.source_verification == "unverified")
    hidden += sum(1 for v in result.views for f in v.fields if f.readiness == "structure_only" or f.source_verification == "unverified")
    if hidden:
        notes.append(f"genie: {hidden} stub-only or unverified object(s) are kept out of Genie until verified")
    return notes


def self_check(result: PlannerResult, decisions: Dict[str, Decision]) -> None:
    errors: List[str] = []
    placed: Dict[str, str] = {}
    left = {lo.measure: lo for lo in result.left_out}
    for v in result.views:
        names: Set[str] = set()
        paths = {"source"} | {j.name for j in v.joins}
        one_to_many = {j.name for j in v.joins if j.cardinality == "one_to_many"}
        for j in v.joins:
            if j.cardinality == "one_to_many" and j.parent != "source":
                errors.append(f"{v.name}: one-to-many join {j.name} is not directly under the source")
            if j.parent not in paths:
                errors.append(f"{v.name}: join {j.name} has unknown parent {j.parent}")
        for f in v.fields:
            if f.path in one_to_many:
                errors.append(f"{v.name}: field {f.name} comes from a one-to-many table")
            if f.path not in paths:
                errors.append(f"{v.name}: field {f.name} uses unknown path {f.path}")
        join_aliases = {j.name.split(".")[-1].lower() for j in v.joins}
        for obj in list(v.fields) + list(v.measures):
            if obj.name.upper() in names:
                errors.append(f"{v.name}: duplicate name {obj.name}")
            names.add(obj.name.upper())
            problem = check_name(obj.name, getattr(obj, "name_generated", True))
            if problem:
                errors.append(f"{v.name}: {problem}")
            if obj.name.lower() in join_aliases or obj.name.lower() == "source":
                errors.append(f"{v.name}: {obj.name} is also a join alias")
            if obj.source_id is None:
                errors.append(f"{v.name}: {obj.name} has no source identity")
        for m in v.measures:
            if m.source_name in placed:
                errors.append(f"{m.source_name} placed in both {placed[m.source_name]} and {v.name}")
            placed[m.source_name] = v.name
            if m.source_name in left:
                errors.append(f"{m.source_name} is both placed and left out")
            d = decisions[m.source_name]
            sem = d.semantics
            if m.placement == "A" and sem is not None and sem.grains and sem.grains != {v.source_dataset}:
                errors.append(f"{v.name}: {m.source_name} counts {sorted(sem.grains)}, not the source")
            if m.placement == "B":
                if one_to_many == set() or sem is None:
                    errors.append(f"{v.name}: composed {m.source_name} without one-to-many joins")
                else:
                    for a in sem.aggregates:
                        if set(a.value_tables) != {a.grain}:
                            errors.append(f"{v.name}: composed {m.source_name} aggregate reads several tables")
            for a in sqltext.aliases(m.expr):
                if a.upper() != "SOURCE":   # every other reference must be a rewritten, quoted join path
                    errors.append(f"{v.name}: {m.source_name} reads {a}, not a join of this view")
            for dep in m.depends_on:
                lo = left.get(dep)
                if lo is not None and lo.reason in _PROPAGATED:
                    errors.append(f"{m.source_name} depends on left-out {dep} ({lo.reason})")
            if not m.semantic_status or not m.source_verification or not m.readiness:
                errors.append(f"{m.source_name} has no status")
    for v in result.views:
        problem = check_name(v.name, True) if v.name.endswith("_metrics") else check_name(v.name, False)
        if problem:
            errors.append(problem)
    view_names = [v.name.upper() for v in result.views]
    if len(view_names) != len(set(view_names)):
        errors.append(f"duplicate view names: {sorted(view_names)}")
    for name in decisions:
        if name not in placed and name not in left:
            errors.append(f"{name} is neither placed nor left out")
    if len(placed) + len(left) != len(decisions):
        errors.append(f"{len(placed)} placed + {len(left)} left out != {len(decisions)} measures")
    if errors:
        raise PlannerInvariantError("planner self-check failed:\n  " + "\n  ".join(errors))
