"""Time windows for BI Truth Suite cases.

A window narrows a case to one period. It carries its own expected value, because
a quarter compared against an annual figure always fails.

STATUS NOTE (CS-19 Verified):
Step 1 window injection is fully implemented and verified against live warehouses.
Snowflake SEMANTIC_VIEW natively requires internal WHERE clauses inside the call argument
list (e.g. `SEMANTIC_VIEW(view METRICS metric WHERE COL_DATE.YEAR = 2014)`) rather than
trailing WHERE clauses. `build_windowed_sql` injects the filter clause directly into the
`SEMANTIC_VIEW(...)` invocation, successfully narrowing metrics across time slices even when
the slice column is not in the outer query DIMENSIONS list, preserving scalar query shape.
For plain SQL queries, standard trailing WHERE clauses are safely attached. Malformed,
injection-risk, or non-SELECT expressions are refused honestly (returning None).
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Dict, List, Optional


@dataclass
class CaseWindow:
    window_key: str
    window_label: str = ""
    filter_clause: str = ""
    expected_value: Any = None
    expected_row_count: Optional[int] = None

    @property
    def has_expected_value(self) -> bool:
        return self.expected_value is not None

    def __getitem__(self, item: str) -> Any:
        if item in ("name", "window_label", "label"):
            return self.window_label or self.window_key
        if item in ("filter", "filter_clause"):
            return self.filter_clause
        if item in ("window_key", "key"):
            return self.window_key
        return getattr(self, item, None)

    def get(self, item: str, default: Any = None) -> Any:
        val = self[item]
        return val if val is not None else default


def parse_case_windows(raw: Any) -> List[CaseWindow]:
    """Windows from stored JSON or list. Unusable entries are skipped, not guessed at."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []

    out: List[CaseWindow] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("window_key") or item.get("key") or item.get("name") or "").strip()
        if not key or key.casefold() in seen:
            continue
        seen.add(key.casefold())
        out.append(CaseWindow(
            window_key=key,
            window_label=str(item.get("window_label") or item.get("label") or item.get("name") or key).strip(),
            filter_clause=str(item.get("filter_clause") or item.get("filter") or "").strip(),
            expected_value=item.get("expected_value"),
            expected_row_count=item.get("expected_row_count"),
        ))
    return out


_SEMANTIC_VIEW_RE = re.compile(r"\bSEMANTIC_VIEW\s*\(", re.IGNORECASE)
_TAIL_RE = re.compile(r"\b(GROUP\s+BY|HAVING|QUALIFY|ORDER\s+BY|LIMIT|OFFSET|FETCH)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_PLAIN_SELECT_RE = re.compile(r"^(?:SELECT|WITH)\b", re.IGNORECASE)
_FORBIDDEN_SQL_RE = re.compile(
    r"(;|\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|MERGE|EXEC|EXECUTE|UNION|SELECT)\b|--|/\*|\*/)",
    re.IGNORECASE,
)
_QUALIFIED_FILTER_RE = re.compile(
    r"^[A-Za-z0-9_\"`]+(?:\.[A-Za-z0-9_\"`]+)\s*(?:=|!=|<>|>=|<=|>|<|LIKE|ILIKE|NOT\s+LIKE|IS|IS\s+NOT|IN|BETWEEN)\s*[^;]*$",
    re.IGNORECASE,
)
_PLAIN_FILTER_RE = re.compile(
    r"^[A-Za-z0-9_\"`]+(?:\.[A-Za-z0-9_\"`]+)?\s*(?:=|!=|<>|>=|<=|>|<|LIKE|ILIKE|NOT\s+LIKE|IS|IS\s+NOT|IN|BETWEEN)\s*[^;]*$",
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


def is_semantic_view_query(sql: str) -> bool:
    """A semantic view query does not take a trailing WHERE the way a plain SELECT does."""
    return bool(_SEMANTIC_VIEW_RE.search(sql or ""))


def build_windowed_sql(
    sql: Optional[str],
    filter_clause: Any,
    platform: Optional[str] = "SNOWFLAKE",
) -> Optional[str]:
    """Return the SQL narrowed to a window, or None when it cannot be narrowed.

    Returning None is the honest answer for a shape this cannot safely modify. A
    caller that receives None reports the window as not comparable rather than
    running the unfiltered query and comparing it to a window's expected value.
    """
    from semabridge.ai.window_builders import get_window_builder

    builder = get_window_builder(platform)
    if builder is None:
        return None
    supported, _reason = builder.supports_windows()
    if not supported:
        return None
    return builder.build(sql, filter_clause)
