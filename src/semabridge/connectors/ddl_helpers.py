"""Small helpers extracted from ddl_builder to keep nested defs minimal.

These are thin, pure helpers that operate on strings/lists and accept any
collaborating objects (like `translator`) as parameters so they can be
reused from the larger `SemanticViewBuilder` implementation.
"""
from __future__ import annotations

import datetime
import re
from typing import List, Optional


def fix_global_sums(sql_str: str, translator) -> str:
    """Ensure SUM() arguments are cast safely by delegating to translator.

    This mirrors the logic previously embedded as a nested function inside
    `SemanticViewBuilder._generate_semantic_view` but receives the
    `translator` dependency explicitly.
    """
    result = []
    i = 0
    while i < len(sql_str):
        match = re.search(r'(?i)\bSUM\(', sql_str[i:])
        if not match:
            result.append(sql_str[i:])
            break

        start_idx = i + match.start()
        sum_open_idx = i + match.end()

        depth = 1
        j = sum_open_idx
        in_string = False
        while j < len(sql_str):
            if sql_str[j] == "'" and (j == 0 or sql_str[j - 1] != '\\'):
                in_string = not in_string
            elif not in_string:
                if sql_str[j] == '(':
                    depth += 1
                elif sql_str[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
            j += 1

        if depth == 0:
            expr = sql_str[sum_open_idx:j].strip()
            result.append(sql_str[i:start_idx])
            result.append(translator._build_safe_sum_sql(expr, expr))
            i = j + 1
        else:
            result.append(sql_str[i:sum_open_idx])
            i = sum_open_idx
    return "".join(result)


def format_scalar_sql_literal(value) -> str:
    """Render a Python value fetched from Snowflake as a SQL literal.

    Used to splice a pre-fetched anchor value (e.g. "today's fiscal period")
    into DDL as a constant instead of embedding the subquery that produced
    it — Snowflake rejects subqueries inside semantic-view TABLES clauses
    and view definitions regardless of whether they are correlated.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    # datetime.datetime is a subclass of datetime.date — check it first.
    # A DATE/TIMESTAMP anchor (e.g. MAX_DATE) formatted as a bare quoted
    # string is a VARCHAR literal, not a date — functions that require a
    # real date/timestamp argument (DATE_TRUNC, DATEADD, etc.) reject it
    # with a type error rather than implicitly casting it.
    if isinstance(value, datetime.datetime):
        return f"TIMESTAMP '{value.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(value, datetime.date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def deduplicate_metrics_lines(lines: List[str]) -> List[str]:
    """Remove duplicate metric expressions based on LHS metric name."""
    seen_metrics: dict[str, str] = {}
    unique_lines: List[str] = []

    for line in lines:
        match = re.search(r'(\w+)\."([^\"]+)"\s+AS\s+', line)
        if match:
            table_alias = match.group(1)
            metric_name = match.group(2)
            expr_key = f"{table_alias}.{metric_name}"

            if expr_key in seen_metrics:
                # keep first definition, drop later duplicates
                continue

            seen_metrics[expr_key] = line
            unique_lines.append(line)
        else:
            unique_lines.append(line)

    return unique_lines


def deduplicate_metrics_lines_osi(lines: List[str]) -> List[str]:
    # For now identical implementation; kept separate for clarity/extension.
    return deduplicate_metrics_lines(lines)


def extract_expr_key(def_line: str) -> Optional[str]:
    match = re.search(r'(\w+)\."([^\"]+)"\s+AS\s+', def_line)
    if not match:
        return None
    return f"{match.group(1)}.{match.group(2)}"


def extract_expr_key_osi(def_line: str) -> Optional[str]:
    return extract_expr_key(def_line)


def extract_referenced_table_aliases(metric_sql: str, valid_aliases: set[str]) -> set[str]:
    if not metric_sql:
        return set()
    referenced: set[str] = set()
    patterns = [
        r'(\w+)\."([^\"]+)"',
        r'(\w+)\.([A-Za-z_][A-Za-z0-9_$]*)',
    ]
    for pattern in patterns:
        for alias, _ in re.findall(pattern, metric_sql):
            if alias in valid_aliases:
                referenced.add(alias)
    return referenced
