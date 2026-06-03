"""
SQL → DAX reverse translation for SemaBridge.

Used when extracting from Snowflake semantic views and need to publish back to Fabric.
Covers Tiers 1-3 deterministically; Tier 4 defers to LLM; Tier 0 marks sync_disabled.

Tier definitions:
    1 — Simple aggregation: SUM/AVG/MIN/MAX/COUNT(col)
    2 — DIVIDE pattern, ratio expressions
    3 — Time intelligence patterns: CASE WHEN date windows → YTD/SAMEPERIODLASTYEAR
    4 — LLM fallback (not implemented here — caller handles)
    0 — Failed/unsupported; caller should set sync_enabled=False
"""
from __future__ import annotations

import re
from typing import Optional


class SQLToDAXConverter:
    """Deterministic SQL → DAX converter for Tiers 1-3."""

    # SQL aggregation → DAX function
    AGG_MAP: dict[str, str] = {
        "SUM": "SUM",
        "AVG": "AVERAGE",
        "AVERAGE": "AVERAGE",
        "MIN": "MIN",
        "MAX": "MAX",
        "COUNT": "COUNTROWS",
        "COUNT_DISTINCT": "DISTINCTCOUNT",
        "COUNT DISTINCT": "DISTINCTCOUNT",
    }

    def translate(self, sql: str, table_name: str) -> tuple[str, int]:
        """Translate a SQL metric expression to DAX.

        Args:
            sql: Snowflake SQL expression string (e.g. "SUM(AMOUNT)")
            table_name: Fabric table name for column references (e.g. "Sales")

        Returns:
            (dax_expression, tier) where tier is 1-3 on success, 0 on failure
        """
        sql = sql.strip()

        # Tier 1: simple aggregation — SUM(col), AVG(col), etc.
        result = self._try_simple_agg(sql, table_name)
        if result:
            return result, 1

        # Tier 1b: COUNT(DISTINCT col)
        result = self._try_count_distinct(sql, table_name)
        if result:
            return result, 1

        # Tier 2: DIVIDE / ratio — SUM(a) / SUM(b) or SUM(a) / NULLIF(SUM(b), 0)
        result = self._try_divide(sql, table_name)
        if result:
            return result, 2

        # Tier 2b: simple arithmetic on aggregations
        result = self._try_arithmetic_aggs(sql, table_name)
        if result:
            return result, 2

        # Tier 3: YTD pattern (CASE WHEN date BETWEEN year-start AND today)
        result = self._try_ytd_pattern(sql, table_name)
        if result:
            return result, 3

        # Tier 3b: SAMEPERIODLASTYEAR pattern (CASE WHEN date in prior year range)
        result = self._try_sameperiodlastyear(sql, table_name)
        if result:
            return result, 3

        # Tier 0: cannot translate
        return sql, 0

    # ─── Tier 1 ─────────────────────────────────────────────────────────────

    def _try_simple_agg(self, sql: str, table: str) -> Optional[str]:
        """Match SUM(col), AVG(col), MIN(col), MAX(col), COUNT(col)."""
        pat = re.compile(
            r'^(SUM|AVG|AVERAGE|MIN|MAX|COUNT)\s*\(\s*(?:"?(\w+)"?\.)?"?(\w+)"?\s*\)$',
            re.IGNORECASE,
        )
        m = pat.match(sql)
        if not m:
            return None
        agg_fn = m.group(1).upper()
        col = m.group(3)
        dax_fn = self.AGG_MAP.get(agg_fn)
        if not dax_fn:
            return None
        if dax_fn == "COUNTROWS":
            return f"COUNTROWS('{table}')"
        return f"{dax_fn}('{table}'[{col}])"

    def _try_count_distinct(self, sql: str, table: str) -> Optional[str]:
        """Match COUNT(DISTINCT col)."""
        pat = re.compile(
            r'^COUNT\s*\(\s*DISTINCT\s+"?(\w+)"?\s*\)$',
            re.IGNORECASE,
        )
        m = pat.match(sql)
        if not m:
            return None
        col = m.group(1)
        return f"DISTINCTCOUNT('{table}'[{col}])"

    # ─── Tier 2 ─────────────────────────────────────────────────────────────

    def _try_divide(self, sql: str, table: str) -> Optional[str]:
        """Match SUM(a) / SUM(b) or SUM(a) / NULLIF(SUM(b), 0)."""
        # Pattern: <agg(col)> / <agg(col)> or <agg(col)> / NULLIF(<agg(col)>, 0)
        agg_pat = r'(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*"?(\w+)"?\s*\)'
        pat = re.compile(
            rf'^({agg_pat})\s*/\s*(?:NULLIF\s*\(\s*({agg_pat})\s*,\s*0\s*\)|({agg_pat}))$',
            re.IGNORECASE,
        )
        m = pat.match(sql)
        if not m:
            return None
        # Numerator
        num_fn, num_col = m.group(2).upper(), m.group(3)
        num_dax = self.AGG_MAP.get(num_fn, num_fn)
        numerator = f"{num_dax}('{table}'[{num_col}])"
        # Denominator (could be in group 5+6 or 8+9 depending on NULLIF)
        # Simpler: just re-extract denominator by splitting on /
        parts = re.split(r'\s*/\s*', sql, maxsplit=1)
        if len(parts) != 2:
            return None
        den_sql = re.sub(r'NULLIF\s*\((.+),\s*0\s*\)', r'\1', parts[1], flags=re.IGNORECASE).strip()
        den_translated, den_tier = self.translate(den_sql, table)
        if den_tier == 0:
            return None
        return f"DIVIDE({numerator}, {den_translated})"

    def _try_arithmetic_aggs(self, sql: str, table: str) -> Optional[str]:
        """Match SUM(a) +/- SUM(b) patterns."""
        # Split on + or - (not inside parens)
        ops = self._split_arithmetic(sql)
        if ops is None or len(ops) < 2:
            return None
        dax_parts = []
        for op_sign, part in ops:
            translated, tier = self.translate(part.strip(), table)
            if tier == 0:
                return None
            dax_parts.append((op_sign, translated))
        result = dax_parts[0][1]
        for sign, part in dax_parts[1:]:
            result = f"{result} {sign} {part}"
        return result

    def _split_arithmetic(self, sql: str) -> Optional[list[tuple[str, str]]]:
        """Split SQL on top-level + or - operators, returning [(sign, expr), ...]."""
        parts: list[tuple[str, str]] = []
        depth = 0
        current = ""
        current_sign = "+"
        i = 0
        while i < len(sql):
            ch = sql[i]
            if ch in "([":
                depth += 1
                current += ch
            elif ch in ")]":
                depth -= 1
                current += ch
            elif ch in "+-" and depth == 0 and current.strip():
                parts.append((current_sign, current.strip()))
                current_sign = ch
                current = ""
            else:
                current += ch
            i += 1
        if current.strip():
            parts.append((current_sign, current.strip()))
        # Only return if we found actual arithmetic (>1 part) and no * or /
        if len(parts) <= 1 or "*" in sql or "/" in sql:
            return None
        return parts

    # ─── Tier 3 ─────────────────────────────────────────────────────────────

    def _try_ytd_pattern(self, sql: str, table: str) -> Optional[str]:
        """Detect CASE WHEN date BETWEEN year-start AND CURRENT_DATE() → TOTALYTD DAX."""
        # Pattern: SUM(CASE WHEN date >= DATE_TRUNC('YEAR',...) AND date <= CURRENT_DATE() THEN col END)
        if not re.search(r'CASE\s+WHEN', sql, re.IGNORECASE):
            return None
        if not re.search(r"DATE_TRUNC\s*\(\s*'YEAR'", sql, re.IGNORECASE):
            return None
        # Extract the aggregated column from the CASE expr
        m = re.search(r'(SUM|AVG|MIN|MAX)\s*\(\s*CASE\b.+?THEN\s+"?(\w+)"?\s+END\s*\)', sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        agg_fn = self.AGG_MAP.get(m.group(1).upper(), m.group(1).upper())
        col = m.group(2)
        # Find date column
        date_m = re.search(r'WHEN\s+"?(\w+)"?\s*(?:>=|BETWEEN)', sql, re.IGNORECASE)
        date_col = date_m.group(1) if date_m else "Date"
        return f"TOTALYTD({agg_fn}('{table}'[{col}]), '{table}'[{date_col}])"

    def _try_sameperiodlastyear(self, sql: str, table: str) -> Optional[str]:
        """Detect prior-year CASE WHEN → CALCULATE with SAMEPERIODLASTYEAR."""
        if not re.search(r'CASE\s+WHEN', sql, re.IGNORECASE):
            return None
        if not re.search(r'YEAR\s*\(.+\)\s*=\s*YEAR\s*\(.+\)\s*-\s*1', sql, re.IGNORECASE):
            return None
        m = re.search(r'(SUM|AVG|MIN|MAX)\s*\(\s*CASE\b.+?THEN\s+"?(\w+)"?\s+END\s*\)', sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return None
        agg_fn = self.AGG_MAP.get(m.group(1).upper(), m.group(1).upper())
        col = m.group(2)
        date_m = re.search(r'WHEN\s+YEAR\s*\(\s*"?(\w+)"?\s*\)', sql, re.IGNORECASE)
        date_col = date_m.group(1) if date_m else "Date"
        return (
            f"CALCULATE({agg_fn}('{table}'[{col}]), "
            f"SAMEPERIODLASTYEAR('{table}'[{date_col}]))"
        )
