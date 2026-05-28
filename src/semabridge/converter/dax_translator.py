"""
DAX to SQL Translator.

Implements the "Tiered Safety" translation strategy to convert Fabric DAX expressions
into Snowflake-compatible SQL for Semantic Views.
"""

import os
import re
from typing import Optional, Tuple, Dict, List, Any

from semabridge.utils.logger import get_logger
from semabridge.utils.naming import sanitize_column, to_alias
from semabridge.converter.dax_rule_translator import is_simple_metric, rule_based_translation

logger = get_logger(__name__)


class DAXTranslationResult:
    """Result of a DAX translation attempt."""
    
    def __init__(self, sql: Optional[str], tier: int, original_dax: str):
        self.sql = sql
        self.tier = tier  # 0=Override, 1=Direct, 2=Branching/Arith, 3=Opaque (Failed)
        self.original_dax = original_dax
        self.is_success = sql is not None


class DAXTranslator:
    """
    Translates DAX expressions to SQL.
    
    Tier 1: Direct Aggregations (SUM, AVG, MIN, MAX, COUNT, DISTINCTCOUNT)
    Tier 2: Arithmetic & Branching (A + B, A / B, DIVIDE)
    Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD) - Window functions
    Tier 4: Complex (CALCULATE with filters, iterators) - AST/rules/LLM fallback
    """
    
    # Regex patterns for Tier 1
    # Matches: FUNC('Table'[Column]) or FUNC([Column])
    _TIER1_PATTERN = re.compile(
        r"^\s*(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*$",
        re.IGNORECASE
    )
    
    # Simple Arithmetic Patterns
    # Matches: [Measure1] + [Measure2] 
    # Matches: [Measure1] - [Measure2]
    # Matches: [Measure1] * [Measure2]
    # Matches: [Measure1] / [Measure2]
    # Very basic parser - assumes simple structure
    _ARITHMETIC_PATTERN = re.compile(
        r"^\s*(\[.+?\])\s*([\+\-\*\/])\s*(\[.+?\])\s*$",
        re.IGNORECASE
    )
    
    # DIVIDE functionality
    _DIVIDE_PATTERN = re.compile(
        r"^\s*DIVIDE\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*(?:,.+?)?\)\s*$",
        re.IGNORECASE
    )

    _MEASURE_REF_PATTERN = re.compile(r"\[([^\]]+)\]")

    CALENDAR_MAP = {
        "table": "CALENDAR",
        "date_col": "DATE",
        "year_col": "YEAR",
        "period_col": "PERIOD",
    }
    
    # Time Intelligence patterns that CAN be translated to Snowflake window functions
    TIME_INTEL_PATTERNS = {
        # TOTALYTD(SUM('Table'[Column]), 'Date'[Date])
        "YTD": re.compile(
            r"TOTALYTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
        "MTD": re.compile(
            r"TOTALMTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
        "QTD": re.compile(
            r"TOTALQTD\s*\(\s*(SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(?:'?[\w\s]+'?\[(.+?)\]|\[(.+?)\])\s*\)\s*,\s*'?(\w+)'?\[(\w+)\]",
            re.IGNORECASE
        ),
    }
    
    # Time Intelligence functions that require Date dimension context
    TIME_INTEL_FUNCTIONS = [
        "TOTALYTD", "TOTALMTD", "TOTALQTD", 
        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",
        "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
        "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"
    ]

    STRICT_BLOCKED_FUNCTIONS = (
        "SAMEPERIODLASTYEAR",
        "PREVIOUSYEAR",
        "PREVIOUSMONTH",
        "PREVIOUSQUARTER",
        "DATEADD",
        "DATESYTD",
        "DATESMTD",
        "DATESQTD",
        "PARALLELPERIOD",
        "OPENINGBALANCEYEAR",
        "CLOSINGBALANCEYEAR",
        "ALL",
        "ALLEXCEPT",
    )
    
    # Patterns that CANNOT be safely translated (require DAX engine evaluation)
    UNSUPPORTED_PATTERNS = [
        r"CALCULATE\s*\([^)]+,\s*FILTER\s*\(",          # CALCULATE with FILTER
        r"SUMX\s*\(\s*FILTER\s*\(",                      # SUMX over filtered table
        r"EARLIER\s*\(",                                 # Row context reference
        r"RANKX\s*\(",                                   # Ranking (requires full context)
        r"USERELATIONSHIP\s*\(",                         # Dynamic relationship
        r"CROSSFILTER\s*\(",                             # Cross filter modification
        r"ALL\s*\([^)]*\)\s*\)",                         # ALL alone is complex
        r"ALLEXCEPT\s*\(",                               # Removes filters except specified
        r"VALUES\s*\([^)]+\)\s*\)",                      # Context-dependent values
    ]
    
    def translate(self, 
                  dax: str, 
                  table_alias: str, 
                  dataset_name: str, 
                  metric_name: str = None,
                  metrics_context: List[Any] = None) -> DAXTranslationResult:
        """
        Translate a DAX expression to SQL.
        
        Args:
            dax: The DAX formula string
            table_alias: SQL alias for the main table (e.g. 'sales')
            dataset_name: Name of the dataset for context
            metric_name: Name of the current metric being translated
            metrics_context: List of SMLMetric objects to resolve dependencies
        """
        if not dax:
            return DAXTranslationResult(None, 3, "")
        
        clean_dax = dax.strip()
        
        if metrics_context:
            try:
                sql = self.translate_expr(clean_dax, metrics_context, table_alias)
                if sql and "[" not in sql and "]" not in sql and not any(f in sql.upper() for f in ["SUMX(", "FILTER(", "CALCULATE(", "DIVIDE("]):
                    return DAXTranslationResult(sql, 2, clean_dax)
            except Exception as e:
                logger.warning(f"Error in translate_expr fallback: {e}")

        
        # **NEW PRIMARY FLOW: Try deterministic translator first**
        # This enforces pipeline as single source of truth
        try:
            from semabridge.converter.deterministic_translator import DeterministicTranslator
            det_translator = DeterministicTranslator()
            det_result = det_translator.translate(
                clean_dax,
                table_alias,
                dataset_name,
                metric_name=metric_name
            )
            if det_result.is_success and det_result.sql:
                logger.info(f"✓ Deterministic translation successful: {det_result.sql[:60]}")
                return DAXTranslationResult(det_result.sql, det_result.tier, clean_dax)
            else:
                logger.debug(f"Deterministic translator returned no SQL, falling back to Tier logic")
        except Exception as e:
            logger.warning(f"Deterministic translator error, falling back to Tier logic: {e}")
        
        # Tier 1: Direct Aggregations
        tier1_sql = self._try_tier1(clean_dax, table_alias)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)

        if any(re.search(rf"\b{pattern}\b", clean_dax, re.IGNORECASE) for pattern in self.STRICT_BLOCKED_FUNCTIONS):
            logger.debug(f"Strict translator rejected unsupported DAX pattern: {clean_dax[:80]}...")
            return DAXTranslationResult(None, 4, clean_dax)

        strict_sql = self._try_strict_translation(clean_dax, table_alias, metrics_context)
        if strict_sql:
            return DAXTranslationResult(strict_sql, 2, clean_dax)

        if metrics_context:
            dependency_sql = self._try_dependency_translation(
                clean_dax,
                table_alias,
                dataset_name,
                metrics_context,
            )
            if dependency_sql:
                return DAXTranslationResult(dependency_sql, 2, clean_dax)
        
        # Tier 2: Branching & Arithmetic
        # e.g. [Net Sales] = [Gross Sales] - [Discounts]
        if metrics_context:
            tier2_sql = self._try_branching(clean_dax, metrics_context)
            if tier2_sql:
                return DAXTranslationResult(tier2_sql, 2, clean_dax)
        
        # Tier 3: Time Intelligence (TOTALYTD, TOTALMTD, TOTALQTD, SAMEPERIODLASTYEAR, etc.)
        # Uses the deterministic AST parser + Snowflake window function renderer.
        is_time_intel = any(func.upper() in dax.upper() for func in self.TIME_INTEL_FUNCTIONS)
        if is_time_intel:
            from semabridge.converter.dax_ast_parser import try_ast_translate
            resolved_measures = {}
            if metrics_context:
                for m in metrics_context:
                    if m.sql_expression:
                        resolved_measures[m.unique_name] = m.sql_expression
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                measure_sql_map=resolved_measures,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 3, clean_dax)

        # Tier 4: Complex CALCULATE / FILTER / ALL / ALLEXCEPT — attempt AST translation
        is_complex = any(
            re.search(pattern, clean_dax, re.IGNORECASE)
            for pattern in self.UNSUPPORTED_PATTERNS
        )
        if is_complex:
            from semabridge.converter.dax_ast_parser import try_ast_translate
            resolved_measures = {}
            if metrics_context:
                for m in metrics_context:
                    if m.sql_expression:
                        resolved_measures[m.unique_name] = m.sql_expression
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                measure_sql_map=resolved_measures,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 4, clean_dax)

        # Tier 5: LLM Fallback - Use Claude for complex expressions deterministic parsing couldn't handle
        # Only attempt if LLM is available and enabled
        llm_result = self._try_llm_fallback(
            clean_dax, 
            table_alias,
            dataset_name,
            metric_name
        )
        if llm_result:
            return llm_result

        # No translation possible — return None (all tiers exhausted)
        return DAXTranslationResult(None, 4, clean_dax)

    def _try_dependency_translation(
        self,
        dax: str,
        table_alias: str,
        dataset_name: str,
        metrics_context: List[Any],
        visiting: Optional[set[str]] = None,
    ) -> Optional[str]:
        """Resolve pure measure references and arithmetic chains by expanding dependencies first."""
        if not dax or not metrics_context:
            return None

        visiting = visiting or set()
        clean_dax = " ".join((dax or "").split())
        metric_index = {
            str(getattr(m, "unique_name", "")).casefold(): m
            for m in metrics_context
            if getattr(m, "unique_name", None)
        }

        def resolve_measure(name: str) -> Optional[str]:
            key = (name or "").strip().casefold()
            if not key:
                return None
            if key in visiting:
                logger.warning(f"Circular measure dependency detected for '{name}'")
                return None

            metric = metric_index.get(key)
            if not metric:
                return None

            existing_sql = (getattr(metric, "sql_expression", None) or "").strip()
            if existing_sql:
                return self._strip_outer_parens(existing_sql)

            metric_expr = (getattr(metric, "expression", None) or "").strip()
            if not metric_expr:
                return None

            visiting.add(key)
            try:
                tier1 = self._try_tier1(metric_expr, table_alias)
                if tier1:
                    return self._strip_outer_parens(tier1)

                strict = self._try_strict_translation(metric_expr, table_alias, metrics_context)
                if strict:
                    return self._strip_outer_parens(strict)

                nested = self._try_dependency_translation(
                    metric_expr,
                    table_alias,
                    dataset_name,
                    metrics_context,
                    visiting,
                )
                if nested:
                    return self._strip_outer_parens(nested)

                return None
            finally:
                visiting.remove(key)

        def split_args(args_text: str) -> List[str]:
            args: List[str] = []
            current: List[str] = []
            depth = 0
            for ch in args_text:
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth > 0:
                    depth -= 1
                elif ch == "," and depth == 0:
                    value = "".join(current).strip()
                    if value:
                        args.append(value)
                    current = []
                    continue
                current.append(ch)
            tail = "".join(current).strip()
            if tail:
                args.append(tail)
            return args

        pure_ref = re.fullmatch(r"\[([^\]]+)\]", clean_dax)
        if pure_ref:
            return resolve_measure(pure_ref.group(1))

        if clean_dax.upper().startswith("DIVIDE(") and clean_dax.endswith(")"):
            args = split_args(clean_dax[7:-1])
            if len(args) >= 2:
                numerator_sql = self._try_dependency_translation(args[0], table_alias, dataset_name, metrics_context, visiting)
                denominator_sql = self._try_dependency_translation(args[1], table_alias, dataset_name, metrics_context, visiting)
                if numerator_sql and denominator_sql:
                    alt = args[2].strip() if len(args) >= 3 and args[2].strip() else "0"
                    return f"COALESCE(({numerator_sql}) / NULLIF(({denominator_sql}), 0), {alt})"

        refs = [match.group(1).strip() for match in self._MEASURE_REF_PATTERN.finditer(clean_dax)]
        refs = [ref for ref in refs if ref]
        if refs:
            replaced = clean_dax
            for ref in refs:
                dep_sql = resolve_measure(ref)
                if not dep_sql:
                    return None
                replaced = re.sub(rf"\[{re.escape(ref)}\]", f"({dep_sql})", replaced, flags=re.IGNORECASE)

            if replaced != clean_dax:
                upper_replaced = replaced.upper()
                if any(keyword in upper_replaced for keyword in ("CALCULATE", "SAMEPERIODLASTYEAR", "DATEADD", "DATESYTD", "TOTALYTD", "IF(", "BLANK(")):
                    return None
                if "[" in replaced or "]" in replaced:
                    return None
                return replaced

        return None

    def _split_dax_arguments(self, args_text: str) -> List[str]:
        """Split a comma-separated DAX argument list at top level only."""
        args: List[str] = []
        current: List[str] = []
        depth = 0
        in_string = False
        string_quote = ""

        for ch in args_text:
            if in_string:
                current.append(ch)
                if ch == string_quote:
                    in_string = False
                continue

            if ch in ('"', "'"):
                in_string = True
                string_quote = ch
                current.append(ch)
                continue

            if ch == "(":
                depth += 1
            elif ch == ")" and depth > 0:
                depth -= 1
            elif ch == "," and depth == 0:
                value = "".join(current).strip()
                if value:
                    args.append(value)
                current = []
                continue

            current.append(ch)

        tail = "".join(current).strip()
        if tail:
            args.append(tail)
        return args

    def _strip_outer_parens(self, expr: str) -> str:
        """Remove wrapping parentheses when they fully enclose the expression."""
        clean = (expr or "").strip()
        while clean.startswith("(") and clean.endswith(")"):
            depth = 0
            wrapped = True
            for index, ch in enumerate(clean):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0 and index != len(clean) - 1:
                        wrapped = False
                        break
            if not wrapped or depth != 0:
                break
            clean = clean[1:-1].strip()
        return clean

    def _parse_sql_aggregation(self, sql: str) -> Optional[Tuple[str, str]]:
        """Return the aggregate function and inner expression for simple SQL."""
        clean = self._strip_outer_parens(sql)
        distinct_match = re.match(r"^COUNT\s*\(\s*DISTINCT\s+(.+?)\s*\)$", clean, re.IGNORECASE)
        if distinct_match:
            return ("COUNT_DISTINCT", distinct_match.group(1).strip())

        match = re.match(r"^(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(.+?)\s*\)$", clean, re.IGNORECASE)
        if match:
            return (match.group(1).upper(), match.group(2).strip())
        return None

    def _extract_simple_filter_predicate(self, filter_expr: str) -> Optional[str]:
        """Extract a direct predicate from FILTER(table, predicate) when safely possible."""
        clean_filter = self._strip_outer_parens(filter_expr)
        if not re.match(r"^FILTER\s*\(", clean_filter, re.IGNORECASE):
            return clean_filter

        args = self._split_dax_arguments(clean_filter[len("FILTER("):-1]) if clean_filter.endswith(")") else []
        if len(args) != 2:
            return None

        predicate = self._strip_outer_parens(args[1])
        # Keep strict mode deterministic: reject nested table expressions/functions.
        if re.search(r"\b(FILTER|ALL|ALLEXCEPT|SAMEPERIODLASTYEAR|PREVIOUSYEAR|PREVIOUSMONTH|PREVIOUSQUARTER|DATEADD|DATESYTD|DATESMTD|DATESQTD|PARALLELPERIOD|OPENINGBALANCEYEAR|CLOSINGBALANCEYEAR)\b", predicate, re.IGNORECASE):
            return None
        return predicate

    def _resolve_measure_sql(self, name: str, metrics_context: Optional[List[Any]], table_alias: str) -> Optional[str]:
        """Resolve a measure name to SQL using the provided dependency cache."""
        if not metrics_context:
            return None

        for metric in metrics_context:
            if str(getattr(metric, "unique_name", "")).casefold() != str(name or "").casefold():
                continue

            sql_expression = (getattr(metric, "sql_expression", None) or "").strip()
            if sql_expression:
                return self._strip_outer_parens(sql_expression)

            metric_expr = (getattr(metric, "expression", None) or "").strip()
            if metric_expr:
                direct_sql = self._try_tier1(metric_expr, table_alias)
                if direct_sql:
                    return self._strip_outer_parens(direct_sql)

                strict_sql = self._try_strict_translation(metric_expr, table_alias, metrics_context)
                if strict_sql:
                    return self._strip_outer_parens(strict_sql)

                dependency_sql = self._try_dependency_translation(
                    metric_expr,
                    table_alias,
                    "",
                    metrics_context,
                    visiting=set(),
                )
                if dependency_sql:
                    return self._strip_outer_parens(dependency_sql)

                translated = self._try_branching(metric_expr, metrics_context)
                if translated:
                    return self._strip_outer_parens(translated)
            return None

        return None

    def _try_strict_translation(
        self,
        dax: str,
        table_alias: str,
        metrics_context: Optional[List[Any]] = None,
    ) -> Optional[str]:
        """Translate only the two strict forms supported by the prompt."""
        clean_dax = " ".join((dax or "").split())
        upper_dax = clean_dax.upper()

        if upper_dax.startswith("TOTALYTD(") and clean_dax.endswith(")"):
            args = self._split_dax_arguments(clean_dax[len("TOTALYTD("):-1])
            if len(args) != 2:
                return None

            base_expr = self._strip_outer_parens(args[0])
            base_sql = self._try_tier1(base_expr, table_alias)
            if not base_sql and re.fullmatch(r"\[([^\]]+)\]", base_expr):
                base_sql = self._resolve_measure_sql(base_expr[1:-1], metrics_context, table_alias)
            if not base_sql and metrics_context:
                base_sql = self._try_dependency_translation(
                    base_expr,
                    table_alias,
                    "",
                    metrics_context,
                    visiting=set(),
                )
            if not base_sql and metrics_context:
                base_sql = self._try_branching(base_expr, metrics_context)
            if not base_sql:
                return None

            base_sql = self._strip_outer_parens(base_sql)
            parsed_agg = self._parse_sql_aggregation(base_sql)
            
            date_col_ref = args[1].strip()
            date_match = re.match(r"^(?:'([^']+)'|([a-zA-Z0-9_#@ -]+))\[([^\]]+)\]$", date_col_ref)
            if date_match:
                table_name = date_match.group(1) or date_match.group(2)
                cal_tbl = sanitize_column(table_name, force_uppercase=True)
            else:
                cal_tbl = "CALENDAR"

            if parsed_agg:
                agg_func, value_expr = parsed_agg
                sql_agg = "COUNT(DISTINCT" if agg_func == "COUNT_DISTINCT" else agg_func
                if agg_func == "COUNT_DISTINCT":
                    return (
                        f"COUNT(DISTINCT {value_expr}) OVER "
                        f"(PARTITION BY {cal_tbl}.YEAR ORDER BY {cal_tbl}.PERIOD)"
                    )
                return (
                    f"{sql_agg}({value_expr}) OVER "
                    f"(PARTITION BY {cal_tbl}.YEAR ORDER BY {cal_tbl}.PERIOD)"
                )

            return f"SUM({base_sql}) OVER (PARTITION BY {cal_tbl}.YEAR ORDER BY {cal_tbl}.PERIOD)"

        if upper_dax.startswith("CALCULATE(") and clean_dax.endswith(")"):
            args = self._split_dax_arguments(clean_dax[len("CALCULATE("):-1])
            if len(args) != 2:
                return None

            base_expr = self._strip_outer_parens(args[0])
            filter_expr = self._strip_outer_parens(args[1])
            filter_expr = self._extract_simple_filter_predicate(filter_expr)
            if not filter_expr:
                return None

            if re.search(
                r"\b(FILTER|ALL|ALLEXCEPT|SAMEPERIODLASTYEAR|PREVIOUSYEAR|PREVIOUSMONTH|PREVIOUSQUARTER|DATEADD|DATESYTD|DATESMTD|DATESQTD|PARALLELPERIOD|OPENINGBALANCEYEAR|CLOSINGBALANCEYEAR)\b",
                filter_expr,
                re.IGNORECASE,
            ):
                return None

            filter_match = re.match(
                r"^(?:'(?P<table_quoted>[^']+)'|(?P<table>[A-Za-z_][A-Za-z0-9_]*))\[(?P<column>[^\]]+)\]\s*=\s*(?P<value>.+)$",
                filter_expr,
                re.IGNORECASE,
            )
            if not filter_match:
                return None

            base_sql = self._try_tier1(base_expr, table_alias)
            if not base_sql and re.fullmatch(r"\[([^\]]+)\]", base_expr):
                base_sql = self._resolve_measure_sql(base_expr[1:-1], metrics_context, table_alias)
            if not base_sql:
                return None

            agg = self._parse_sql_aggregation(base_sql)
            if not agg:
                return None

            agg_func, value_expr = agg
            table_name = filter_match.group("table_quoted") or filter_match.group("table") or ""
            column_name = filter_match.group("column")
            value_text = filter_match.group("value").strip()

            if re.fullmatch(r'"(?:[^"\\]|\\.)*"', value_text):
                literal = "'" + value_text[1:-1].replace("'", "''") + "'"
            elif re.fullmatch(r"'(?:[^'\\]|\\.)*'", value_text):
                literal = value_text
            elif re.fullmatch(r"-?\d+(?:\.\d+)?", value_text):
                literal = value_text
            else:
                return None

            lhs_table = sanitize_column(table_name, force_uppercase=True)
            lhs_column = sanitize_column(column_name, force_uppercase=True)
            condition_sql = f"{lhs_table}.{lhs_column} = {literal}"

            if agg_func == "SUM":
                return f"SUM(CASE WHEN {condition_sql} THEN {value_expr} ELSE 0 END)"
            if agg_func == "COUNT":
                return f"COUNT(CASE WHEN {condition_sql} THEN {value_expr} END)"
            if agg_func == "COUNT_DISTINCT":
                return f"COUNT(DISTINCT CASE WHEN {condition_sql} THEN {value_expr} END)"
            if agg_func == "AVG":
                return f"AVG(CASE WHEN {condition_sql} THEN {value_expr} END)"
            if agg_func in {"MIN", "MAX"}:
                return f"{agg_func}(CASE WHEN {condition_sql} THEN {value_expr} END)"

        return None
    def _try_llm_fallback(self,
                          dax: str,
                          table_alias: str,
                          dataset_name: str,
                          metric_name: Optional[str] = None) -> Optional[DAXTranslationResult]:
        """
        Attempt Tier 5 LLM translation for complex expressions.
        
        First checks if the metric is simple enough for rule-based translation.
        Only uses Google Gemini as a fallback for truly complex expressions.
        Returns None if LLM is not available or declines to translate.
        """
        # TIER 4.5: Check if metric is simple enough for rule-based translation
        # This dramatically reduces LLM API usage by 60-80%
        if is_simple_metric(dax):
            logger.info(f"🟢 Metric classified as SIMPLE - using rule-based translation: {metric_name or dax[:50]}")
            
            # Try rule-based translation
            sql = rule_based_translation(dax, table_alias)
            if sql:
                logger.debug(f"   ✓ Rule-based translation succeeded: {sql[:80]}")
                return DAXTranslationResult(sql, 4, dax)  # Tier 4 for rule-based (deterministic)
            else:
                logger.debug(f"   ✗ Rule-based translation failed, will fall through to LLM")
        else:
            logger.info(f"🟠 Metric classified as COMPLEX - requesting LLM translation: {metric_name or dax[:50]}")
        
        # PRIORITY 0: OpenAI translation first if OPENAI_API_KEY is configured
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key:
            try:
                from openai import OpenAI
                model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
                client = OpenAI(api_key=openai_api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
                
                logger.info(f"🤖 Calling OpenAI API for measure '{metric_name or dax[:30]}'...")
                
                prompt = (
                    "Dialect: Snowflake Semantic View METRICS clause\n"
                    f"Metric name: {metric_name or 'unnamed'}\n"
                    f"Default table alias: {table_alias}\n"
                    "Rules:\n"
                    "- Return only a single SQL expression, no explanation.\n"
                    "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
                    "- Do not nest aggregate functions like SUM(MAX(...)).\n"
                    "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN column ELSE 0 END).\n"
                    "- Quote identifiers only when needed as ALIAS.\"COLUMN\"; uppercase Snowflake column names.\n"
                    "- If a pattern is impossible, return CAST(NULL AS DOUBLE).\n"
                    "DAX:\n"
                    f"{dax}"
                )
                
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. "
                                "Return only one SQL expression. Do not use markdown."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                    max_tokens=int(os.getenv("OPENAI_DAX_MAX_TOKENS", "500")),
                    timeout=float(os.getenv("OPENAI_DAX_TIMEOUT", "30")),
                )
                sql = (response.choices[0].message.content or "").strip()
                
                if sql.startswith("```"):
                    sql = re.sub(r"^```(?:sql|python|.*?)\n", "", sql, flags=re.IGNORECASE)
                    sql = re.sub(r"\n```$", "", sql, flags=re.IGNORECASE)
                    sql = sql.strip()
                
                if sql:
                    sql_upper = sql.upper()
                    forbidden = (
                        " SELECT ",
                        "(SELECT",
                        " FROM ",
                        " JOIN ",
                        " WITH ",
                        " DROP ",
                        " DELETE ",
                        " TRUNCATE ",
                        " INSERT ",
                        " UPDATE ",
                        " ALTER ",
                        ";",
                    )
                    padded = f" {sql_upper} "
                    if any(token in padded for token in forbidden):
                        logger.warning(
                            "OpenAI DAX translation rejected (forbidden SQL tokens) for metric '%s': %s",
                            metric_name,
                            sql[:120],
                        )
                    else:
                        logger.info(f"✓ [{metric_name or dax[:30]}]: OpenAI translation")
                        return DAXTranslationResult(sql, 5, dax)
            except Exception as exc:
                logger.warning("OpenAI individual translation fallback failed: %s", exc)

        # Tier 5: LLM Fallback - Use Gemini for genuinely complex expressions
        try:
            from semabridge.converter.gemini_dax_translator import get_gemini_translator
            
            translator = get_gemini_translator()
            if not translator.api_key:
                logger.debug("LLM API key not configured")
                return None
            
            # Attempt LLM translation
            llm_result = translator.translate(
                dax=dax,
                table_alias=table_alias,
                dataset_name=dataset_name,
                metric_name=metric_name
            )
            
            # Only use LLM result if:
            # 1. Translation succeeded (is_valid=True)
            # 2. Confidence is acceptable (>= 0.55)
            if llm_result.is_valid and llm_result.sql and llm_result.confidence >= 0.55:
                logger.info(
                    f"LLM translation accepted for '{metric_name}' "
                    f"(confidence: {llm_result.confidence:.2f}, tier: 5)"
                )
                return DAXTranslationResult(llm_result.sql, 5, dax)
            elif llm_result.sql and llm_result.confidence > 0.4:
                # Low confidence - log but don't use
                logger.warning(
                    f"LLM translation low confidence for '{metric_name}': "
                    f"confidence={llm_result.confidence:.2f}. Falling back to None. "
                    f"SQL was: {llm_result.sql[:100]}..."
                )
                return None
            else:
                logger.debug(
                    f"LLM translation declined for '{metric_name}': "
                    f"confidence={llm_result.confidence:.2f}. Error: {llm_result.error}"
                )
                return None
                
        except ImportError:
            logger.debug("LLM translator module not available")
            return None
        except Exception as e:
            logger.warning(f"Unexpected error in LLM fallback: {str(e)}")
            return None
    
    def batch_translate_tier5(self,
                             metrics_list: List[Tuple[str, str, str, str]]) -> Dict[str, Optional[DAXTranslationResult]]:
        """
        Batch translate multiple metrics that failed Tier 1-4 using LLM (Tier 5).
        
        First separates simple metrics (which use rule-based translation) from complex ones
        (which need LLM). This dramatically reduces API calls by preventing simple metrics
        from being sent to Gemini.
        
        When there are complex metrics, batches them into groups to minimize API calls
        (batching 20+ metrics into a single request).
        
        Args:
            metrics_list: List of (metric_name, dax, table_alias, dataset_name) tuples
            
        Returns:
            Dict of metric_name -> DAXTranslationResult (or None if no LLM result)
        """
        if not metrics_list:
            return {}
        
        results = {}
        
        # CLASSIFICATION STEP: Separate simple from complex metrics
        simple_metrics = []
        complex_metrics = []
        simple_failed_for_llm = []
        
        for metric_name, dax, table_alias, dataset_name in metrics_list:
            if is_simple_metric(dax):
                simple_metrics.append((metric_name, dax, table_alias, dataset_name))
            else:
                complex_metrics.append((metric_name, dax, table_alias, dataset_name))
        
        logger.info(
            f"🔄 Batch processing {len(metrics_list)} metrics:\n"
            f"   ├─ SIMPLE (rule-based): {len(simple_metrics)} metrics\n"
            f"   └─ COMPLEX (LLM): {len(complex_metrics)} metrics"
        )
        
        # RULE-BASED TRANSLATION: Process simple metrics without API calls
        simple_api_calls = 0
        for metric_name, dax, table_alias, dataset_name in simple_metrics:
            sql = rule_based_translation(dax, table_alias)
            if sql:
                results[metric_name] = DAXTranslationResult(sql, 4, dax)
                logger.debug(f"   ✓ [{metric_name}] Rule-based translation: {sql[:60]}...")
            else:
                # IMPORTANT: do not drop simple metrics when deterministic rules fail.
                # Escalate them to Tier-5 LLM fallback.
                simple_failed_for_llm.append((metric_name, dax, table_alias, dataset_name))
                logger.debug(
                    f"   ⚠ [{metric_name}] Rule-based translation failed; escalating to LLM"
                )
        
        llm_candidates = complex_metrics + simple_failed_for_llm
        
        # ─────────────────────────────────────────────────────────
        # PRIORITY 0: OPENAI BATCH
        # ─────────────────────────────────────────────────────────
        openai_api_key = os.getenv("OPENAI_API_KEY")
        openai_translated = {}
        import json
        
        if openai_api_key and llm_candidates:
            try:
                from openai import OpenAI
                model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
                client = OpenAI(api_key=openai_api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
                
                # Chunk candidates into batches of 20
                chunk_size = 20
                for start in range(0, len(llm_candidates), chunk_size):
                    chunk = llm_candidates[start:start + chunk_size]
                    logger.info(f"🤖 Calling OpenAI API for batch of {len(chunk)} measures...")
                    
                    # Build batch prompt
                    metric_lines = []
                    for metric_name, dax, alias, dataset in chunk:
                        dax_clean = " ".join(dax.split())
                        metric_lines.append(
                            f'{{"name":"{metric_name}","dataset":"{dataset}","table_alias":"{alias}","dax":"{dax_clean}"}}'
                        )
                    
                    prompt = (
                        "Dialect: Snowflake Semantic View METRICS clause\n"
                        "Rules:\n"
                        "- Return ONLY valid JSON object mapping metric name to SQL expression.\n"
                        "- No markdown, no extra keys, no prose.\n"
                        "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
                        "- Do not nest aggregate functions like SUM(MAX(...)).\n"
                        "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
                        "- Quote identifiers only when needed as ALIAS.\"COLUMN\"; uppercase Snowflake column names.\n"
                        "- If a pattern is impossible in a metric expression, return CAST(NULL AS DOUBLE).\n"
                        "Metrics:\n"
                        + "\n".join(metric_lines)
                    )
                    
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. "
                                    "Return JSON only mapping measure names to translated SQL expressions."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                        max_tokens=int(os.getenv("OPENAI_DAX_BATCH_MAX_TOKENS", "2200")),
                        timeout=float(os.getenv("OPENAI_DAX_TIMEOUT", "30")),
                    )
                    
                    payload = (response.choices[0].message.content or "").strip()
                    logger.info(f"✅ OpenAI batch response received for {len(chunk)} measures")
                    
                    # Parse JSON payload
                    if payload.startswith("```"):
                        payload = re.sub(r"^```(?:json|sql|python|.*?)\n", "", payload, flags=re.IGNORECASE)
                        payload = re.sub(r"\n```$", "", payload, flags=re.IGNORECASE)
                        payload = payload.strip()
                    payload = re.sub(r"^json\s*", "", payload, flags=re.IGNORECASE)
                    
                    parsed = {}
                    try:
                        parsed = json.loads(payload)
                    except Exception:
                        pass
                    
                    if isinstance(parsed, dict):
                        for metric_name, dax, alias, dataset in chunk:
                            sql = parsed.get(metric_name)
                            if sql:
                                sql = sql.strip()
                                if sql.startswith("```"):
                                    sql = re.sub(r"^```(?:sql|python|.*?)\n", "", sql, flags=re.IGNORECASE)
                                    sql = re.sub(r"\n```$", "", sql, flags=re.IGNORECASE)
                                    sql = sql.strip()
                                
                                # Safety validation
                                sql_upper = sql.upper()
                                forbidden = (
                                    " SELECT ", "(SELECT", " FROM ", " JOIN ", " WITH ", " OVER ",
                                    " DROP ", " DELETE ", " TRUNCATE ", " INSERT ", " UPDATE ", " ALTER ", ";"
                                )
                                padded = f" {sql_upper} "
                                if any(token in padded for token in forbidden):
                                    logger.warning(
                                        "OpenAI batch translation rejected (forbidden SQL tokens) for metric '%s': %s",
                                        metric_name,
                                        sql[:120],
                                    )
                                else:
                                    logger.info(f"✓ [{metric_name}]: OpenAI translation")
                                    results[metric_name] = DAXTranslationResult(sql, 5, dax)
                                    openai_translated[metric_name] = sql
                
                # Remove successfully translated candidates so they aren't processed by Gemini
                llm_candidates = [c for c in llm_candidates if c[0] not in openai_translated]
                
            except Exception as exc:
                logger.error(f"OpenAI batch translation failed: {exc}")
        
        # LLM TRANSLATION: Only send remaining complex metrics to Gemini
        if llm_candidates:
            try:
                from semabridge.converter.gemini_dax_translator import get_gemini_translator
                
                translator = get_gemini_translator()
                if not translator.api_key:
                    logger.debug("LLM API key not configured for batch translation")
                    for metric_name, _, _, _ in llm_candidates:
                        if metric_name not in results:
                            results[metric_name] = None
                    return results
                
                # Prepare batch for Gemini translator (only complex metrics)
                # Format: (metric_name, dax, table_alias, dataset_name, None)
                batch = [
                    (metric_name, dax, table_alias, dataset_name, None)
                    for metric_name, dax, table_alias, dataset_name in llm_candidates
                ]
                
                logger.info(
                    f"🔄 Batch translating {len(batch)} COMPLEX metrics via Tier 5 LLM "
                    f"(expected API calls: {(len(batch) + 19) // 20}) - "
                    f"API CALL REDUCTION: {len(simple_metrics) - len(simple_failed_for_llm) + len(openai_translated)} / {len(metrics_list)} metrics skipped LLM"
                )
                
                # Call batch translation
                batch_result = translator.translate_batch(batch, batch_size=20)
                simple_api_calls = batch_result.api_calls
                
                # Process results - convert to DAXTranslationResult with confidence filtering
                for metric_name, gemini_result in batch_result.results.items():
                    if gemini_result.is_valid and gemini_result.sql and gemini_result.confidence >= 0.55:
                        # High confidence - use result
                        results[metric_name] = DAXTranslationResult(gemini_result.sql, 5, 
                                                                    next((dax for name, dax, _, _ in llm_candidates if name == metric_name), ""))
                        logger.debug(f"   ✓ [{metric_name}] LLM translated (conf: {gemini_result.confidence:.2f})")
                    elif gemini_result.sql and gemini_result.confidence > 0.4:
                        # Low confidence - log warning but don't use
                        logger.warning(
                            f"   ⚠️ [{metric_name}] Low confidence: {gemini_result.confidence:.2f}"
                        )
                        results[metric_name] = None
                    else:
                        # Failed translation
                        logger.debug(f"   ❌ [{metric_name}] Could not translate: {gemini_result.error}")
                        results[metric_name] = None
                
                # Log summary with classification insight
                successful = sum(1 for r in results.values() if r is not None)
                if llm_candidates:
                    api_call_reduction = (simple_api_calls / len(metrics_list)) * 100
                    quota_reduction = ((len(simple_metrics) - len(simple_failed_for_llm) + len(openai_translated)) / len(metrics_list)) * 100
                else:
                    api_call_reduction = 0
                    quota_reduction = 100
                
                logger.info(
                    f"✅ Batch translation complete:\n"
                    f"   ├─ Total metrics: {len(metrics_list)}\n"
                    f"   ├─ Simple (no API calls): {len(simple_metrics) - len(simple_failed_for_llm)} ({(len(simple_metrics) - len(simple_failed_for_llm)) / len(metrics_list) * 100:.0f}%)\n"
                    f"   ├─ LLM candidates: {len(llm_candidates)} ({len(llm_candidates) / len(metrics_list) * 100:.0f}%)\n"
                    f"   ├─ API calls: {simple_api_calls} (vs {len(metrics_list)} per-metric)\n"
                    f"   ├─ Successful: {successful}/{len(metrics_list)}\n"
                    f"   └─ QUOTA REDUCTION: {quota_reduction:.0f}% metrics avoided LLM calls"
                )
                
                return results
                
            except ImportError:
                logger.debug("Batch LLM translator not available")
                for metric_name, _, _, _ in llm_candidates:
                    if metric_name not in results:
                        results[metric_name] = None
                return results
            except Exception as e:
                logger.error(f"Unexpected error in batch Tier 5 translation: {str(e)}")
                for metric_name, _, _, _ in llm_candidates:
                    if metric_name not in results:
                        results[metric_name] = None
                return results
        else:
            # All metrics were simple or translated by OpenAI, no Gemini needed
            successful = sum(1 for r in results.values() if r is not None)
            logger.info(
                f"✅ Batch translation complete:\n"
                f"   ├─ Total metrics: {len(metrics_list)}\n"
                f"   ├─ Successful: {successful}/{len(metrics_list)}"
            )
            return results
    
    def _try_tier1(self, dax: str, table_alias: str) -> Optional[str]:
        """Attempt Tier 1 translation."""
        match = self._TIER1_PATTERN.match(dax)
        if not match:
            return None
        
        func = match.group(1).upper()
        # Group 2 has Table[Column] format col name, Group 3 has [Column] format
        col_name = match.group(2) or match.group(3)
        
        # Map DAX function to SQL function
        func_map = {
            "SUM": "SUM",
            "AVERAGE": "AVG",
            "MIN": "MIN",
            "MAX": "MAX",
            "COUNT": "COUNT",
            "DISTINCTCOUNT": "COUNT(DISTINCT {col})"
        }
        
        col_ref = f"{table_alias}.{self._quote(col_name)}"
        sql_template = func_map.get(func)
        
        if not sql_template:
            return None
            
        if "{col}" in sql_template:
            return sql_template.format(col=col_ref)
        else:
            return f"{sql_template}({col_ref})"
    
    def _try_branching(self, dax: str, metrics: List[Any]) -> Optional[str]:
        """
        Attempt to resolve references to other measures.
        Handles simple cases:
        1. Single measure ref: [Measure]
        2. Simple arithmetic: [A] + [B]
        3. DIVIDE: DIVIDE([A], [B])
        """
        
        if not dax or not metrics:
            return None

        metric_index = {
            str(getattr(m, "unique_name", "")).casefold(): m
            for m in metrics
            if getattr(m, "unique_name", None)
        }
        cache: Dict[str, str] = {}

        def split_args(args_text: str) -> List[str]:
            args: List[str] = []
            current: List[str] = []
            depth = 0
            for ch in args_text:
                if ch == "(":
                    depth += 1
                elif ch == ")" and depth > 0:
                    depth -= 1
                elif ch == "," and depth == 0:
                    args.append("".join(current).strip())
                    current = []
                    continue
                current.append(ch)
            tail = "".join(current).strip()
            if tail:
                args.append(tail)
            return args

        def extract_measure_refs(expr: str) -> List[str]:
            refs: List[str] = []
            for match in self._MEASURE_REF_PATTERN.finditer(expr or ""):
                start = match.start()
                if start > 0 and expr[start - 1] == "'":
                    # Skip table-qualified column refs like 'Table'[Column]
                    continue
                refs.append(match.group(1).strip())
            return refs

        def replace_measure_refs(expr: str, visiting: set[str]) -> Optional[str]:
            replaced = expr
            refs = extract_measure_refs(expr)
            for ref in refs:
                dep_sql = resolve_measure_sql(ref, visiting)
                if not dep_sql:
                    return None
                token_pattern = re.compile(rf"\[{re.escape(ref)}\]", re.IGNORECASE)
                replaced = token_pattern.sub(f"({dep_sql})", replaced)
            return replaced

        def resolve_measure_sql(name: str, visiting: set[str]) -> Optional[str]:
            key = (name or "").strip().casefold()
            if not key:
                return None
            if key in cache:
                return cache[key]
            if key in visiting:
                logger.warning(f"Circular measure dependency detected for '{name}'")
                return None

            metric = metric_index.get(key)
            if not metric:
                return None

            existing_sql = (getattr(metric, "sql_expression", None) or "").strip()
            if existing_sql:
                cache[key] = existing_sql
                return existing_sql

            metric_expr = (getattr(metric, "expression", None) or "").strip()
            if not metric_expr:
                return None

            visiting.add(key)
            try:
                alias = to_alias(getattr(metric, "dataset", "") or "fact")

                tier1 = self._try_tier1(metric_expr, alias)
                if tier1:
                    cache[key] = tier1
                    return tier1

                resolved = resolve_expression(metric_expr, alias, visiting)
                if resolved:
                    cache[key] = resolved
                    return resolved

                return None
            finally:
                visiting.remove(key)

        def resolve_expression(expr: str, table_alias: str, visiting: set[str]) -> Optional[str]:
            clean_expr = (expr or "").strip()
            if not clean_expr:
                return None

            # Handle DIVIDE(A, B [, alt]) with safe division semantics.
            if clean_expr.upper().startswith("DIVIDE(") and clean_expr.endswith(")"):
                inner = clean_expr[7:-1]
                args = split_args(inner)
                if len(args) >= 2:
                    numerator_sql = replace_measure_refs(args[0], visiting)
                    denominator_sql = replace_measure_refs(args[1], visiting)
                    if not numerator_sql or not denominator_sql:
                        return None
                    alt = args[2].strip() if len(args) >= 3 and args[2].strip() else "0"
                    return (
                        f"COALESCE(({numerator_sql}) / NULLIF(({denominator_sql}), 0), {alt})"
                    )

            # Handle TOTALYTD(Expression, Date) using metadata injection.
            if clean_expr.upper().startswith("TOTALYTD(") and clean_expr.endswith(")"):
                inner = clean_expr[9:-1]
                args = split_args(inner)
                if args:
                    inner_expr = args[0].strip()
                    inner_sql = (
                        self._try_tier1(inner_expr, table_alias)
                        or replace_measure_refs(inner_expr, visiting)
                        or self._try_dependency_translation(
                            inner_expr,
                            table_alias,
                            "",
                            metrics,
                            visiting=set(),
                        )
                    )
                    if inner_sql:
                        inner_sql = self._strip_outer_parens(inner_sql)
                        parsed_agg = self._parse_sql_aggregation(inner_sql)

                        # Snowflake semantic metric windows are safest when PARTITION/ORDER
                        # columns come from the same metric entity alias.
                        year_ref = f"{table_alias}.YEAR"
                        period_ref = f"{table_alias}.PERIOD"
                        if parsed_agg:
                            agg_func, value_expr = parsed_agg
                            if agg_func == "COUNT_DISTINCT":
                                return (
                                    f"COUNT(DISTINCT {value_expr}) OVER "
                                    f"(PARTITION BY {year_ref} ORDER BY {period_ref})"
                                )
                            return (
                                f"{agg_func}({value_expr}) OVER "
                                f"(PARTITION BY {year_ref} ORDER BY {period_ref})"
                            )

                        return (
                            f"SUM({inner_sql}) OVER (PARTITION BY {year_ref} "
                            f"ORDER BY {period_ref})"
                        )

            # Generic arithmetic replacement for [A] +/-/*// [B].
            replaced = replace_measure_refs(clean_expr, visiting)
            if replaced and replaced != clean_expr:
                # Do not emit partially-rewritten DAX function syntax as SQL.
                # If DAX-only keywords remain, force fallback handling instead.
                dax_only_keywords = (
                    "CALCULATE",
                    "SAMEPERIODLASTYEAR",
                    "DATEADD",
                    "DATESYTD",
                    "TOTALYTD",
                    "IF(",
                    "BLANK(",
                )
                upper_replaced = replaced.upper()
                if any(keyword in upper_replaced for keyword in dax_only_keywords):
                    return None
                if "[" in replaced or "]" in replaced:
                    return None
                return replaced

            return None

        return resolve_expression(dax, "fact", set())
    
    def _quote(self, identifier: str) -> str:
        """Quote SQL identifier, replacing spaces and illegal chars."""
        clean_id = sanitize_column(identifier, force_uppercase=True)
        return f'"{clean_id}"'
    
    def analyze_complexity(self, dax: str) -> dict:
        """
        Analyze DAX expression complexity and return metadata for sync decisions.
        
        Returns:
            dict with keys:
                - tier: int (1-4)
                - requires_time_intel: bool
                - group_by_dimensions: list[str]
                - depends_on_measures: list[str]
                - sync_enabled: bool
                - failure_reason: Optional[str]
        """
        if not dax:
            return {
                "tier": 0,
                "requires_time_intel": False,
                "group_by_dimensions": [],
                "depends_on_measures": [],
                "sync_enabled": False,
                "failure_reason": "Empty expression"
            }
        
        clean_dax = dax.strip()
        upper_dax = clean_dax.upper()
        
        result = {
            "tier": 1,
            "requires_time_intel": False,
            "group_by_dimensions": [],
            "depends_on_measures": [],
            "sync_enabled": True,
            "failure_reason": None
        }
        
        # Extract measure dependencies [MeasureName]
        measure_refs = re.findall(r"\[([^\]]+)\]", clean_dax)
        # Filter out column refs (those from 'Table'[Column] patterns)
        table_col_pattern = re.findall(r"'[\w\s]+'\[([^\]]+)\]", clean_dax)
        pure_measure_refs = [m for m in measure_refs if m not in table_col_pattern]
        result["depends_on_measures"] = list(set(pure_measure_refs))
        
        # Check for unsupported patterns first (Tier 4)
        for pattern in self.UNSUPPORTED_PATTERNS:
            if re.search(pattern, upper_dax, re.IGNORECASE):
                result["tier"] = 4
                result["sync_enabled"] = False
                result["failure_reason"] = f"Unsupported DAX pattern detected"
                return result
        
        # Check for Time Intelligence functions (Tier 3)
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                result["tier"] = 3
                result["requires_time_intel"] = True
                # Time Intelligence requires Date dimension for proper evaluation
                result["group_by_dimensions"] = ["'Calendar'[Date]"]
                
                # Check if we can translate this specific pattern
                can_translate = False
                for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
                    if pattern.search(clean_dax):
                        can_translate = True
                        break
                
                if not can_translate:
                    # Complex Time Intelligence we can't translate
                    result["sync_enabled"] = False
                    result["failure_reason"] = f"Complex Time Intelligence ({func}) not automatically translatable"
                break
        
        # Check for simple CALCULATE without complex filters (still Tier 3)
        if "CALCULATE" in upper_dax and result["tier"] < 3:
            result["tier"] = 3
            # Simple CALCULATE might still be translatable if it's just wrapping an aggregation
            if not re.search(r"CALCULATE\s*\([^)]+,\s*\w+\s*\(", upper_dax):
                # CALCULATE with simple filter - might work
                pass
            else:
                result["sync_enabled"] = False
                result["failure_reason"] = "CALCULATE with complex filter not automatically translatable"
        
        # Check for arithmetic/branching (Tier 2)
        if result["tier"] == 1 and result["depends_on_measures"]:
            result["tier"] = 2
        
        return result
    
    def try_tier3_time_intel(
        self, 
        dax: str, 
        table_alias: str, 
        date_alias: str = "CALENDAR"
    ) -> Optional[str]:
        """
        Attempt Tier 3 Time Intelligence translation using Snowflake window functions.
        
        Converts DAX Time Intelligence to SQL window functions:
        - TOTALYTD -> Cumulative sum partitioned by Year
        - TOTALMTD -> Cumulative sum partitioned by Year, Month
        - TOTALQTD -> Cumulative sum partitioned by Year, Quarter
        
        Args:
            dax: The DAX expression
            table_alias: SQL alias for the measure's source table
            date_alias: SQL alias for the Date dimension table
            
        Returns:
            SQL expression or None if translation not possible
        """
        for period_type, pattern in self.TIME_INTEL_PATTERNS.items():
            match = pattern.search(dax)
            if match:
                agg_func = match.group(1).upper()
                col_name = match.group(2) or match.group(3)
                # Groups 4 and 5 are the date table and column
                
                col_ref = f"{table_alias}.{self._quote(col_name)}"
                sql_agg = {
                    "SUM": "SUM", 
                    "AVERAGE": "AVG", 
                    "COUNT": "COUNT",
                    "MIN": "MIN",
                    "MAX": "MAX"
                }.get(agg_func, "SUM")
                
                # Generate Snowflake window function for period-to-date
                if period_type == "YTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "MTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."MONTH"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
                elif period_type == "QTD":
                    return f"""{sql_agg}({col_ref}) OVER (
    PARTITION BY {date_alias}."YEAR", {date_alias}."QUARTER"
    ORDER BY {date_alias}."DATE"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)"""
        
        return None
    
    def get_required_dimensions(self, dax: str) -> list[str]:
        """
        Extract dimension columns that should be included in GROUP BY for proper measure evaluation.
        
        Analyzes the DAX expression to determine what dimensions are needed for
        context-dependent calculations.
        
        Returns:
            List of dimension column references (e.g., ["'Date'[Year]", "'Region'[Name]"])
        """
        dimensions = []
        upper_dax = dax.upper() if dax else ""
        
        # Time Intelligence always needs date context
        for func in self.TIME_INTEL_FUNCTIONS:
            if func in upper_dax:
                found = False
                for full_call, args_text in self._extract_function_call(dax or "", func):
                    args = self._split_dax_arguments(args_text)
                    if len(args) >= 2:
                        date_ref = args[1].strip()
                        date_match = re.match(r"^(?:'([^']+)'|([a-zA-Z0-9_#@ -]+))\[([^\]]+)\]$", date_ref)
                        if date_match:
                            tbl = date_match.group(1) or date_match.group(2)
                            col = date_match.group(3)
                            dimensions.append(f"'{tbl}'[{col}]")
                            found = True
                            break
                if not found:
                    dimensions.append("'Calendar'[Date]")
                break
        
        # Extract explicit table[column] references that might indicate required dimensions
        # Pattern: 'TableName'[ColumnName] 
        table_col_refs = re.findall(r"'([\w\s]+)'\[(\w+)\]", dax or "")
        for table, col in table_col_refs:
            dim_ref = f"'{table}'[{col}]"
            if dim_ref not in dimensions:
                # Only add if it looks like a dimension (not a measure column)
                col_upper = col.upper()
                if not any(m in col_upper for m in ["AMOUNT", "SALES", "REVENUE", "PRICE", "COST", "QTY"]):
                    dimensions.append(dim_ref)
        
        return dimensions

    def build_dependency_graph(self, metrics: List[Any]) -> Dict[str, List[str]]:
        """
        Build a dependency graph where each measure maps to a list of other measures it depends on.
        Detects [MeasureName] references.
        """
        metric_names = {str(m.unique_name).casefold() for m in metrics}
        graph = {}
        for m in metrics:
            name = m.unique_name
            expr = m.expression or ""
            # Find all [Ref]
            refs = []
            for match in re.finditer(r"\[([^\]]+)\]", expr):
                ref_name = match.group(1).strip()
                if ref_name.casefold() in metric_names:
                    refs.append(ref_name)
            # Also find 'Table'[Ref]
            for match in re.finditer(r"'?([\w\s/]+)'?\[([^\]]+)\]", expr):
                ref_name = match.group(2).strip()
                if ref_name.casefold() in metric_names:
                    refs.append(ref_name)
            
            # De-duplicate refs
            unique_refs = []
            seen = set()
            for r in refs:
                rc = r.casefold()
                if rc not in seen and rc != name.casefold():
                    seen.add(rc)
                    unique_refs.append(r)
            graph[name] = unique_refs
        return graph

    def get_translation_order(self, graph: Dict[str, List[str]]) -> List[str]:
        # Topological sort using DFS
        visited = {}  # 0 = unvisited, 1 = visiting, 2 = visited
        order = []
        cycles = []
        
        def dfs(node: str):
            node_key = node.casefold()
            actual_node = None
            for k in graph:
                if k.casefold() == node_key:
                    actual_node = k
                    break
            
            if not actual_node:
                return
                
            state = visited.get(actual_node.casefold(), 0)
            if state == 1:
                cycles.append(actual_node)
                return
            if state == 2:
                return
                
            visited[actual_node.casefold()] = 1
            for dep in graph.get(actual_node, []):
                dfs(dep)
            visited[actual_node.casefold()] = 2
            order.append(actual_node)

        for node in graph:
            if visited.get(node.casefold(), 0) == 0:
                dfs(node)
                
        if cycles:
            logger.warning(f"Circular dependency detected involving: {', '.join(set(cycles))}")
            
        order_set = {n.casefold() for n in order}
        for node in graph:
            if node.casefold() not in order_set:
                order.append(node)
                
        return order

    def translate_with_dependencies(self, metrics: List[Any], table_alias: str, dataset_name: str) -> List[Any]:
        logger.info(f"Building dependency graph for {len(metrics)} measures")
        graph = self.build_dependency_graph(metrics)
        order = self.get_translation_order(graph)
        logger.info(f"Translation order: {', '.join(order)}")
        
        metric_map = {m.unique_name.casefold(): m for m in metrics}
        
        for name in order:
            metric = metric_map.get(name.casefold())
            if not metric or not metric.expression:
                continue
                
            try:
                translated_sql = self.translate_expr(metric.expression, metrics, table_alias)
                if translated_sql and "[" not in translated_sql and "]" not in translated_sql and not any(f in translated_sql.upper() for f in ["SUMX(", "FILTER(", "CALCULATE(", "DIVIDE("]):
                    metric.sql_expression = translated_sql
                    metric.sync_enabled = True
                    metric.complexity_tier = 2
                    logger.info(f"Translated {metric.unique_name}: {translated_sql}")
                else:
                    logger.warning(f"Could not translate metric '{metric.unique_name}'")
            except Exception as e:
                logger.warning(f"Failed to translate metric '{metric.unique_name}': {e}")
                
        return metrics

    def translate_expr(self, expr: str, metrics: List[Any], table_alias: str) -> str:
        clean = " ".join((expr or "").split()).strip()
        
        # 1. Try direct tier 1 first (e.g. SUM([Value]))
        tier1 = self._try_tier1(clean, table_alias)
        if tier1:
            return tier1
            
        # 2. Resolve TOTALYTD/MTD/QTD calls using parenthesis matching
        for func in ["TOTALYTD", "TOTALMTD", "TOTALQTD"]:
            for full_call, args_text in self._extract_function_call(clean, func):
                args = self._split_dax_arguments(args_text)
                if len(args) >= 2:
                    base_expr = args[0].strip()
                    date_col_ref = args[1].strip()
                    date_match = re.match(r"^(?:'([^']+)'|([a-zA-Z0-9_#@ -]+))\[([^\]]+)\]$", date_col_ref)
                    if date_match:
                        table_name = date_match.group(1) or date_match.group(2)
                        cal_tbl = sanitize_column(table_name, force_uppercase=True)
                    else:
                        cal_tbl = "CALENDAR"

                    base_sql = self.translate_expr(base_expr, metrics, table_alias)
                    agg = self._parse_sql_aggregation(base_sql)
                    if agg:
                        agg_func, value_expr = agg
                        if agg_func == "COUNT_DISTINCT":
                            repl_sql = f"COUNT(DISTINCT {value_expr}) OVER (PARTITION BY {cal_tbl}.YEAR ORDER BY {cal_tbl}.PERIOD)"
                        else:
                            repl_sql = f"{agg_func}({value_expr}) OVER (PARTITION BY {cal_tbl}.YEAR ORDER BY {cal_tbl}.PERIOD)"
                    else:
                        repl_sql = f"SUM({base_sql}) OVER (PARTITION BY {cal_tbl}.YEAR ORDER BY {cal_tbl}.PERIOD)"
                    clean = clean.replace(full_call, f"({repl_sql})")

        # 3. Try CALCULATE
        for full_call, args_text in self._extract_function_call(clean, "CALCULATE"):
            calc_sql = self._translate_calculate(full_call, metrics, table_alias)
            if calc_sql:
                clean = clean.replace(full_call, f"({calc_sql})")

        # 4. Try DIVIDE
        for full_call, args_text in self._extract_function_call(clean, "DIVIDE"):
            div_sql = self._translate_divide(full_call, metrics, table_alias)
            if div_sql:
                clean = clean.replace(full_call, f"({div_sql})")

        # 5. Try measure reference resolution on arithmetic chains / nested expressions
        res = self._translate_measure_reference(clean, metrics, table_alias)
        
        # 6. Resolve remaining columns
        def table_col_repl(match):
            tbl = match.group(1).strip()
            col = match.group(2).strip()
            san_tbl = sanitize_column(tbl, force_uppercase=True)
            return f'{san_tbl}."{col}"'
            
        res = re.sub(r"'?([\w\s/]+)'?\[([^\]]+)\]", table_col_repl, res)
        
        def col_repl(match):
            col = match.group(1).strip()
            return f'{table_alias}."{col}"'
            
        res = re.sub(r"\[([^\]]+)\]", col_repl, res)
        
        res = " ".join(res.split())
        return res

    def _translate_measure_reference(self, ref_name: str, metrics: List[Any], table_alias: str) -> Optional[str]:
        """
        Resolve all bracketed measure references in the given expression.
        If a reference matches a metric name in metrics, it is replaced by its SQL expression.
        """
        if not ref_name:
            return ref_name
            
        # If it's a single measure reference like "[Amount]"
        stripped = ref_name.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip()
            resolved = self._resolve_measure_sql(name, metrics, table_alias)
            if resolved:
                return resolved
                
        # Find all occurrences of [MeasureName]
        replaced = ref_name
        
        # Find all [Measure] pattern matches
        # Skip table-qualified columns like Table[Column] by checking if there's a word/quote character right before '['
        pattern = re.compile(r"(?<!['\w])\[([^\]]+)\]")
        
        # We need to find matches and replace them
        matches = pattern.findall(ref_name)
        for ref in matches:
            resolved_val = self._resolve_measure_sql(ref, metrics, table_alias)
            if resolved_val:
                # Replace [ref] with (resolved_val)
                token_pattern = re.compile(rf"(?<!['\w])\[{re.escape(ref)}\]")
                replaced = token_pattern.sub(f"({resolved_val})", replaced)
                
        return replaced

    def _resolve_measure_sql(self, name: str, metrics: List[Any], table_alias: str) -> Optional[str]:
        """
        Look up a measure by name (case-insensitive) in the metrics list and resolve its SQL.
        """
        if not name:
            return None
            
        metrics = metrics or []
        logger.debug(
            f"Resolving measure '{name}' "
            f"against {len(metrics)} metrics"
        )
            
        metric_map = {m.unique_name.casefold(): m for m in metrics if getattr(m, "unique_name", None)}
        metric = metric_map.get(name.casefold())
        if not metric:
            return None
            
        # If already has SQL expression, return it
        if getattr(metric, "sql_expression", None):
            return metric.sql_expression
        if getattr(metric, "_sql_expression", None):
            return metric._sql_expression
            
        # Otherwise, translate it recursively
        if getattr(metric, "expression", None):
            try:
                resolved = self.translate_expr(metric.expression, metrics, table_alias)
                if resolved and "[" not in resolved and "]" not in resolved:
                    if hasattr(metric, "sql_expression"):
                        try:
                            metric.sql_expression = resolved
                        except Exception:
                            pass
                    try:
                        object.__setattr__(metric, "_sql_expression", resolved)
                    except Exception:
                        pass
                    return resolved
            except Exception as e:
                logger.warning(f"Error resolving nested measure '{name}': {e}")
                
        return None

    def _strip_outer_parens(self, sql: str) -> str:
        """Strip outer parenthesis from a SQL expression if they are balanced."""
        if not sql:
            return sql
        sql_str = sql.strip()
        while sql_str.startswith("(") and sql_str.endswith(")"):
            # Check if these outer parens are actually matching/balanced
            depth = 0
            balanced = True
            for i, ch in enumerate(sql_str[:-1]):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        # Balanced matched paren closed early, so the outer parens are NOT a matching pair
                        balanced = False
                        break
            if balanced:
                sql_str = sql_str[1:-1].strip()
            else:
                break
        return sql_str

    def _extract_calculate_components(self, expr: str) -> Tuple[str, List[str]]:
        """Extract the base expression and filter arguments from a CALCULATE call."""
        clean = expr.strip()
        if not (clean.upper().startswith("CALCULATE(") and clean.endswith(")")):
            return clean, []
        args_text = clean[len("CALCULATE("):-1]
        args = self._split_dax_arguments(args_text)
        if not args:
            return clean, []
        base_expr = args[0].strip()
        filter_exprs = [arg.strip() for arg in args[1:]]
        return base_expr, filter_exprs

    def _filter_to_sql(self, filter_expr: str) -> Optional[str]:
        """Convert a DAX filter expression to Snowflake SQL."""
        clean = filter_expr.strip()
        
        # Check for FILTER(...)
        if clean.upper().startswith("FILTER(") and clean.endswith(")"):
            args_text = clean[len("FILTER("):-1]
            args = self._split_dax_arguments(args_text)
            if len(args) == 2:
                return self._filter_to_sql(args[1])
                
        # Check for ALL(...)
        if clean.upper().startswith("ALL(") and clean.endswith(")"):
            return "1=1"
            
        # Support string/numeric equality: Table[Column] = Value
        eq_match = re.match(
            r"^(?:'(?P<table_quoted>[^']+)'|(?P<table>[A-Za-z0-9_#@ -]+))\[(?P<column>[^\]]+)\]\s*=\s*(?P<value>.+)$",
            clean,
            re.IGNORECASE
        )
        if eq_match:
            table_name = eq_match.group("table_quoted") or eq_match.group("table")
            column_name = eq_match.group("column")
            value_text = eq_match.group("value").strip()
            
            san_tbl = sanitize_column(table_name, force_uppercase=True)
            san_col = sanitize_column(column_name, force_uppercase=True)
            
            if re.fullmatch(r'"(?:[^"\\]|\\.)*"', value_text):
                literal = "'" + value_text[1:-1].replace("'", "''") + "'"
            elif re.fullmatch(r"'(?:[^'\\]|\\.)*'", value_text):
                literal = value_text
            elif re.fullmatch(r"-?\d+(?:\.\d+)?", value_text):
                literal = value_text
            else:
                literal = f"'{value_text.strip()}'"
                
            return f"{san_tbl}.{san_col} = {literal}"
            
        return None

    def _translate_calculate(self, expr: str, metrics: List[Any], table_alias: str) -> Optional[str]:
        clean = expr.strip()
        if not (clean.upper().startswith("CALCULATE(") and clean.endswith(")")):
            return None
            
        base_expr, filter_exprs = self._extract_calculate_components(clean)
        
        # Translate the base measure
        translated_base = None
        if base_expr.startswith("[") and base_expr.endswith("]"):
            translated_base = self._translate_measure_reference(base_expr, metrics, table_alias)
        if not translated_base:
            translated_base = self.translate_expr(base_expr, metrics, table_alias)
            
        if not translated_base:
            return None
            
        # Parse filter conditions
        conditions = []
        for filter_expr in filter_exprs:
            if any(keyword in filter_expr.upper() for keyword in ["USERELATIONSHIP", "CROSSFILTER", "ALLSELECTED", "REMOVEFILTERS"]):
                logger.warning(f"CALCULATE filter contains unsupported construct: {filter_expr}")
                continue
                
            cond_sql = self._filter_to_sql(filter_expr)
            if cond_sql:
                conditions.append(cond_sql)
                
        if not conditions:
            return translated_base
            
        condition_sql = " AND ".join(conditions)
        
        # Wrap the translated base with the CASE WHEN clause
        stripped_base = self._strip_outer_parens(translated_base)
        return f"CASE WHEN {condition_sql} THEN {stripped_base} ELSE NULL END"

    def _translate_divide(self, expr: str, metrics: List[Any], table_alias: str) -> Optional[str]:
        clean = expr.strip()
        if not (clean.upper().startswith("DIVIDE(") and clean.endswith(")")):
            return None
            
        args_text = clean[len("DIVIDE("):-1]
        args = self._split_dax_arguments(args_text)
        if len(args) < 2:
            return None
            
        num = self.translate_expr(args[0].strip(), metrics, table_alias)
        den = self.translate_expr(args[1].strip(), metrics, table_alias)
        
        alt = "NULL"
        if len(args) >= 3:
            alt_raw = args[2].strip()
            if alt_raw.upper() == "BLANK()":
                alt = "NULL"
            else:
                alt = alt_raw
                
        return f"CASE WHEN {den} = 0 OR {den} IS NULL THEN {alt} ELSE {num} / {den} END"

    def _extract_function_call(self, text: str, func_name: str) -> List[Tuple[str, str]]:
        results = []
        pattern = re.compile(rf"\b{re.escape(func_name)}\s*\(", re.IGNORECASE)
        for match in pattern.finditer(text):
            start_idx = match.start()
            depth = 1
            idx = match.end()
            while idx < len(text) and depth > 0:
                if text[idx] == "(":
                    depth += 1
                elif text[idx] == ")":
                    depth -= 1
                idx += 1
            if depth == 0:
                full_call = text[start_idx:idx]
                args_text = text[match.end():idx-1]
                results.append((full_call, args_text))
        return results

    def translate_measure(self, dax: str, measure_name: str, context: dict) -> dict:
        """
        Structured translation API for a single measure.
        
        Args:
            dax: Original DAX expression string.
            measure_name: Name of the measure.
            context: Translation context dict containing keys:
                     - 'table_alias': str
                     - 'dataset_name': str
                     - 'metrics_context': List[Any] (optional)
                     
        Returns:
            dict with structured success/error information.
        """
        if not dax or not dax.strip():
            return {
                "success": False,
                "sql": None,
                "error": "Empty DAX expression",
                "tier": "4"
            }
            
        table_alias = context.get("table_alias") or "FACT"
        dataset_name = context.get("dataset_name") or "Fact"
        metrics_context = context.get("metrics_context") or []
        
        try:
            res = self.translate(
                dax=dax,
                table_alias=table_alias,
                dataset_name=dataset_name,
                metric_name=measure_name,
                metrics_context=metrics_context
            )
            
            if res.is_success and res.sql and res.sql.strip():
                # Perform basic validation: must not contain un-substituted bracket references or DAX keywords
                dax_funcs = ["SUMX(", "FILTER(", "CALCULATE(", "DIVIDE(", "AVERAGEX(", "MAXX(", "MINX("]
                if any(func in res.sql.upper() for func in dax_funcs):
                    return {
                        "success": False,
                        "sql": None,
                        "error": f"Translation output contains unsupported DAX functions: {res.sql}",
                        "tier": str(res.tier)
                    }
                if "[" in res.sql or "]" in res.sql:
                    return {
                        "success": False,
                        "sql": None,
                        "error": f"Translation output contains unresolved references: {res.sql}",
                        "tier": str(res.tier)
                    }
                # Must not just return the original DAX
                if res.sql.strip() == dax.strip() and len(dax.strip()) > 10:
                    return {
                        "success": False,
                        "sql": None,
                        "error": "Translation fell back to original DAX expression",
                        "tier": str(res.tier)
                    }
                return {
                    "success": True,
                    "sql": res.sql,
                    "error": None,
                    "tier": str(res.tier)
                }
            else:
                return {
                    "success": False,
                    "sql": None,
                    "error": f"Failed to translate DAX expression: tier logic exhausted (tier {res.tier})",
                    "tier": str(res.tier)
                }
        except Exception as e:
            logger.warning(f"Exception during translate_measure for '{measure_name}': {e}")
            return {
                "success": False,
                "sql": None,
                "error": f"Exception during translation: {str(e)}",
                "tier": "4"
            }