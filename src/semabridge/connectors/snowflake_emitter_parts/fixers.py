"""Compatibility exports for older Snowflake emitter refactor imports."""

from __future__ import annotations

from semabridge.connectors.ddl_helpers import (
    deduplicate_metrics_lines,
    deduplicate_metrics_lines_osi,
    extract_expr_key,
    extract_expr_key_osi,
    fix_global_sums,
)
from semabridge.connectors.snowflake_emitter_parts.metric_helpers import (
    normalize_date_part_arguments,
    normalize_rolling_monthindex_max_predicates,
    prune_unresolved_metric_lines,
    sanitize_sql_markdown,
)

__all__ = [
    "deduplicate_metrics_lines",
    "deduplicate_metrics_lines_osi",
    "extract_expr_key",
    "extract_expr_key_osi",
    "fix_global_sums",
    "normalize_date_part_arguments",
    "normalize_rolling_monthindex_max_predicates",
    "prune_unresolved_metric_lines",
    "sanitize_sql_markdown",
]
