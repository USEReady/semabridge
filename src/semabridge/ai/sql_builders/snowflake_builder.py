"""Snowflake Truth SQL Builder."""
from __future__ import annotations

from typing import List, Union
from semabridge.ai.sql_builders.base import TruthSQLBuilder
from semabridge.reconciliation.golden_verifier import _format_sql_identifier


class SnowflakeTruthSQLBuilder(TruthSQLBuilder):
    platform: str = "SNOWFLAKE"

    def quote_identifier(self, name: str) -> str:
        s = str(name or "").strip()
        parts = [p.strip().strip('"').strip('`') for p in s.split(".") if p.strip()]
        if len(parts) >= 4 and parts[:2] == parts[2:4]:
            s = ".".join(parts[2:])
        return _format_sql_identifier(s)

    def _format_dimensions(self, dimensions: Union[List[str], str]) -> str:
        if isinstance(dimensions, (list, tuple)):
            dims = [self.quote_identifier(d) for d in dimensions if d]
            return ", ".join(dims)
        return self.quote_identifier(str(dimensions))

    def build_total(self, view: str, metric: str) -> str:
        clean_view = self.quote_identifier(view)
        clean_metric = self.quote_identifier(metric)
        return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric});"

    def build_sliced(self, view: str, metric: str, dimensions: Union[List[str], str]) -> str:
        clean_view = self.quote_identifier(view)
        clean_metric = self.quote_identifier(metric)
        dims_str = self._format_dimensions(dimensions)
        if not dims_str:
            return self.build_total(view, metric)
        return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric} DIMENSIONS {dims_str});"

    def build_top_n(self, view: str, metric: str, dimensions: Union[List[str], str], n: int = 5) -> str:
        clean_view = self.quote_identifier(view)
        clean_metric = self.quote_identifier(metric)
        dims_str = self._format_dimensions(dimensions)
        if not dims_str:
            return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric}) ORDER BY {clean_metric} DESC LIMIT {n};"
        return f"SELECT * FROM SEMANTIC_VIEW({clean_view} METRICS {clean_metric} DIMENSIONS {dims_str}) ORDER BY {clean_metric} DESC LIMIT {n};"
