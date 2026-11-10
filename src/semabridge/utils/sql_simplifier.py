"""Generic SQL metric expression simplifier.

Removes dead arithmetic (e.g. x * 0 / 100 -> 0, x + 0 -> x, x - 0 -> x, x * 1 -> x)
and redundant NULL guards (e.g. CASE WHEN ... 0 IS NULL, nested COALESCE/NULLIF)
without changing mathematical results.
"""
from __future__ import annotations

import re


def simplify_sql_expression(sql: str) -> str:
    """Simplify an emitted SQL metric expression by removing dead arithmetic
    and redundant NULL guards. Returns the simplified expression.
    """
    if not sql or not isinstance(sql, str):
        return sql

    prev = ""
    res = sql.strip()
    for _ in range(5):
        if res == prev:
            break
        prev = res

        # 1. Normalize literal parentheses: (0) -> 0, (1) -> 1
        res = re.sub(r'\(\s*0(?:\.0+)?\s*\)', '0', res)
        res = re.sub(r'\(\s*1(?:\.0+)?\s*\)', '1', res)

        # 2. Dead division with 0 numerator: 0 / N -> 0
        res = re.sub(r'\b0\s*/\s*[1-9]\d*(?:\.\d+)?\b', '0', res)

        # 3. Constant arithmetic with 0 and 1
        res = re.sub(r'\b1\s*\+\s*0\b', '1', res)
        res = re.sub(r'\b0\s*\+\s*1\b', '1', res)
        res = re.sub(r'\b1\s*-\s*0\b', '1', res)

        # 4. Redundant NULL guards:
        # A branch checking `0 IS NULL` is always FALSE in SQL.
        # CASE WHEN ... 0 IS NULL ... THEN NULL ELSE <expr> END -> <expr>
        res = re.sub(
            r'CASE\s+WHEN\s+.*?\b0(?:\.0+)?\s+IS\s+NULL\b.*?\s+THEN\s+NULL\s+ELSE\s+(.+?)\s+END',
            r'\1',
            res,
            flags=re.IGNORECASE | re.DOTALL,
        )

        # Redundant identity guards:
        # CASE WHEN expr IS NULL THEN NULL ELSE expr END -> expr
        def _simplify_case_identity(match: re.Match) -> str:
            cond_expr = match.group(1).strip()
            else_expr = match.group(2).strip()
            if cond_expr.strip("()") == else_expr.strip("()"):
                return else_expr
            return match.group(0)

        res = re.sub(
            r'CASE\s+WHEN\s+(.+?)\s+IS\s+NULL\s+THEN\s+NULL\s+ELSE\s+(.+?)\s+END',
            _simplify_case_identity,
            res,
            flags=re.IGNORECASE,
        )

        # IFF(expr IS NULL, NULL, expr) -> expr
        def _simplify_iff_identity(match: re.Match) -> str:
            cond_expr = match.group(1).strip()
            else_expr = match.group(2).strip()
            if cond_expr.strip("()") == else_expr.strip("()"):
                return else_expr
            return match.group(0)

        res = re.sub(
            r'IFF\s*\(\s*(.+?)\s+IS\s+NULL\s*,\s*NULL\s*,\s*(.+?)\s*\)',
            _simplify_iff_identity,
            res,
            flags=re.IGNORECASE,
        )

        # 5. Redundant nested guards
        # Nested COALESCE: COALESCE(COALESCE(x, 0), 0) -> COALESCE(x, 0)
        res = re.sub(
            r'COALESCE\s*\(\s*(COALESCE\s*\(.*?\))\s*,\s*0\s*\)',
            r'\1',
            res,
            flags=re.IGNORECASE,
        )
        # COALESCE(NULL, 0) -> 0
        res = re.sub(r'COALESCE\s*\(\s*NULL\s*,\s*0\s*\)', '0', res, flags=re.IGNORECASE)
        # Nested NULLIF: NULLIF(NULLIF(x, 0), 0) -> NULLIF(x, 0)
        res = re.sub(
            r'NULLIF\s*\(\s*(NULLIF\s*\(.*?\))\s*,\s*0\s*\)',
            r'\1',
            res,
            flags=re.IGNORECASE,
        )

        # 6. Dead multiplication by 0:
        # expr * 0 / 100 -> 0
        res = re.sub(
            r'^.+?\s*\*\s*0(?:\.0+)?\s*/\s*[1-9]\d*(?:\.\d+)?$',
            '0',
            res,
        )
        # expr * 0 -> 0, 0 * expr -> 0
        res = re.sub(
            r'^.+?\s*\*\s*0(?:\.0+)?$',
            '0',
            res,
        )
        res = re.sub(
            r'^0(?:\.0+)?\s*\*\s*.+?$',
            '0',
            res,
        )

        # 7. Identity multiplication / division by 1:
        # expr * 1 -> expr
        res = re.sub(r'\s*\*\s*1(?:\.0+)?(?!\d)', '', res)
        res = re.sub(r'(?<![A-Za-z0-9_])1(?:\.0+)?\s*\*\s*', '', res)
        # expr / 1 -> expr
        res = re.sub(r'\s*/\s*1(?:\.0+)?(?!\d)', '', res)

        # 8. Additions / subtractions of 0:
        # expr + 0 -> expr
        res = re.sub(r'\s*\+\s*0(?:\.0+)?(?!\d)', '', res)
        # 0 + expr -> expr
        res = re.sub(r'(?<![A-Za-z0-9_])0(?:\.0+)?\s*\+\s*', '', res)
        # expr - 0 -> expr
        res = re.sub(r'\s*-\s*0(?:\.0+)?(?!\d)', '', res)

        # 9. Clean up redundant outer double parens: ((expr)) -> (expr)
        res = re.sub(r'\(\s*\(([^()]+)\)\s*\)', r'(\1)', res)
        res = res.strip()

    return res
