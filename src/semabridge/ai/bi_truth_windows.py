"""Time windows for BI Truth Suite cases.

A window narrows a case to one period. It carries its own expected value, because
a quarter compared against an annual figure always fails.

STATUS NOTE (Step 1 / CS-19):
Step 1 is built (parsing, storage, comparator integration, refusal handling).
However, build_windowed_sql currently refuses all SEMANTIC_VIEW queries (returning None)
because trailing WHERE clauses are rejected by Snowflake.
Live warehouse testing demonstrates that Snowflake SEMANTIC_VIEW natively supports
internal WHERE clauses inside the call (e.g. SEMANTIC_VIEW(... WHERE COL_DATE.YEAR = 2014)),
which successfully narrows metrics across time slices even when the slice column is not
in the outer DIMENSIONS list. Step 1 will become active for generated queries once internal
WHERE injection into the SEMANTIC_VIEW argument list is enabled.
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


def build_windowed_sql(sql: Optional[str], filter_clause: Any) -> Optional[str]:
    """Return the SQL narrowed to a window, or None when it cannot be narrowed.

    Returning None is the honest answer for a shape this cannot safely modify. A
    caller that receives None reports the window as not comparable rather than
    running the unfiltered query and comparing it to a window's expected value.
    """
    if not sql or not str(sql).strip():
        return None

    # Handle filter_clause passed as string, dict, or CaseWindow
    if isinstance(filter_clause, dict):
        clause_str = str(filter_clause.get("filter_clause") or filter_clause.get("filter") or "").strip()
    elif hasattr(filter_clause, "filter_clause"):
        clause_str = str(filter_clause.filter_clause or "").strip()
    else:
        clause_str = str(filter_clause or "").strip()

    if not clause_str:
        return str(sql)

    text = str(sql).rstrip().rstrip(";")

    # Refuse query shapes not recognised as a SELECT statement
    cleaned_sql = _strip_leading_comments(text)
    if not _PLAIN_SELECT_RE.match(cleaned_sql):
        return None

    # Refuse forbidden / injection patterns in the filter clause
    if _FORBIDDEN_SQL_RE.search(clause_str):
        return None

    if is_semantic_view_query(text):
        # A filter inside SEMANTIC_VIEW must be a simple qualified comparison (TABLE.DIMENSION <op> <val>)
        if not _QUALIFIED_FILTER_RE.match(clause_str):
            return None

        # Snowflake SEMANTIC_VIEW natively accepts an internal WHERE clause inside
        # the call argument list, narrowing the metric calculation over the slice.
        m = _SEMANTIC_VIEW_RE.search(text)
        if not m:
            return None
        start_paren = m.end() - 1
        depth = 0
        end_paren = -1
        for i in range(start_paren, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    end_paren = i
                    break
        if end_paren == -1:
            return None

        args = text[start_paren + 1 : end_paren].strip()
        joiner = "AND" if _WHERE_RE.search(args) else "WHERE"
        new_args = f"{args} {joiner} {clause_str}"
        return f"{text[:start_paren + 1]}{new_args}{text[end_paren:]}".strip()

    # Plain SELECT: must be a valid simple filter
    if not _PLAIN_FILTER_RE.match(clause_str):
        return None

    tail_match = _TAIL_RE.search(text)
    head = text[: tail_match.start()].rstrip() if tail_match else text
    tail = text[tail_match.start():] if tail_match else ""

    joiner = "AND" if _WHERE_RE.search(head) else "WHERE"
    windowed = f"{head} {joiner} ({clause_str})" if joiner == "AND" else f"{head} {joiner} {clause_str}"
    if tail:
        windowed = f"{windowed} {tail}"
    return windowed
