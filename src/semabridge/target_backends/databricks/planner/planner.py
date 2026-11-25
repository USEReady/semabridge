"""Orchestration: Stage 0 to 7, then the self-check."""

from __future__ import annotations

from typing import Any, Dict, Optional

from semabridge.target_backends.databricks.planner import anchors, checks, fields, joins, measures, names, requests, views
from semabridge.target_backends.databricks.planner.bindings import Bindings
from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import (
    LeftOut,
    MeasurePlan,
    PlannerResult,
    PlannerSettings,
    ViewPlan,
)
from semabridge.target_backends.databricks.planner.semantics import SemanticsReader


def plan_metric_views(sml_model: Any, table_plan: Optional[Any] = None,
                      settings: Optional[PlannerSettings] = None) -> PlannerResult:
    """Plan the metric views for `sml_model` (Step 6 measures, after 3.4's column
    rewrite). `table_plan` is 3.4's TablePlan, or None when no connection exists."""
    settings = settings or PlannerSettings()
    graph = ModelGraph(sml_model)
    bindings = Bindings(table_plan)
    reader = SemanticsReader(graph)
    result = PlannerResult(settings=settings)

    decisions = anchors.decide_all(graph, reader, bindings, settings)          # Stages 0-1
    grouped = views.group(decisions, graph, settings)                          # Stage 2

    placed: Dict[str, MeasurePlan] = {}
    order = list(graph.measures)
    measure_order = {m: i for i, m in enumerate(order)}
    name_requests, unresolved = requests.resolve(settings, graph)                  # saved names -> identities
    events: list = []
    vnames, vrenames = names.view_names(list(grouped), settings.reserved_view_names)   # Stage 6 (views)
    result.renames += vrenames
    for source in sorted(grouped, key=lambda s: vnames[s]):
        ds = sorted(grouped[source], key=lambda d: order.index(d.measure))
        join_list, where, notes, blocked = joins.build_joins(source, ds, graph, bindings)   # Stage 3
        kept = []
        for d in ds:
            needs = set(d.semantics.tables) if d.semantics is not None else set()
            missing = sorted(t for t in needs if t not in where)
            if missing:
                why = next((blocked[t] for t in missing if t in blocked), "needs_composition")
                d.left_out = LeftOut(d.measure, why, f"needs {missing}, not joinable in {vnames[source]}")
                d.placement = None
            else:
                kept.append(d)
        if not kept:
            continue
        field_list, field_notes = fields.build_fields(source, join_list, where, graph, bindings)  # Stage 4
        view = ViewPlan(
            name=vnames[source],
            source_dataset=source,
            source_table=bindings.uc_table(source),
            version="1.1",  # display_name / synonyms need YAML 1.1 (Runtime 17.3+)
            readiness=bindings.readiness(source),
            joins=join_list,
            fields=field_list,
            notes=notes + field_notes,
        )
        view.measures = measures.build_measures(source, kept, where, graph, bindings, settings, placed)  # Stage 5
        reserved = (settings.reserved_names or {}).get(source, {})
        result.renames += names.assign_names(view, graph, reserved, [j.name.split(".")[-1] for j in join_list],
                                             measure_order, name_requests, events)         # Stage 6
        if any(j.readiness == "structure_only" for j in join_list) or view.readiness == "structure_only":
            stubs = sorted({j.dataset for j in join_list if j.readiness == "structure_only"} |
                           ({source} if view.readiness == "structure_only" else set()))
            view.notes.append(f"partly stub: {', '.join(stubs)} {'has' if len(stubs) == 1 else 'have'} no data yet; their fields are kept out of Genie")
        result.views.append(view)

    measures.propagate(placed, anchors._order(graph, reader))
    for view in result.views:
        view.checks = checks.view_checks(view)                                             # Stage 7
    for name in order:
        d = decisions[name]
        if d.left_out is not None:
            result.left_out.append(d.left_out)
    result.name_requests, name_blockers = requests.outcomes(name_requests, unresolved, events)
    result.blockers = name_blockers + checks.security_blockers(settings)
    result.notes = checks.result_notes(result, settings, graph.two_way_notes, graph.skipped_relationships)
    checks.self_check(result, decisions)
    return result
