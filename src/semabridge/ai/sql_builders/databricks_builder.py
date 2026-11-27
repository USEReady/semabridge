"""Databricks Truth SQL Builder."""
from __future__ import annotations

import re
from typing import List, Union
from semabridge.ai.sql_builders.base import TruthSQLBuilder


def _sanitize_ident(value: str) -> str:
    """Mirror DatabricksPublisher._sanitize_identifier."""
    raw = str(value or "").strip().replace("`", "")
    safe = re.sub(r"[^0-9A-Za-z_]", "_", raw)
    safe = re.sub(r"_+", "_", safe).strip("_")
    return safe or "unnamed"


def _projection_prefix(table_name: str) -> str:
    """Mirror DatabricksPublisher._metric_view_projection_prefix."""
    raw = str(table_name or "").strip().replace("`", "")
    if not raw:
        return "col"
    sanitized = _sanitize_ident(raw)
    compact = re.sub(r"[^A-Z0-9]+", "", sanitized.upper())
    if not compact:
        return "col"
    return (compact[:4] or "col").lower()


class DatabricksTruthSQLBuilder(TruthSQLBuilder):
    platform: str = "DATABRICKS"

    def quote_identifier(self, name: str) -> str:
        s = str(name or "").strip()
        parts = [p.strip().strip("`").strip('"') for p in s.split(".") if p.strip()]
        if not parts:
            return ""
        # Deduplicate repeated catalog.schema prefix if present
        if len(parts) >= 4 and parts[:2] == parts[2:4]:
            parts = parts[2:]
        return ".".join(f"`{p.replace('`', '``')}`" for p in parts)

    def format_dimension(self, dim: str) -> str:
        """Map an ontology dimension (TABLE.COLUMN or column) to its deployed metric-view column name."""
        s = str(dim or "").strip().strip("`").strip('"')
        if not s:
            return ""
        if "." in s:
            tbl, col = s.split(".", 1)
            prefix = _projection_prefix(tbl)
            col_name = _sanitize_ident(col).lower()
            deployed = f"{prefix}_{col_name}"
        else:
            deployed = _sanitize_ident(s).lower()
        return self.quote_identifier(deployed)

    def _format_dimensions(self, dimensions: Union[List[str], str]) -> str:
        if isinstance(dimensions, (list, tuple)):
            dims = [self.format_dimension(d) for d in dimensions if d]
            return ", ".join(d for d in dims if d)
        return self.format_dimension(str(dimensions))

    def _clean_metric(self, metric: str) -> str:
        s = str(metric or "").strip().strip("`").strip('"')
        if "." in s:
            s = s.split(".")[-1]
        return self.quote_identifier(s)

    def build_total(self, view: str, metric: str) -> str:
        clean_view = self.quote_identifier(view)
        clean_metric = self._clean_metric(metric)
        return f"SELECT MEASURE({clean_metric}) FROM {clean_view};"

    def build_sliced(self, view: str, metric: str, dimensions: Union[List[str], str]) -> str:
        clean_view = self.quote_identifier(view)
        clean_metric = self._clean_metric(metric)
        dims_str = self._format_dimensions(dimensions)
        if not dims_str:
            return self.build_total(view, metric)
        return f"SELECT {dims_str}, MEASURE({clean_metric}) FROM {clean_view} GROUP BY {dims_str};"

    def build_top_n(self, view: str, metric: str, dimensions: Union[List[str], str], n: int = 5) -> str:
        clean_view = self.quote_identifier(view)
        clean_metric = self._clean_metric(metric)
        dims_str = self._format_dimensions(dimensions)
        if not dims_str:
            return f"SELECT MEASURE({clean_metric}) FROM {clean_view} ORDER BY MEASURE({clean_metric}) DESC LIMIT {n};"
        return f"SELECT {dims_str}, MEASURE({clean_metric}) FROM {clean_view} GROUP BY {dims_str} ORDER BY MEASURE({clean_metric}) DESC LIMIT {n};"
