"""Stage 2: one view per source table, filtered by the view mode."""

from __future__ import annotations

from typing import Dict, List

from semabridge.target_backends.databricks.planner.anchors import Decision
from semabridge.target_backends.databricks.planner.graph import ModelGraph
from semabridge.target_backends.databricks.planner.model import (
    Capability,
    LeftOut,
    PlannerError,
    PlannerSettings,
)


def view_name(graph: ModelGraph, source: str) -> str:
    """The natural view name (allocation against other views happens in names.py)."""
    from semabridge.target_backends.databricks.planner.names import NAMING

    return f"{NAMING.normalize(source, 'view', ('view', source))[0]}_metrics"


def group(decisions: Dict[str, Decision], graph: ModelGraph, settings: PlannerSettings) -> Dict[str, List[Decision]]:
    """{source table: placed decisions}. Decisions moved out here get their left_out set."""
    if settings.view_mode == "primary_fact_only":
        if not settings.primary_fact or graph.table(settings.primary_fact) is None:
            raise PlannerError("view mode primary_fact_only needs the user to choose the primary fact table")
        allowed = {graph.table(settings.primary_fact)}
    elif settings.view_mode == "selected_facts":
        allowed = {graph.table(t) for t in settings.selected_tables if graph.table(t)}
        if not allowed:
            raise PlannerError("view mode selected_facts needs at least one selected table")
    else:
        allowed = None

    by_source: Dict[str, List[Decision]] = {}
    for d in decisions.values():
        if d.left_out is not None or d.placement is None:
            continue
        if allowed is not None and d.view_source not in allowed:
            d.left_out = LeftOut(d.measure, "view_not_selected",
                                 f"its view ({view_name(graph, d.view_source)}) is not in the selected view mode")
            d.placement = None
            continue
        by_source.setdefault(d.view_source, []).append(d)

    # A and B measures share a root view only once that is proven (3.5 probe).
    if not settings.has(Capability.MIXED_ROOT_VIEWS):
        for source, ds in by_source.items():
            if any(d.placement == "A" for d in ds) and any(d.placement == "B" for d in ds):
                for d in ds:
                    if d.placement == "B":
                        d.left_out = LeftOut(d.measure, "mixed_view_unproven",
                                             f"{view_name(graph, source)} also holds single-grain measures; "
                                             "mixing them with composed measures is not proven yet")
                        d.placement = None
                by_source[source] = [d for d in ds if d.placement is not None]
    return {s: ds for s, ds in by_source.items() if ds}
