"""Snowflake Truth Window Builder."""
from __future__ import annotations

import re
from typing import Any, Optional, Tuple

from semabridge.ai.window_builders.base import WindowBuilder

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


class SnowflakeWindowBuilder(WindowBuilder):
    platform: str = "SNOWFLAKE"

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

        # Snowflake SEMANTIC_VIEW requires internal WHERE clause inside SEMANTIC_VIEW(...)
        if bool(_SEMANTIC_VIEW_RE.search(text)):
            if not _QUALIFIED_FILTER_RE.match(clause_str):
                return None

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

        # Plain SELECT query fallback
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


# Backward-compatible alias
SnowflakeTruthWindowBuilder = SnowflakeWindowBuilder
