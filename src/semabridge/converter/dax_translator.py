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
from semabridge.connectors.fact_table_naming import tokenize_dataset_name as _tokenize_dataset_name

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
        # "COL_DATE" is the established Snowflake naming convention for the
        # Date/Calendar dimension's table alias (see snowflake_emitter.py's
        # "Map common DAX name -> physical name (e.g. Date -> COL_DATE)" and
        # dax_rule_translator.py's own _date_alias(), which already defaults
        # to this) — kept consistent with those rather than a stale default
        # that doesn't match any table the deployed semantic view declares.
        "table": "COL_DATE",
        "date_col": "DATE",
        "year_col": "YEAR",
        "period_col": "PERIOD",
    }

    def __init__(self) -> None:
        # Lazily created and reused across every _try_llm_fallback /
        # batch_translate_tier5 call made through this instance, so a
        # provider's per-run "unavailable after auth failure" cache (see
        # Tier5Service._unavailable_providers) actually spans the whole
        # run instead of resetting on every metric (each fresh
        # Tier5Service() used to start that cache empty again).
        self._tier5_service = None

    def _get_tier5_service(self):
        if self._tier5_service is None:
            from semabridge.dax_translation.tier5.service import Tier5Service
            self._tier5_service = Tier5Service()
        return self._tier5_service

    def _get_date_alias(self) -> str:
        return os.getenv("SEMABRIDGE_DATE_ALIAS", self.CALENDAR_MAP.get("table", "COL_DATE"))
    
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

    # TOTALYTD/MTD/QTD are included here (not just the SAMEPERIODLASTYEAR-style
    # offset functions) so that _try_strict_translation's own TOTALYTD branch —
    # which emits a bare OVER(...) window function with no date-range bound,
    # invalid for a Snowflake semantic-view METRICS clause — never runs. That
    # branch has been removed; blocking these here routes every time-intelligence
    # function through the AST renderer's correct CASE-WHEN-bounded translation
    # instead (see dax_ast_parser.DaxSqlRenderer._render_period_to_date /
    # _render_lag_period).
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
        "TOTALYTD",
        "TOTALMTD",
        "TOTALQTD",
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
    
    @staticmethod
    def build_schema_lookup(datasets: Optional[List[Any]]) -> Tuple[Dict[str, set], Dict[str, str]]:
        """Build a (dataset_col_lookup, dataset_aliases) pair from SML/OSI
        dataset objects (anything with .unique_name and .columns, each
        column having .unique_name).

        Uses the same sanitize_column/to_alias utilities already imported
        by this module — general and schema-shape-derived, not hardcoded to
        any specific table/column/model name. This is what lets Tier 5's
        semantic validator run against Pipeline A's *source* (Fabric/PBIX)
        schema, since no Snowflake-side physical schema exists yet at
        extraction time.
        """
        dataset_col_lookup: Dict[str, set] = {}
        dataset_aliases: Dict[str, str] = {}
        for ds in datasets or []:
            dataset_col_lookup[ds.unique_name] = {
                sanitize_column(c.unique_name, force_uppercase=True)
                for c in getattr(ds, "columns", []) or []
            }
            dataset_aliases[ds.unique_name] = to_alias(ds.unique_name)
        return dataset_col_lookup, dataset_aliases

    def translate(self,
                  dax: str,
                  table_alias: str,
                  dataset_name: str,
                  metric_name: str = None,
                  metrics_context: List[Any] = None,
                  dataset_col_lookup: Optional[Dict[str, set]] = None,
                  dataset_aliases: Optional[Dict[str, str]] = None,
                  anchor_flag_map: Optional[Dict[Any, str]] = None,
                  skip_tier5: bool = False) -> DAXTranslationResult:
        """
        Translate a DAX expression to SQL.

        dataset_col_lookup/dataset_aliases are new, optional (Step 3 of the
        DAX translation consolidation) — see build_schema_lookup() above.
        Only consumed by the Tier 5 LLM fallback; Tiers 1-4 are pure
        DAX-grammar logic and never needed physical schema.

        Args:
            dax: The DAX formula string
            table_alias: SQL alias for the main table (e.g. 'sales')
            dataset_name: Name of the dataset for context
            metric_name: Name of the current metric being translated
            metrics_context: List of SMLMetric objects to resolve dependencies
            skip_tier5: Run Tiers 1-4 only and decline (same shape as "all
                tiers exhausted") instead of calling _try_llm_fallback -- i.e.
                instead of issuing one Tier5Service.translate() call for THIS
                metric right now. Callers that process many metrics in a loop
                (osi_to_sml.py's _convert_metric/_resolve_metric_dependencies)
                pass this so nothing here is resolved via an individual,
                sequential Tier-5 API call; every metric that needs Tier 5
                collects into one batch_translate_tier5()/translate_batch()
                call instead (see the dry-run timeout fix). Default False:
                every other existing caller's behavior is unchanged.
        """
        if not dax:
            return DAXTranslationResult(None, 3, "")
        
        clean_dax = dax.strip()

        # Tier 1-4 (regex/AST-based general DAX grammar) run first; anything
        # they decline falls through to the retry/AST/LLM tiers below.
        tiered_sql = self._try_tiered_translation(
            clean_dax,
            table_alias,
            dataset_name,
            metrics_context,
            anchor_flag_map=anchor_flag_map,
        )
        if tiered_sql:
            return tiered_sql

        # Tier 1: Direct Aggregations
        tier1_sql = self._try_tier1(clean_dax, table_alias)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)

        strict_blocked = any(
            re.search(rf"\b{pattern}\b", clean_dax, re.IGNORECASE)
            for pattern in self.STRICT_BLOCKED_FUNCTIONS
        )
        if strict_blocked:
            logger.debug(
                "Strict translator rejected unsupported DAX pattern; continuing with AST/LLM: %s",
                clean_dax[:80],
            )

        if not strict_blocked:
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
            resolved_measures = self._build_resolved_measures_map(table_alias, metrics_context)
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self._get_date_alias(),
                measure_sql_map=resolved_measures,
                known_measure_names=self._all_measure_names(metrics_context),
                anchor_flag_map=anchor_flag_map,
                primary_table_name=dataset_name,
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
            resolved_measures = self._build_resolved_measures_map(table_alias, metrics_context)
            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self._get_date_alias(),
                measure_sql_map=resolved_measures,
                known_measure_names=self._all_measure_names(metrics_context),
                anchor_flag_map=anchor_flag_map,
                primary_table_name=dataset_name,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 4, clean_dax)

        # General deterministic fallback: attempt AST translation for any
        # remaining shape the AST renderer can already express structurally —
        # e.g. CALCULATE(measure) nested inside IF/arithmetic, binary
        # arithmetic between CALCULATE-wrapped measures, or nested IF/SWITCH
        # with comparison conditions — before giving up to the LLM tier.
        # try_ast_translate is side-effect-free and returns None on failure,
        # so this can only recover cases that would otherwise reach Tier 5.
        #
        # Unlike the two blocks above (pre-existing, and possibly relying on
        # FILTER/iterator row-context bracket-as-column semantics this simple
        # grammar doesn't model), this new path requires every bracket
        # reference in the expression to be an already-resolved measure. A
        # measure reference the renderer can't distinguish from a column
        # (silently falling back to treating it as one) must never be
        # accepted here — better to defer to a later pass that has fuller
        # context (see _resolve_metric_dependencies-style passes) than to
        # risk a plausible-looking but wrong translation.
        from semabridge.converter.dax_ast_parser import try_ast_translate
        resolved_measures = self._build_resolved_measures_map(table_alias, metrics_context)
        if not self._has_unresolved_bracket_reference(clean_dax, resolved_measures):
            general_ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self._get_date_alias(),
                measure_sql_map=resolved_measures,
                known_measure_names=self._all_measure_names(metrics_context),
                anchor_flag_map=anchor_flag_map,
                primary_table_name=dataset_name,
            )
            if general_ast_sql:
                return DAXTranslationResult(general_ast_sql, 4, clean_dax)

        if skip_tier5:
            # Tiers 1-4 above already declined. Return the identical shape
            # as "all tiers exhausted" below rather than spending this
            # metric's one Tier-5 attempt right now -- the caller collects
            # it for a single batched call instead (see docstring above).
            return DAXTranslationResult(None, 4, clean_dax)

        # Tier 5: LLM Fallback - Use Claude for complex expressions deterministic parsing couldn't handle
        # Only attempt if LLM is available and enabled
        llm_result = self._try_llm_fallback(
            clean_dax,
            table_alias,
            dataset_name,
            metric_name,
            dataset_col_lookup=dataset_col_lookup,
            dataset_aliases=dataset_aliases,
        )
        if llm_result:
            return llm_result

        # No translation possible — return None (all tiers exhausted)
        return DAXTranslationResult(None, 4, clean_dax)

    def _try_tiered_translation(
        self,
        clean_dax: str,
        table_alias: str,
        dataset_name: str,
        metrics_context: List[Any] = None,
        anchor_flag_map: Optional[Dict[Any, str]] = None,
    ) -> Optional[DAXTranslationResult]:
        tier1_sql = self._try_tier1(clean_dax, table_alias)
        if tier1_sql:
            return DAXTranslationResult(tier1_sql, 1, clean_dax)

        strict_blocked = any(
            re.search(rf"\b{pattern}\b", clean_dax, re.IGNORECASE)
            for pattern in self.STRICT_BLOCKED_FUNCTIONS
        )
        if not strict_blocked:
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

            tier2_sql = self._try_branching(clean_dax, metrics_context)
            if tier2_sql:
                return DAXTranslationResult(tier2_sql, 2, clean_dax)

        resolved_measures = self._build_resolved_measures_map(table_alias, metrics_context)
        all_measure_names = self._all_measure_names(metrics_context)

        is_time_intel = any(func.upper() in clean_dax.upper() for func in self.TIME_INTEL_FUNCTIONS)
        if is_time_intel:
            from semabridge.converter.dax_ast_parser import try_ast_translate

            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self._get_date_alias(),
                measure_sql_map=resolved_measures,
                known_measure_names=all_measure_names,
                anchor_flag_map=anchor_flag_map,
                primary_table_name=dataset_name,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 3, clean_dax)

        is_complex = any(
            re.search(pattern, clean_dax, re.IGNORECASE)
            for pattern in self.UNSUPPORTED_PATTERNS
        )
        if is_complex:
            from semabridge.converter.dax_ast_parser import try_ast_translate

            ast_sql = try_ast_translate(
                clean_dax,
                table_alias=table_alias,
                date_alias=self._get_date_alias(),
                measure_sql_map=resolved_measures,
                known_measure_names=all_measure_names,
                anchor_flag_map=anchor_flag_map,
                primary_table_name=dataset_name,
            )
            if ast_sql:
                return DAXTranslationResult(ast_sql, 4, clean_dax)

        return None

    def _has_unresolved_bracket_reference(
        self, dax: str, resolved_measures: Dict[str, str]
    ) -> bool:
        """
        True if `dax` contains any bracket-only reference (e.g. [Name]) that
        is not a key of `resolved_measures`.

        The AST renderer's grammar cannot distinguish "this bracket reference
        names a measure with no SQL yet" from "this bracket reference names a
        column in an iterator/FILTER row-context" (both use identical [Name]
        syntax) — unresolved, it silently falls back to treating the name as
        a raw physical column, which is only sometimes correct. Rather than
        try to reconstruct row-context here, this check is deliberately
        strict: every bracket reference must already be a known, resolved
        measure, or the result is not trusted. This only guards the new,
        general fallback path below (CALCULATE-wrapping-a-measure,
        measure-to-measure arithmetic, nested IF/SWITCH) — none of those
        shapes legitimately need iterator/FILTER row-context, so being this
        strict costs nothing there, while a declined case simply falls
        through to whatever later pass has fuller measure context (e.g.
        _resolve_metric_dependencies-style passes), never a hard failure.
        """
        if not dax:
            return False
        resolved_lower = {str(k).casefold() for k in (resolved_measures or {}).keys()}
        for match in self._MEASURE_REF_PATTERN.finditer(dax):
            start = match.start()
            if start > 0 and dax[start - 1] == "'":
                continue  # table-qualified 'Table'[Column] ref, not a bracket-only ref
            name = match.group(1).strip().casefold()
            if name and name not in resolved_lower:
                return True
        return False

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

        # This substitution only replaces [Measure] tokens — it cannot
        # translate surrounding DAX function-call syntax (e.g. INT(...),
        # DIVIDE(...), ROUND(...)) into valid SQL. Trust it only for pure
        # bracket-arithmetic (operators/parens/literals/measure refs, no
        # function calls anywhere outside the brackets) — anything else must
        # fall through to a function-aware tier (the AST renderer) rather
        # than silently emit un-translated DAX syntax as if it were SQL.
        # (DIVIDE(...) as the *entire* expression is already handled above;
        # this only guards the remaining, more general case.)
        if self._has_function_call_outside_brackets(clean_dax):
            return None

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

    @staticmethod
    def _has_function_call_outside_brackets(dax: str) -> bool:
        """True if `dax` contains an identifier-immediately-followed-by-'('
        (a function call) anywhere outside of [bracket references]. Bracket
        contents are stripped first so a measure name that happens to
        contain literal parentheses (e.g. "[Revenue (Net)]") never triggers
        a false positive."""
        without_brackets = re.sub(r"\[[^\]]*\]", "", dax or "")
        return bool(re.search(r"[A-Za-z_][A-Za-z0-9_]*\s*\(", without_brackets))

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

    def _build_resolved_measures_map(
        self, table_alias: str, metrics_context: Optional[List[Any]]
    ) -> Dict[str, str]:
        """Build a name -> SQL map for every measure in `metrics_context`
        that can be resolved right now, for inlining [MeasureName]
        references the AST renderer (Tier 3/4) encounters.

        Reuses `_resolve_measure_sql`'s existing recursive fallback (already
        translated `.sql_expression`, or freshly translating the measure's
        own `.expression`) instead of only checking whether `.sql_expression`
        already happens to be populated — a referenced measure gets a fair
        shot at resolution even on the pass where it hasn't been translated
        yet, so the AST renderer doesn't have to fall back to treating an
        unresolved [MeasureName] as a raw column reference.
        """
        resolved: Dict[str, str] = {}
        if not metrics_context:
            return resolved
        for metric in metrics_context:
            name = getattr(metric, "unique_name", None)
            if not name or name in resolved:
                continue
            sql = self._resolve_measure_sql(name, metrics_context, table_alias)
            if sql:
                resolved[name] = sql
        return resolved

    @staticmethod
    def _all_measure_names(metrics_context: Optional[List[Any]]) -> set:
        """Every measure name tracked in `metrics_context`, resolved or not.

        Passed to the AST renderer alongside its (possibly smaller)
        resolved-SQL map so it can distinguish "this bracket reference names
        a real measure that just isn't translated yet" (fail closed) from
        "this bracket reference names something outside the measure
        registry entirely" (fall back to a column reference — the common
        SUM([Column]) shape).
        """
        if not metrics_context:
            return set()
        return {
            getattr(m, "unique_name", None)
            for m in metrics_context
            if getattr(m, "unique_name", None)
        }

    def _try_strict_translation(
        self,
        dax: str,
        table_alias: str,
        metrics_context: Optional[List[Any]] = None,
    ) -> Optional[str]:
        """Translate only the strict CALCULATE-with-simple-filter form.

        A TOTALYTD branch previously lived here, emitting a bare
        OVER(PARTITION BY ... ORDER BY ...) window function with no
        date-range bound — invalid for a Snowflake semantic-view METRICS
        clause and missing the actual "up to today" filter entirely. TOTALYTD
        (and MTD/QTD) are now in STRICT_BLOCKED_FUNCTIONS so this method is
        never reached for them; translation instead goes through the AST
        renderer's correct CASE-WHEN-bounded logic (see
        dax_ast_parser.DaxSqlRenderer._render_period_to_date).
        """
        clean_dax = " ".join((dax or "").split())
        upper_dax = clean_dax.upper()

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
                          metric_name: Optional[str] = None,
                          dataset_col_lookup: Optional[Dict[str, set]] = None,
                          dataset_aliases: Optional[Dict[str, str]] = None) -> Optional[DAXTranslationResult]:
        """Thin shim onto DaxTranslationService's Tier 5 (Step 3 of the
        approved consolidation migration). Same signature (two new
        optional trailing kwargs), same Optional[DAXTranslationResult]
        contract — every existing caller (tmsl_to_sml.py, osi_to_sml.py)
        is unaffected unless it opts in by passing the new kwargs.

        Preserved, not delegated — no equivalent hook in
        DaxTranslationService: the Tier 4.5 rule-based-translation-first
        classification (is_simple_metric / rule_based_translation).
        Unchanged below, for the same reason as Pipeline B's cutover
        (connectors/translator.py) — dropping it would be a real
        regression, not a shape change.

        Calls tier5.service.Tier5Service directly rather than
        DaxTranslationService.translate_metric() — this method is only
        ever reached from translate() *after* Tiers 1-4 have already run
        and declined; going through DaxTranslationService here would
        silently re-run those same deterministic tiers a second time for
        no benefit (the exact "verbatim double-pass" waste eliminated in
        Step 1's tiers_1_4.py).

        dataset_col_lookup/dataset_aliases are new. This call site never
        had real schema information before (extraction-time, no
        Snowflake-side physical schema exists yet), so Tier 5 here
        previously ran with zero semantic (column-existence) validation.
        Feeding empty dicts through the new mandatory validator would
        reject nearly all realistic LLM output (both the old and new
        prompts ask for qualified alias.column references) — a real
        regression, not a no-op. Callers now build this from the
        *source* (Fabric/PBIX) schema already in scope, via
        DAXTranslator.build_schema_lookup() (sanitize_column/to_alias —
        the same general utilities already used throughout this module,
        not hardcoded to any table/column/model name). This gives
        Pipeline A real semantic validation for the first time.

        Batching note: the old OpenAI-batch path here (and in
        batch_translate_tier5) issued one JSON-batched API call per 20
        metrics. Tier5Service has no equivalent — it translates one
        metric per call. With no real LLM keys configured today this has
        no observable effect, but once real keys are available this is a
        real (if currently dormant) increase in API call count worth
        tracking, not something silently preserved.
        """
        if is_simple_metric(dax):
            logger.info(f"🟢 Metric classified as SIMPLE - using rule-based translation: {metric_name or dax[:50]}")
            sql = rule_based_translation(dax, table_alias)
            if sql:
                logger.debug(f"   ✓ Rule-based translation succeeded: {sql[:80]}")
                return DAXTranslationResult(sql, 4, dax)  # Tier 4 for rule-based (deterministic)
            else:
                logger.debug(f"   ✗ Rule-based translation failed, will fall through to LLM")
        else:
            logger.info(f"🟠 Metric classified as COMPLEX - requesting LLM translation: {metric_name or dax[:50]}")

        try:
            from semabridge.dax_translation.types import TranslationRequest

            request = TranslationRequest(
                dax=dax,
                dataset_name=dataset_name,
                table_alias=table_alias,
                dataset_col_lookup=dataset_col_lookup or {},
                dataset_aliases=dataset_aliases or {},
                metric_name=metric_name,
                dialect="snowflake",
            )
            result = self._get_tier5_service().translate(request)
            if result is not None and result.is_success and result.sql:
                logger.info(
                    f"LLM translation accepted for '{metric_name}' "
                    f"(provider={result.provider}, confidence={result.translation_provider_confidence:.2f})"
                )
                return DAXTranslationResult(result.sql, 5, dax)
            logger.debug(f"Tier 5 declined for '{metric_name}'")
            return None
        except Exception as exc:
            logger.warning(f"Unexpected error in Tier 5 fallback for metric '{metric_name}': {exc}")
            return None
            return None
    
    def batch_translate_tier5(self,
                             metrics_list: List[Tuple[str, str, str, str]],
                             dataset_col_lookup: Optional[Dict[str, set]] = None,
                             dataset_aliases: Optional[Dict[str, str]] = None) -> Dict[str, Optional[DAXTranslationResult]]:
        """Thin shim onto DaxTranslationService's Tier 5, batch form (Step 3
        of the approved consolidation migration). Same signature (two new
        optional trailing kwargs), same Dict[str, Optional[DAXTranslationResult]]
        contract, every key from metrics_list guaranteed present.

        Preserved unchanged, same rationale as _try_llm_fallback: the
        simple/complex classification and rule-based-translation-first
        step (is_simple_metric / rule_based_translation), including
        escalating simple-but-rule-engine-failed metrics to Tier 5 rather
        than dropping them.

        dataset_col_lookup/dataset_aliases: see _try_llm_fallback's
        docstring — same rationale, built once per model by the caller via
        DAXTranslator.build_schema_lookup() and passed through here.

        Batching restored (previously noted as a real cost/latency
        regression from the Step 3 cutover — Tier5Service now had a
        translate_batch(), this method didn't yet use it). Every remaining
        candidate after rule-based classification is now sent to
        Tier5Service.translate_batch() as one call (chunked internally at
        Tier5Config.max_batch_size, default 20 — the same chunk size
        Pipeline B's original OpenAI batch-prefetch used), not one
        Tier5Service.translate() call per metric. Every individual result
        still goes through the exact same validation Tier5Service.translate()
        applies — batching only reduces API call count, never weakens
        per-metric validation.
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

        if llm_candidates:
            # Restored batching: one (or a few, chunked at
            # Tier5Config.max_batch_size) JSON-map API call for every
            # remaining candidate instead of one call per metric — the
            # cost/latency regression noted in this method's docstring
            # above is fixed by Tier5Service.translate_batch(), not by
            # reverting to a Pipeline-A-specific batch prompt.
            try:
                from semabridge.dax_translation.types import TranslationRequest

                requests = [
                    TranslationRequest(
                        dax=dax,
                        dataset_name=dataset_name,
                        table_alias=table_alias,
                        dataset_col_lookup=dataset_col_lookup or {},
                        dataset_aliases=dataset_aliases or {},
                        metric_name=metric_name,
                        dialect="snowflake",
                    )
                    for metric_name, dax, table_alias, dataset_name in llm_candidates
                ]
                tier5_results = self._get_tier5_service().translate_batch(requests)
            except Exception as exc:
                logger.error(f"Unexpected error in batch Tier 5 translation: {exc}")
                tier5_results = [None] * len(llm_candidates)

            for (metric_name, dax, table_alias, dataset_name), tier5_result in zip(
                llm_candidates, tier5_results
            ):
                if tier5_result is not None and tier5_result.is_success and tier5_result.sql:
                    results[metric_name] = DAXTranslationResult(tier5_result.sql, 5, dax)
                    logger.debug(
                        f"   ✓ [{metric_name}] LLM translated (provider={tier5_result.provider}, "
                        f"conf={tier5_result.translation_provider_confidence:.2f})"
                    )
                else:
                    results[metric_name] = None
                    logger.debug(f"   ❌ [{metric_name}] Could not translate")

        successful = sum(1 for r in results.values() if r is not None)
        logger.info(
            f"✅ Batch translation complete:\n"
            f"   ├─ Total metrics: {len(metrics_list)}\n"
            f"   ├─ Simple (no API calls): {len(simple_metrics) - len(simple_failed_for_llm)}\n"
            f"   ├─ LLM candidates: {len(llm_candidates)}\n"
            f"   └─ Successful: {successful}/{len(metrics_list)}"
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

            # TOTALYTD(Expression, Date) used to be handled here with a bare
            # OVER(PARTITION BY ... ORDER BY ...) window function — invalid
            # for a Snowflake semantic-view METRICS clause, and with no
            # date-range bound at all. Time-intelligence functions are
            # handled correctly and generally by the AST renderer (see
            # dax_ast_parser.DaxSqlRenderer._render_period_to_date), reached
            # via the is_time_intel check in translate()/_try_tiered_translation;
            # declining here (falling through to that check) instead of
            # returning early with the broken shape.

            # Generic arithmetic replacement for [A] +/-/*// [B]. This only
            # replaces [Measure] tokens — it cannot translate a surrounding
            # DAX function call (e.g. INT(...), DIVIDE(...), ROUND(...)) into
            # valid SQL, so decline outright whenever one is present anywhere
            # outside the bracket references, rather than relying on a
            # hardcoded denylist of "known bad" keywords that will always be
            # one function behind whatever DAX actually uses.
            if self._has_function_call_outside_brackets(clean_expr):
                return None

            replaced = replace_measure_refs(clean_expr, visiting)
            if replaced and replaced != clean_expr:
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
                from semabridge.converter.dax_ast_parser import dax_context_transition_failure_reason
                context_transition_reason = dax_context_transition_failure_reason(clean_dax)
                result["failure_reason"] = context_transition_reason or "Unsupported DAX pattern detected"
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
    
    # Whole-word terms suggesting a bracketed reference is a value/measure
    # column rather than a dimension to group by — see get_required_dimensions.
    _VALUE_COLUMN_TERMS = frozenset({"AMOUNT", "SALES", "REVENUE", "PRICE", "COST", "QTY"})

    def get_required_dimensions(self, dax: str) -> list[str]:
        """
        Extract dimension columns that should be included in GROUP BY for proper measure evaluation.

        Analyzes the DAX expression to determine what dimensions are needed for
        context-dependent calculations.

        Returns:
            List of dimension column references (e.g., ["'Date'[Year]", "'Region'[Name]"])
        """
        dimensions = []

        # Extract explicit table[column] references that might indicate required dimensions
        # Pattern: 'TableName'[ColumnName]
        table_col_refs = re.findall(r"'([\w\s]+)'\[(\w+)\]", dax or "")
        for table, col in table_col_refs:
            dim_ref = f"'{table}'[{col}]"
            if dim_ref not in dimensions:
                # Only add if it looks like a dimension (not a measure column) —
                # whole-word match, not a raw substring (which would wrongly
                # exclude e.g. "Costco_Region" for containing "COST").
                col_tokens = set(_tokenize_dataset_name(col))
                if not (col_tokens & self._VALUE_COLUMN_TERMS):
                    dimensions.append(dim_ref)

        return dimensions
