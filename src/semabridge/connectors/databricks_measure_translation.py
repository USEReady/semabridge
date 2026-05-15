from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional

from semabridge.core.behavior import DatabricksBehavior
from semabridge.sml.models import SMLMetric


TIER_STANDARD_AGGREGATION = "TIER_1_STANDARD_AGGREGATION"
TIER_SCALAR_SYSTEM_FUNCTION = "TIER_2_SCALAR_SYSTEM_FUNCTION"
TIER_FILTERED_CONDITIONAL_AGGREGATION = "TIER_3_FILTERED_CONDITIONAL_AGGREGATION"
TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION = "TIER_4_RELATIONSHIP_AWARE_FILTERED_AGGREGATION"
TIER_DEFERRED = "TIER_5_DEFERRED"


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

        def _replace_once(source: str) -> tuple[str, bool, bool]:
            output: list[str] = []
            changed = False
            unresolved = False
            index = 0
            while index < len(source):
                if source[index] != "[":
                    output.append(source[index])
                    index += 1
                    continue

                prev = source[index - 1] if index > 0 else ""
                if prev and (prev.isalnum() or prev in "_'\"]"):
                    output.append(source[index])
                    index += 1
                    continue

                end = source.find("]", index + 1)
                if end < 0:
                    output.append(source[index:])
                    unresolved = True
                    break

                measure_name = source[index + 1 : end].strip()
                resolved = str(
                    measure_sql_map.get(self.measure_ref_key(measure_name), "")
                ).strip()
                if not resolved:
                    output.append(source[index : end + 1])
                    unresolved = True
                else:
                    output.append(f"({resolved})")
                    changed = True
                index = end + 1

            return "".join(output), changed, unresolved

        for _ in range(max_passes):
            text, changed, unresolved = _replace_once(text)
            if not changed:
                return text, unresolved or ("[" in text and "]" in text)

        return text, "[" in text and "]" in text

    def rewrite_if_calls(self, expression: str) -> str:
        """Rewrite DAX IF(condition, true, false) into SQL CASE expressions."""
        text = str(expression or "")
        output: list[str] = []
        cursor = 0

        while True:
            match = re.search(r"(?i)\bIF\s*\(", text[cursor:])
            if not match:
                output.append(text[cursor:])
                break

            start = cursor + match.start()
            open_paren = cursor + match.end() - 1
            output.append(text[cursor:start])

            depth = 1
            idx = open_paren + 1
            in_single = False
            in_double = False
            while idx < len(text) and depth > 0:
                ch = text[idx]
                if ch == "'" and not in_double:
                    in_single = not in_single
                elif ch == '"' and not in_single:
                    in_double = not in_double
                elif not in_single and not in_double:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                idx += 1

            if depth != 0:
                output.append(text[start:])
                break

            inner = text[open_paren + 1 : idx - 1]
            args = self.split_top_level_csv(inner)
            if len(args) < 3:
                output.append(text[start:idx])
                cursor = idx
                continue

            condition = args[0]
            true_expr = args[1]
            false_expr = args[2]
            if re.fullmatch(r"(?i)BLANK\s*\(\s*\)", false_expr or ""):
                false_expr = "NULL"
            output.append(f"CASE WHEN {condition} THEN {true_expr} ELSE {false_expr} END")
            cursor = idx

        return "".join(output)

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

    def normalize_dax_leakage_in_sql_expression(self, sql_expression: str) -> str:
        """Normalize DAX syntax that was accidentally stored as SQL.

        Fabric-to-target conversion can persist DAX in ``sql_expression`` for
        simple expressions. Databricks cannot execute DAX refs like
        ``'Fact'[Revenue]`` or the DAX-only ``DIVIDE`` function, so repair the
        small deterministic subset before the publisher treats it as native SQL.
        """
        text = str(sql_expression or "").strip()
        if not text:
            return text
        if not re.search(r"(?i)\bDIVIDE\s*\(|\[[^\]]+\]", text):
            return text

        def _render_ref(match: re.Match) -> str:
            col = self._sanitize_identifier(match.group("column") or "")
            return f"`{col}`" if col else match.group(0)

        def _render_aggregate(match: re.Match) -> str:
            func = str(match.group("func") or "").upper()
            col = self._sanitize_identifier(match.group("column") or "")
            if not col:
                return match.group(0)
            if func == "AVERAGE":
                func = "AVG"
            if func == "DISTINCTCOUNT":
                return self._distinct_count_expression(col)
            return f"{func}(`{col}`)"

        aggregate_pattern = re.compile(
            r"(?is)\b(?P<func>SUM|AVERAGE|AVG|COUNT|MIN|MAX|DISTINCTCOUNT)\s*\(\s*"
            r"(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_ ]*)\s*)?\[(?P<column>[^\]]+)\]\s*\)"
        )
        ref_pattern = re.compile(
            r"(?is)(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_ ]*)\s*)?\[(?P<column>[^\]]+)\]"
        )

        normalized = aggregate_pattern.sub(_render_aggregate, text)
        normalized = ref_pattern.sub(_render_ref, normalized)
        normalized = self.rewrite_divide_calls(normalized)
        normalized = re.sub(r"(?i)\bBLANK\s*\(\s*\)", "NULL", normalized)
        return normalized

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
        expanded_expr = self.rewrite_if_calls(expanded_expr)
        expanded_expr = self.rewrite_divide_calls(expanded_expr)

        if self.is_sql_arithmetic_expression(expanded_expr):
            return expanded_expr

        return None

    def try_simple_dax_to_sql(self, dax_expression: str) -> Optional[str]:
        """Translate common simple DAX patterns to Databricks SQL."""
        expr = " ".join(dax_expression.split())

        # Minimal VAR/RETURN support for scalar variables that can be translated
        # deterministically (e.g., TODAY()). Complex variable chains are skipped.
        var_return_sql = self._try_simple_var_return_to_sql(expr)
        if var_return_sql:
            return var_return_sql

        # Bulletproof Tier-1 parser: top-level single-column aggregates.
        # Preserve table qualifier when present so cross-table join resolution
        # can map lineage downstream. Keep unqualified output for same-table
        # expressions to preserve existing behavior.
        m_simple_agg = re.match(
            r"^\s*(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\s*\(\s*(?:'(?P<table_q>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))?\s*\[(?P<column>[^\]]+)\]\s*\)\s*$",
            expr,
            re.IGNORECASE,
        )
        if m_simple_agg:
            func = m_simple_agg.group(1).lower()
            table = self._sanitize_identifier(
                str(m_simple_agg.group("table_q") or m_simple_agg.group("table") or "")
            ).lower()
            col = self._sanitize_identifier(m_simple_agg.group("column")).lower()
            qualify_for_join = {"sum", "average", "count"}
            if self._behavior.enable_cross_table_joins and table and func in qualify_for_join:
                col_ref = f"{table}.{col}"
            else:
                col_ref = col
            if func == "average":
                func = "avg"
            elif func == "distinctcount":
                return f"count(distinct {col_ref})"
            return f"{func}({col_ref})"

        def _col_ref(name: str) -> str:
            return f"`{self._sanitize_identifier(name)}`"

        # TODAY() -> current_date()
        if re.match(r"(?i)^TODAY\(\s*\)$", expr):
            return "current_date()"

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
            col_ref = _col_ref(m_agg.group(2))

            func_map = {
                "SUM": f"SUM({col_ref})",
                "AVERAGE": f"AVG({col_ref})",
                "COUNT": f"COUNT({col_ref})",
                "DISTINCTCOUNT": self._distinct_count_expression(col_ref),
                "MIN": f"MIN({col_ref})",
                "MAX": f"MAX({col_ref})",
            }
            return func_map.get(func)

        # CONCATENATE("text", MAX('Table'[Column])) -> concat('text', MAX(`Column`))
        m_concat = re.match(
            r'(?is)^CONCATENATE\(\s*("(?:[^"]|"")*"|\'[^\']*\')\s*,\s*(.+)\s*\)$',
            expr,
        )
        if m_concat:
            prefix = m_concat.group(1)
            suffix = m_concat.group(2).strip()
            suffix_sql = self.try_simple_dax_to_sql(suffix) or suffix
            if re.match(r"(?is)^(MIN|MAX|SUM|AVG|COUNT)\s*\(", suffix_sql.strip()):
                suffix_sql = f"cast({suffix_sql} as string)"
            if prefix.startswith('"') and prefix.endswith('"'):
                literal = prefix[1:-1].replace('""', '"').replace("'", "''")
                prefix_sql = f"'{literal}'"
            else:
                prefix_sql = prefix
            return f"concat({prefix_sql}, {suffix_sql})"

        # CALCULATE(AGG('Table'[Column]), condition)
        # -> AGG(CASE WHEN ... THEN `Column` ELSE <agg-neutral> END)
        m_calculate = re.match(
            r"(?is)^CALCULATE\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\(\s*(?:(?:'(?P<table_q>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\s*)?\[([^\]]+)\]\s*\)\s*,\s*(.+)\s*\)$",
            expr,
        )
        if m_calculate:
            agg_func = m_calculate.group(1).upper()
            base_table = str(m_calculate.group("table_q") or m_calculate.group("table") or "").strip()
            base_col = self._sanitize_identifier(m_calculate.group(4))
            condition_expr = m_calculate.group(5).strip()

            base_col_sql = f"`{base_col}`"
            if base_table:
                base_col_sql = f"`{self._sanitize_identifier(base_table)}`.{base_col_sql}"

            if condition_expr.upper().startswith("FILTER(") and condition_expr.endswith(")"):
                filter_args = self.split_top_level_csv(condition_expr[7:-1])
                if len(filter_args) >= 2:
                    condition_expr = filter_args[1]

            condition_sql = self._translate_simple_dax_condition(condition_expr)
            if condition_sql:
                case_expr_by_agg = {
                    "SUM": f"SUM(CASE WHEN {condition_sql} THEN {base_col_sql} ELSE 0 END)",
                    "AVERAGE": f"AVG(CASE WHEN {condition_sql} THEN {base_col_sql} ELSE NULL END)",
                    "COUNT": f"COUNT(CASE WHEN {condition_sql} THEN {base_col_sql} ELSE NULL END)",
                    "MIN": f"MIN(CASE WHEN {condition_sql} THEN {base_col_sql} ELSE NULL END)",
                    "MAX": f"MAX(CASE WHEN {condition_sql} THEN {base_col_sql} ELSE NULL END)",
                }
                return case_expr_by_agg.get(agg_func)

        # Simple [Column] reference (bare column ref without aggregation)
        m_bare = re.match(r"^\[([^\]]+)\]$", expr)
        if m_bare:
            col = self._sanitize_identifier(m_bare.group(1))
            return f"`{col}`"

        # Pattern too complex for deterministic translation
        return None

    def classify_dax_measure_tier(self, dax_expression: str) -> str:
        """Classify DAX measures into deterministic sync tiers for Databricks.

        Tier order:
            1) Standard single-table aggregations
            2) Scalar system functions
            3) Filtered/conditional same-table aggregations
            4) Relationship-aware cross-table aggregations
            5) Deferred/unsupported
        """
        expr = " ".join(str(dax_expression or "").split())
        if not expr:
            return TIER_DEFERRED

        # Tier 2: scalar system functions
        if re.match(r"(?i)^(TODAY|NOW|USEROBJECTID)\s*\(\s*\)$", expr):
            return TIER_SCALAR_SYSTEM_FUNCTION

        # Tier 1: standard aggregations over one table/column
        if re.match(
            r"(?i)^(SUM|AVERAGE|COUNT|DISTINCTCOUNT|MIN|MAX)"
            r"\(\s*(?:(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*)?\[[^\]]+\]\s*\)$",
            expr,
        ):
            return TIER_STANDARD_AGGREGATION

        # Tier 1: table row counts
        if re.match(r"(?i)^COUNTROWS\(\s*(?:'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*\)$", expr):
            return TIER_STANDARD_AGGREGATION

        # Tier 3/4: CALCULATE over aggregate with filter predicates
        if re.match(r"(?is)^CALCULATE\s*\(", expr):
            # Use table references inside [table][column] refs to distinguish same-table vs cross-table.
            table_refs = re.findall(
                r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*\[[^\]]+\]",
                expr,
            )
            tables = {
                str(table_q or table or "").strip().lower()
                for table_q, table in table_refs
                if str(table_q or table or "").strip()
            }
            if len(tables) > 1:
                return TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION
            if len(tables) == 1:
                return TIER_FILTERED_CONDITIONAL_AGGREGATION

        # VAR/RETURN wrappers can hide CALCULATE body; inspect table refs.
        if re.search(r"(?i)\bVAR\b", expr) and re.search(r"(?i)\bRETURN\b", expr):
            table_refs = re.findall(
                r"(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_]*))\s*\[[^\]]+\]",
                expr,
            )
            tables = {
                str(table_q or table or "").strip().lower()
                for table_q, table in table_refs
                if str(table_q or table or "").strip()
            }
            if len(tables) > 1:
                return TIER_RELATIONSHIP_AWARE_FILTERED_AGGREGATION
            if len(tables) == 1 and re.search(r"(?i)\bCALCULATE\s*\(", expr):
                return TIER_FILTERED_CONDITIONAL_AGGREGATION

        return TIER_DEFERRED

    def _translate_simple_dax_condition(self, condition: str) -> Optional[str]:
        """Translate a simple DAX filter predicate into SQL-safe text."""
        expr = " ".join(str(condition or "").split())
        if not expr:
            return None

        def _replace_column_ref(match: re.Match) -> str:
            table_name = str(match.group("table_q") or match.group("table") or "").strip()
            column_name = self._sanitize_identifier(match.group("col") or "")
            if table_name and self._behavior.enable_cross_table_joins:
                table_alias = self._sanitize_identifier(table_name).lower()
                return f"`{table_alias}`.`{column_name}`"
            return f"`{column_name}`"

        expr = re.sub(
            r"(?:'(?P<table_q>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\s*\[(?P<col>[^\]]+)\]",
            _replace_column_ref,
            expr,
        )
        expr = re.sub(r"(?i)\bTODAY\s*\(\s*\)", "current_date()", expr)

        if re.search(
            r"(?i)\b(TOTALYTD|CALCULATE|SAMEPERIODLASTYEAR|DATESYTD|FILTER|ALL|EARLIER|SUMX|AVERAGEX|IF|DIVIDE)\b",
            expr,
        ):
            return None

        if re.fullmatch(r"(?is)[A-Za-z0-9_`\"().,+\-*/%\s=<>!|&:'$]+", expr):
            return expr
        return None

    def _try_simple_var_return_to_sql(self, normalized_expr: str) -> Optional[str]:
        """Translate simple VAR...RETURN expressions via scalar inlining.

        Supports only scalar vars that can be translated by try_simple_dax_to_sql
        without requiring other variables or measure references.
        """
        expr = str(normalized_expr or "").strip()
        if not expr or "RETURN" not in expr.upper() or "VAR" not in expr.upper():
            return None

        m_return = re.search(r"(?is)\bRETURN\b\s*(.+)$", expr)
        if not m_return:
            return None
        return_expr = str(m_return.group(1) or "").strip()
        var_block = expr[: m_return.start()]

        var_matches = re.findall(r"(?is)\bVAR\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)(?=\bVAR\s+[A-Za-z_][A-Za-z0-9_]*\s*=|\Z)", var_block)
        if not var_matches:
            return None

        var_sql: dict[str, str] = {}
        for var_name, var_expr in var_matches:
            candidate = " ".join(str(var_expr or "").split()).strip()
            if not candidate:
                return None
            translated = self.try_simple_dax_to_sql(candidate)
            if not translated:
                return None
            # Keep this lightweight: only inline scalar-like translations.
            if re.search(r"(?i)\b(CASE|SELECT|FROM|JOIN)\b", translated):
                return None
            var_sql[var_name.lower()] = translated

        inlined = return_expr
        for var_name, translated in var_sql.items():
            inlined = re.sub(rf"(?i)\b{re.escape(var_name)}\b", f"({translated})", inlined)

        return self.try_simple_dax_to_sql(inlined)

    def parse_switch_ui_logic(self, dax_expression: str) -> Optional[dict[str, Any]]:
        """Parse UI text elements out of a SWITCH(SELECTEDVALUE(...)) DAX block.

        Returns a dictionary describing the lookup table components:
        {
           'column': 'Business_Unit',
           'dataset': 'Plant_BU_Mapping',
           'cases': [('USP', '...'), ('MSH', '...')],
           'default': '...',
        }
        Returns None if the expression does not match the expected pattern.
        """
        expr = str(dax_expression or "").strip()

        # Relaxed Regex to capture SWITCH(SELECTEDVALUE('Table'[Column]), ...)
        # Handles optional quotes around the table name and arbitrary arguments.
        pattern = r"(?is)^SWITCH\s*\(\s*SELECTEDVALUE\s*\(\s*(?:'(?P<table_q>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\s*\[(?P<col>[^\]]+)\]\s*(?:,\s*.*?)?\)\s*,(.+)\)$"
        m_switch = re.match(pattern, expr)
        if not m_switch:
            return None

        dataset = str(m_switch.group("table_q") or m_switch.group("table") or "").strip()
        column = str(m_switch.group("col")).strip()
        args_str = m_switch.group(4).strip()

        args = self.split_top_level_csv(args_str)
        cases = []
        default = None

        for i in range(0, len(args), 2):
            if i + 1 < len(args):
                key = args[i].strip()
                val = args[i+1].strip()
                # Remove surrounding quotes if present
                if key.startswith('"') and key.endswith('"'): key = key[1:-1]
                if key.startswith("'") and key.endswith("'"): key = key[1:-1]
                if val.startswith('"') and val.endswith('"'): val = val[1:-1]
                if val.startswith("'") and val.endswith("'"): val = val[1:-1]
                cases.append((key, val))
            else:
                default = args[i].strip()
                if default.startswith('"') and default.endswith('"'): default = default[1:-1]
                if default.startswith("'") and default.endswith("'"): default = default[1:-1]

        return {
            "dataset": dataset,
            "column": column,
            "cases": cases,
            "default": default
        }

    def generate_lookup_ddl(self, measure_name: str, lookup_dict: dict[str, Any], schema_prefix: str = "") -> str:
        """Generate a CREATE TABLE and INSERT script for a look up table.

        Args:
            measure_name: The name of the measure, used to postfix the table name.
            lookup_dict: The parsed output from `parse_switch_ui_logic`.
            schema_prefix: Optional prefix for the table name (e.g., 'workspace_xyz.public.').

        Returns:
            A string containing the SQL DDL commands to create and populate the lookup table.
        """
        base_dataset = self._sanitize_identifier(lookup_dict["dataset"]).lower()
        if not base_dataset:
            base_dataset = "generic"
        
        column_name = self._sanitize_identifier(lookup_dict["column"]).lower()
        if not column_name:
            column_name = "key"

        metric_name_clean = self._sanitize_identifier(measure_name).lower()
        table_name = f"{schema_prefix}{base_dataset}_{metric_name_clean}_callouts"

        output = [
            f"CREATE TABLE IF NOT EXISTS {table_name} (",
            f"  `{column_name}` STRING,",
            f"  `{metric_name_clean}_text` STRING",
            ");",
            ""
        ]

        # In Databricks, we often use INSERT INTO VALUES, or MERGE, but for simple lookup generations we can clear and insert.
        output.append(f"TRUNCATE TABLE {table_name};")
        
        if lookup_dict["cases"] or lookup_dict["default"] is not None:
            output.append(f"INSERT INTO {table_name} VALUES")
            
            values = []
            for key, val in lookup_dict["cases"]:
                safe_key = key.replace("'", "''")
                safe_val = val.replace("'", "''")
                values.append(f"  ('{safe_key}', '{safe_val}')")
                
            if lookup_dict["default"] is not None:
                safe_default = lookup_dict["default"].replace("'", "''")
                # Using a special key like '__DEFAULT__' or NULL depending on usage.
                # For Databricks Metric View logic, we need to bind strictly to keys. If a default applies to everything else,
                # the semantic layer will need to handle it via a COALESCE. We will store it for completeness.
                values.append(f"  ('__DEFAULT__', '{safe_default}')")

            output.append(",\n".join(values) + ";")
            
        return "\n".join(output)

