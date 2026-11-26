"""What Databricks metric views can express: starting values.

Cautious until the capability probes (Phase 3.5) confirm them against the
connected workspace:

* text_measures      -- off: measures are kept numeric, as for Snowflake.
* window_metrics     -- off: metric views have window measures (window:),
                        but in a different shape from the renderer's
                        OVER (PARTITION BY EXCLUDING ...); such measures are
                        declined until the planner supports window: specs.
* nested_joins       -- on: documented from Databricks Runtime 17.3; SQL
                        warehouses run current Databricks SQL.
* bridge_views       -- on: the planner keeps two-fact measures through
                        helper (bridge) views.
* enriched_helper_columns -- off: Snowflake-only enriched-view helper columns.
"""

from __future__ import annotations

from semabridge.target_rules.base import TargetCapabilities

DATABRICKS_CAPABILITIES = TargetCapabilities(
    text_measures=False,
    window_metrics=False,
    nested_joins=True,
    bridge_views=True,
    enriched_helper_columns=False,
)
