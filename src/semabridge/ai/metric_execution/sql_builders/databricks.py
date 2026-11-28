"""Databricks Metric View SQL Builder for Metric Comparison."""
from __future__ import annotations

from typing import Any, Dict, Optional

from semabridge.ai.metric_execution.sql_builders.base import TargetSQLBuilder
from semabridge.ai.sql_builders.databricks_builder import DatabricksTruthSQLBuilder


class DatabricksMetricSQLBuilder(TargetSQLBuilder):
    """Generates Databricks SELECT MEASURE(<measure>) syntax for metric execution."""

    platform: str = "DATABRICKS"

    def __init__(self) -> None:
        self._builder = DatabricksTruthSQLBuilder()

    def build_metric_query(
        self,
        view: str,
        metric: str,
        slice_filters: Optional[Dict[str, Any]] = None,
    ) -> str:
        clean_view = self._builder.quote_identifier(view)
        clean_metric = self._builder._clean_metric(metric)

        if not slice_filters:
            return f"SELECT MEASURE({clean_metric}) FROM {clean_view};"

        where_clauses = []
        for col, val in slice_filters.items():
            col_ident = self._builder.format_dimension(col)
            if val is None:
                where_clauses.append(f"{col_ident} IS NULL")
            elif isinstance(val, (int, float)):
                where_clauses.append(f"{col_ident} = {val}")
            else:
                escaped = str(val).replace("'", "''")
                where_clauses.append(f"{col_ident} = '{escaped}'")

        where_str = " AND ".join(where_clauses)
        return f"SELECT MEASURE({clean_metric}) FROM {clean_view} WHERE {where_str};"
