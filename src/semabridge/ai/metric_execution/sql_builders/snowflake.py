"""Snowflake Semantic View SQL Builder for Metric Comparison."""
from __future__ import annotations

from typing import Any, Dict, Optional

from semabridge.ai.metric_execution.sql_builders.base import TargetSQLBuilder
from semabridge.ai.sql_builders.snowflake_builder import SnowflakeTruthSQLBuilder


class SnowflakeMetricSQLBuilder(TargetSQLBuilder):
    """Generates Snowflake SEMANTIC_VIEW syntax for metric execution."""

    platform: str = "SNOWFLAKE"

    def __init__(self) -> None:
        self._builder = SnowflakeTruthSQLBuilder()

    def build_metric_query(
        self,
        view: str,
        metric: str,
        slice_filters: Optional[Dict[str, Any]] = None,
    ) -> str:
        clean_view = self._builder.quote_identifier(view)
        clean_metric = self._builder.quote_identifier(metric)

        if not slice_filters:
            return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric});"

        # When slice filters are present, we can filter by WHERE clause or DIMENSIONS
        # Semantic view queries support: SELECT * FROM SEMANTIC_VIEW(view METRICS metric) WHERE col = val
        where_clauses = []
        for col, val in slice_filters.items():
            col_ident = self._builder.quote_identifier(col)
            if val is None:
                where_clauses.append(f"{col_ident} IS NULL")
            elif isinstance(val, (int, float)):
                where_clauses.append(f"{col_ident} = {val}")
            else:
                escaped = str(val).replace("'", "''")
                where_clauses.append(f"{col_ident} = '{escaped}'")

        where_str = " AND ".join(where_clauses)
        return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric}) WHERE {where_str};"
