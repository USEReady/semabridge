"""Which platform's rules Step 6 translates measures with (Phase 3.2).

Step 6 writes each measure's SQL (``sql_expression``) and Step 7 saves it in
the snapshot, which the dry-run / Ontology / Comparator screens read. So the
SQL in the snapshot is in the target platform's dialect only if Step 6 uses
that platform's rules.

Databricks rules are used only when the run targets Databricks AND
``behavior.databricks.use_planner_path`` is on: the existing Databricks
publisher (the path while the switch is off) reads ``sql_expression`` itself
and must keep receiving what it gets today. Every other run returns None,
which means the converter's default, Snowflake: unchanged behaviour.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from semabridge.target_rules import TargetRules, get_target_rules

if TYPE_CHECKING:
    from semabridge.core.engine.context import RunContext


def databricks_planner_enabled(context: "RunContext") -> bool:
    """True when this run targets Databricks with the planner path switched on."""
    if str(getattr(context, "target_type", None) or "").strip().lower() != "databricks":
        return False
    databricks = getattr(getattr(context, "behavior", None), "databricks", None)
    return bool(getattr(databricks, "use_planner_path", False))


def conversion_rules(context: "RunContext") -> Optional[TargetRules]:
    """Rules for Step 6 to translate with; None keeps the default (Snowflake)."""
    if databricks_planner_enabled(context):
        return get_target_rules("databricks")
    return None
