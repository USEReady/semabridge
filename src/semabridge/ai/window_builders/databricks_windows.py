"""Databricks Truth Window Builder."""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from semabridge.ai.sql_builders.databricks_builder import DatabricksTruthSQLBuilder
from semabridge.ai.window_builders.base import WindowBuilder

_TAIL_RE = re.compile(r"\b(GROUP\s+BY|HAVING|QUALIFY|ORDER\s+BY|LIMIT|OFFSET|FETCH)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_PLAIN_SELECT_RE = re.compile(r"^(?:SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_SQL_RE = re.compile(
    r"(;|\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|MERGE|EXEC|EXECUTE|UNION|SELECT)\b|--|/\*|\*/)",
    re.IGNORECASE,
)
_FILTER_PARTS_RE = re.compile(
    r"^([A-Za-z0-9_\"`]+(?:\.[A-Za-z0-9_\"`]+)?)\s*((?:=|!=|<>|>=|<=|>|<|LIKE|ILIKE|NOT\s+LIKE|IS|IS\s+NOT|IN|BETWEEN)\s*[^;]*)$",
    re.IGNORECASE,
)


def _strip_leading_comments(sql: str) -> str:
    cleaned = sql
    while True:
        trimmed = cleaned.lstrip()
        if trimmed.startswith("--"):
            _, _, cleaned = trimmed.partition("\n")
        elif trimmed.startswith("/*"):
            end = trimmed.find("*/")
            if end != -1:
                cleaned = trimmed[end + 2 :]
            else:
                break
        else:
            break
    return cleaned.strip()


class DatabricksWindowBuilder(WindowBuilder):
    platform: str = "DATABRICKS"

    def supports_windows(self) -> Tuple[bool, str]:
        return True, ""

    def build(self, sql: Optional[str], window: Any) -> Optional[str]:
        if not sql or not str(sql).strip():
            return None

        if isinstance(window, dict):
            clause_str = str(window.get("filter_clause") or window.get("filter") or "").strip()
        elif hasattr(window, "filter_clause"):
            clause_str = str(window.filter_clause or "").strip()
        else:
            clause_str = str(window or "").strip()

        if not clause_str:
            return str(sql)

        text = str(sql).rstrip().rstrip(";")
        cleaned_sql = _strip_leading_comments(text)
        if not _PLAIN_SELECT_RE.match(cleaned_sql):
            return None

        if _FORBIDDEN_SQL_RE.search(clause_str):
            return None

        m = _FILTER_PARTS_RE.match(clause_str)
        if not m:
            return None

        raw_col, rest = m.group(1), m.group(2).strip()
        mapped_col = DatabricksTruthSQLBuilder().format_dimension(raw_col)
        if not mapped_col:
            return None
        mapped_clause = f"{mapped_col} {rest}"

        tail_match = _TAIL_RE.search(text)
        head = text[: tail_match.start()].rstrip() if tail_match else text
        tail = text[tail_match.start():] if tail_match else ""

        joiner = "AND" if _WHERE_RE.search(head) else "WHERE"
        windowed = f"{head} {joiner} ({mapped_clause})" if joiner == "AND" else f"{head} {joiner} {mapped_clause}"
        if tail:
            windowed = f"{windowed} {tail}"
        return windowed


# Backward-compatible alias
DatabricksTruthWindowBuilder = DatabricksWindowBuilder
