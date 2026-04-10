from __future__ import annotations

import os
import re
from typing import Callable, Optional

from semabridge.core.behavior import DatabricksBehavior
from semabridge.sml.models import SMLMetric


class DatabricksMeasureTranslator:
    """Encapsulates DAX-to-SQL measure translation helpers.

    This module keeps translation-focused logic separate from publish/deploy
    orchestration to reduce `databricks_publisher.py` size and complexity.
    """

    def __init__(
        self,
        *,
        behavior: DatabricksBehavior,
        sanitize_identifier: Callable[[str], str],
        build_aggregation_sql: Callable[[SMLMetric], Optional[str]],
        distinct_count_expression: Callable[[str], str],
    ) -> None:
        self._behavior = behavior
        self._sanitize_identifier = sanitize_identifier
        self._build_aggregation_sql = build_aggregation_sql
        self._distinct_count_expression = distinct_count_expression

    def measure_ref_key(self, value: str) -> str:
        """Build a stable lookup key for DAX [Measure Name] references."""
        return self._sanitize_identifier(value or "").upper()

    def build_measure_sql_reference_map(
        self,
        dataset_metrics: list[SMLMetric],
        current_metric: SMLMetric,
    ) -> dict[str, str]:
        """Build best-effort SQL lookup for same-dataset measure references."""
        lookup: dict[str, str] = {}
        pending: list[SMLMetric] = []

        for metric in dataset_metrics:
            if metric is current_metric:
                continue

            key = self.measure_ref_key(metric.unique_name)
            if not key or key in lookup:
                continue

            sql_expr = (metric.sql_expression or "").strip()
            if not sql_expr and metric.source_column:
                sql_expr = self._build_aggregation_sql(metric) or ""
            if not sql_expr:
                dax_expr = (metric.expression or "").strip()
                if dax_expr:
                    sql_expr = self.try_simple_dax_to_sql(dax_expr) or ""
                    if not sql_expr:
                        pending.append(metric)

            sql_expr = str(sql_expr or "").strip()
            if sql_expr:
                lookup[key] = sql_expr

        # Resolve deferred context-heavy expressions using already-known refs.
        # Bounded passes avoid recursion loops on cyclic measure references.
        for _ in range(3):
            if not pending:
                break
            remaining: list[SMLMetric] = []
            progress = False
            for metric in pending:
                key = self.measure_ref_key(metric.unique_name)
                dax_expr = (metric.expression or "").strip()
                resolved = self.try_contextual_dax_to_sql(dax_expr, lookup)
                if resolved:
                    lookup[key] = resolved
                    progress = True
                else:
                    remaining.append(metric)
            pending = remaining
            if not progress:
                break

        return lookup

    def replace_measure_refs_in_expression(
        self,
        expression: str,
        measure_sql_map: dict[str, str],
        max_passes: int = 6,
    ) -> tuple[str, bool]:
        """Iteratively replace [Measure] refs with resolved SQL snippets."""
        text = str(expression or "")
        pattern = re.compile(r"\[([^\]]+)\]")

        for _ in range(max_passes):
            changed = False
            unresolved = False

            def _replace(match: re.Match[str]) -> str:
                nonlocal changed, unresolved
                measure_name = str(match.group(1) or "")
                resolved = str(
                    measure_sql_map.get(self.measure_ref_key(measure_name), "")
                ).strip()
                if not resolved:
                    unresolved = True
                    return match.group(0)
                changed = True
                return f"({resolved})"

            updated = pattern.sub(_replace, text)
            text = updated

            if not changed:
                return text, bool(unresolved or pattern.search(text))

        return text, bool(pattern.search(text))

    def split_top_level_csv(self, value: str) -> list[str]:
        """Split a comma-separated argument list while respecting nesting."""
        parts: list[str] = []
        current: list[str] = []
        depth = 0
        in_single = False
        in_double = False

        for ch in str(value or ""):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double

            if not in_single and not in_double:
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth > 0:
                    depth -= 1
                elif ch == "," and depth == 0:
                    parts.append("".join(current).strip())
                    current = []
                    continue

            current.append(ch)

        if current:
            parts.append("".join(current).strip())
        return [part for part in parts if part]

    def rewrite_divide_calls(self, expression: str) -> str:
        """Rewrite DAX DIVIDE(a,b[,alt]) to SQL-safe division."""
        text = str(expression or "")
        output: list[str] = []
        cursor = 0

        while True:
            match = re.search(r"(?i)\bDIVIDE\s*\(", text[cursor:])
            if not match:
                output.append(text[cursor:])
                break

            start = cursor + match.start()
            open_paren = cursor + match.end() - 1
            output.append(text[cursor:start])

            depth = 1
            idx = open_paren + 1
            while idx < len(text) and depth > 0:
                if text[idx] == "(":
                    depth += 1
                elif text[idx] == ")":
                    depth -= 1
                idx += 1

            if depth != 0:
                output.append(text[start:])
                break

            inner = text[open_paren + 1 : idx - 1]
            args = self.split_top_level_csv(inner)
            if len(args) < 2:
                output.append(text[start:idx])
                cursor = idx
                continue

            numerator = args[0]
            denominator = args[1]
            alternate = args[2] if len(args) >= 3 and args[2] else "0"
            output.append(
                f"COALESCE(({numerator}) / NULLIF(({denominator}), 0), {alternate})"
            )
            cursor = idx

        return "".join(output)

    def is_sql_arithmetic_expression(self, expression: str) -> bool:
        """Return True when expression is safe to treat as SQL arithmetic."""
        text = str(expression or "").strip()
        if not text:
            return False
        if "[" in text or "]" in text:
            return False
        if re.search(
            r"(?i)\b(TOTALYTD|CALCULATE|SAMEPERIODLASTYEAR|DATESYTD|FILTER|ALL|EARLIER|SUMX|AVERAGEX|IF|DIVIDE|BLANK)\b",
            text,
        ):
            return False
        return bool(
            re.fullmatch(
                r"(?is)[A-Za-z0-9_`\"().,+\-*/%\s=<>!|&:'$]+",
                text,
            )
        )

    def extract_simple_aggregate_parts(self, sql_expr: str) -> tuple[str, str] | None:
        """Extract aggregate function and argument from a simple aggregate SQL expression."""
        text = str(sql_expr or "").strip()
        m = re.fullmatch(
            r"(?is)(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(DISTINCT\s+)?(`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)\s*\)",
            text,
        )
        if not m:
            return None

        func = str(m.group(1)).upper()
        distinct_prefix = "DISTINCT " if m.group(2) else ""
        arg = str(m.group(3)).strip()
        return func, f"{distinct_prefix}{arg}".strip()

    def try_contextual_dax_to_sql(
        self,
        dax_expression: str,
        measure_sql_map: dict[str, str],
    ) -> Optional[str]:
        """Translate selected context-heavy DAX patterns using known measure SQL refs."""
        expr = " ".join(str(dax_expression or "").split())
        if not expr:
            return None

        def _ref(name: str) -> str:
            return measure_sql_map.get(self.measure_ref_key(name), "")

        # TOTALYTD([Measure], 'Date'[Date])
        m_totalytd = re.match(
            r"(?i)^TOTALYTD\(\s*(?:\[(?P<measure>[^\]]+)\]|SUM\(\s*(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*)?\[(?P<sum_col>[^\]]+)\]\s*\))\s*,\s*(?:'(?P<date_table_q>[^']+)'|(?P<date_table>[A-Za-z_][A-Za-z0-9_]*))\s*\[(?P<date_col>[^\]]+)\]\s*\)$",
            expr,
        )
        if m_totalytd:
            measure_name = str(m_totalytd.group("measure") or "").strip()
            sum_col = self._sanitize_identifier(m_totalytd.group("sum_col") or "")
            base_sql = _ref(measure_name) if measure_name else (f"SUM(`{sum_col}`)" if sum_col else "")
            date_table = str(m_totalytd.group("date_table_q") or m_totalytd.group("date_table") or "").strip()
            date_col = self._sanitize_identifier(m_totalytd.group("date_col"))
            agg_parts = self.extract_simple_aggregate_parts(base_sql)
            if agg_parts and date_col:
                func, arg = agg_parts
                if date_table and self._behavior.enable_cross_table_joins:
                    date_prefix = self._sanitize_identifier(date_table).lower()
                    date_ref = f"`{date_prefix}`.`{date_col}`"
                else:
                    date_ref = f"`{date_col}`"
                return (
                    f"{func}({arg}) OVER ("
                    f"PARTITION BY YEAR({date_ref}) "
                    f"ORDER BY {date_ref} "
                    "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
                )

        # CALCULATE([Measure], SAMEPERIODLASTYEAR('Date'[Date]))
        m_sply = re.match(
            r"(?i)^CALCULATE\(\s*\[([^\]]+)\]\s*,\s*SAMEPERIODLASTYEAR\(\s*(?:'(?P<date_table_q>[^']+)'|(?P<date_table>[A-Za-z_][A-Za-z0-9_]*))\s*\[(?P<date_col>[^\]]+)\]\s*\)\s*\)$",
            expr,
        )
        if m_sply:
            base_sql = _ref(m_sply.group(1))
            date_table = str(m_sply.group("date_table_q") or m_sply.group("date_table") or "").strip()
            date_col = self._sanitize_identifier(m_sply.group("date_col"))
            agg_parts = self.extract_simple_aggregate_parts(base_sql)
            if agg_parts and date_col:
                func, arg = agg_parts
                if date_table and self._behavior.enable_cross_table_joins:
                    date_prefix = self._sanitize_identifier(date_table).lower()
                    date_ref = f"`{date_prefix}`.`{date_col}`"
                else:
                    date_ref = f"`{date_col}`"
                return (
                    f"LAG({func}({arg}), 12) OVER ("
                    f"PARTITION BY MONTH({date_ref}) "
                    f"ORDER BY YEAR({date_ref}), MONTH({date_ref})"
                    ")"
                )

        # [MeasureA] - [MeasureB]
        m_sub = re.match(r"(?i)^\[([^\]]+)\]\s*-\s*\[([^\]]+)\]$", expr)
        if m_sub:
            left = _ref(m_sub.group(1))
            right = _ref(m_sub.group(2))
            if left and right:
                return f"({left}) - ({right})"

        # DIVIDE([Numerator], [Denominator], 0)
        m_divide = re.match(
            r"(?i)^DIVIDE\(\s*\[([^\]]+)\]\s*,\s*\[([^\]]+)\]\s*(?:,\s*0\s*)?\)$",
            expr,
        )
        if m_divide:
            num = _ref(m_divide.group(1))
            den = _ref(m_divide.group(2))
            if num and den:
                return f"COALESCE(({num}) / NULLIF(({den}), 0), 0)"

        expanded_expr, unresolved_refs = self.replace_measure_refs_in_expression(
            expr,
            measure_sql_map,
        )
        if unresolved_refs:
            return None

        expanded_expr = re.sub(r"(?i)\bBLANK\(\)", "NULL", expanded_expr)
        expanded_expr = self.rewrite_divide_calls(expanded_expr)

        if self.is_sql_arithmetic_expression(expanded_expr):
            return expanded_expr

        return None

    def try_simple_dax_to_sql(self, dax_expression: str) -> Optional[str]:
        """Translate common simple DAX patterns to Databricks SQL."""
        expr = " ".join(dax_expression.split())

        # COUNTROWS('Table') -> COUNT(*)
        if re.match(r"(?i)^COUNTROWS\(\s*'[^']+'\s*\)$", expr):
            return "COUNT(*)"

        # COUNTBLANK('Table'[Column]) -> COUNT_IF(`Column` IS NULL)
        m_blank = re.match(
            r"(?i)^COUNTBLANK\(\s*(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_blank:
            col = self._sanitize_identifier(m_blank.group(1))
            return f"COUNT_IF(`{col}` IS NULL)"

        # SUM / AVERAGE / COUNT / DISTINCTCOUNT / MIN / MAX('Table'[Column])
        m_agg = re.match(
            r"(?i)^(SUM|AVERAGE|COUNT|DISTINCTCOUNT|MIN|MAX)"
            r"\(\s*(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*)?\[([^\]]+)\]\s*\)$",
            expr,
        )
        if m_agg:
            func = m_agg.group(1).upper()
            col = self._sanitize_identifier(m_agg.group(2))

            func_map = {
                "SUM": f"SUM(`{col}`)",
                "AVERAGE": f"AVG(`{col}`)",
                "COUNT": f"COUNT(`{col}`)",
                "DISTINCTCOUNT": self._distinct_count_expression(f"`{col}`"),
                "MIN": f"MIN(`{col}`)",
                "MAX": f"MAX(`{col}`)",
            }
            return func_map.get(func)

        # Simple [Column] reference (bare column ref without aggregation)
        m_bare = re.match(r"^\[([^\]]+)\]$", expr)
        if m_bare:
            col = self._sanitize_identifier(m_bare.group(1))
            return f"`{col}`"

        # Pattern too complex for deterministic translation
        return None
