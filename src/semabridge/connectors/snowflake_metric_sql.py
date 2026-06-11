"""Snowflake METRICS-clause SQL normalization.

This is the last dialect-specific cleanup point before metric expressions are
written into semantic-view DDL. Translators and overrides can still be imperfect;
this module keeps Snowflake-only compatibility rules centralized.
"""

from __future__ import annotations

import re


def normalize_snowflake_metric_sql(expr: str) -> str:
    """Normalize a scalar metric expression for Snowflake semantic views."""
    if not expr:
        return expr
        
    # Pre-pass regex: strip TO_DOUBLE(CAST(x AS FLOAT/DOUBLE))
    # Snowflake doesn't accept a pre-CAST value as argument to TO_DOUBLE.
    expr = re.sub(
        r'\bTO_DOUBLE\s*\(\s*CAST\s*\(\s*(.+?)\s+AS\s+(?:FLOAT|DOUBLE)\s*\)\s*\)',
        r'TRY_CAST(\1 AS DOUBLE)',
        expr,
        flags=re.IGNORECASE,
    )
    # Strip remaining bare TO_DOUBLE(x) -> x
    expr = re.sub(r'\bTO_DOUBLE\s*\(\s*(.+?)\s*\)', r'\1', expr, flags=re.IGNORECASE)

    normalized = _rewrite_function(expr, "INT", lambda inner: f"CAST(({inner}) AS INT)")
    return normalized


def validate_snowflake_metric_sql(expr: str) -> tuple[bool, str | None]:
    """Validate a scalar Snowflake semantic-view metric expression."""
    if not expr or not str(expr).strip():
        return False, "empty metric SQL"

    scrubbed = _strip_string_literals(expr)
    upper = f" {scrubbed.upper()} "
    forbidden = (
        " SELECT ",
        "(SELECT",
        " FROM ",
        " JOIN ",
        " WITH ",
        " UNION ",
        " OVER ",
        " PARTITION BY ",
        " DROP ",
        " DELETE ",
        " TRUNCATE ",
        " INSERT ",
        " UPDATE ",
        " ALTER ",
        ";",
    )
    for token in forbidden:
        if token in upper:
            return False, f"forbidden token {token.strip()}"

    if re.search(r"\bINT\s*\(", scrubbed, re.IGNORECASE):
        return False, "unsupported INT(...) function"

    if _find_matching_paren(f"({expr})", 0) is None:
        return False, "unbalanced parentheses"

    return True, None


def _rewrite_function(expr: str, func_name: str, render) -> str:
    pattern = re.compile(rf"\b{re.escape(func_name)}\s*\(", re.IGNORECASE)
    out: list[str] = []
    pos = 0

    while True:
        match = pattern.search(expr, pos)
        if not match:
            out.append(expr[pos:])
            break

        open_idx = expr.find("(", match.start())
        close_idx = _find_matching_paren(expr, open_idx)
        if close_idx is None:
            out.append(expr[pos:])
            break

        out.append(expr[pos:match.start()])
        inner = expr[open_idx + 1:close_idx].strip()
        out.append(render(inner))
        pos = close_idx + 1

    return "".join(out)


def _find_matching_paren(text: str, open_idx: int) -> int | None:
    depth = 0
    quote: str | None = None
    i = open_idx

    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1

    return None


def _strip_string_literals(text: str) -> str:
    out: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)
