"""Databricks metric-view planner (Phase 3.6).

Decides, offline and read-only, how the converted Power BI measures become
Unity Catalog metric views: which views exist, and the joins, fields and
measures of each. Every measure is either placed where its numbers are
provably counted at the right grain, or left out with a reason. The plan is
consumed as-is by the YAML writer (3.7) and the deploy step (3.8).

Entry point: :func:`plan_metric_views`.
"""

from semabridge.target_backends.databricks.planner.model import (
    Capability,
    PlannerError,
    PlannerInvariantError,
    PlannerResult,
    PlannerSettings,
)
from semabridge.target_backends.databricks.planner.planner import plan_metric_views

__all__ = [
    "Capability",
    "PlannerError",
    "PlannerInvariantError",
    "PlannerResult",
    "PlannerSettings",
    "plan_metric_views",
]
