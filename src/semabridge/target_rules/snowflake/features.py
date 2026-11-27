"""SemaBridge features implemented for Snowflake: all of today's Snowflake
features (live SQL, metric SQL editing, parity, row access policies, reverse
sync, knowledge-graph DDL). Declaring them changes no behaviour."""

from __future__ import annotations

from semabridge.target_rules.base import TargetFeatures

SNOWFLAKE_FEATURES = TargetFeatures(
    live_query=True,
    edit_metric_sql=True,
    parity_check=True,
    row_level_security=True,
    reverse_sync=True,
    knowledge_graph_sql=True,
    sql_label="Snowflake SQL",
)
