"""
Translation adapter for metric expressions.

This thin adapter delegates existing translation helpers to the
current emitter-like delegate. It acts as a stable injection point for
future refactors that move translation logic out of the emitter.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set, Tuple, List
import re
import os
import json

from semabridge.utils.logger import get_logger
from semabridge.utils.null_sentinel import is_null_cast_sql

logger = get_logger(__name__)

# Matches a DAX qualified column reference, e.g. 'Date'[Running Year] or
# Date[Running Year] -- used to build a column-name -> DAX-source-table hint
# map (see MetricExpressionTranslator._extract_dax_table_hints) so that when
# a metric's SQL (Tier-5/LLM output, most often) contains an ALREADY-
# qualified reference like kpi."RUNNING_YEAR" for a column the DAX
# unambiguously wrote as 'Date'[Running Year], normalization can catch the
# misattribution instead of accepting it silently just because "kpi" is a
# real alias and "RUNNING_YEAR" happens to also be a real column there.
_DAX_QUALIFIED_COLUMN_PATTERN = re.compile(r"'([^']+)'\[([^\]]+)\]|\b([A-Za-z_][\w ]*)\[([^\]]+)\]")


class MetricExpressionTranslator:
    def __init__(self, identifier_sanitizer: Any = None, dialect: str = "snowflake", behavior: Any = None, *args, **kwargs) -> None:
        self._id = identifier_sanitizer
        self.dialect = dialect
        self.behavior = behavior
        self._openai_prefetch_sql_by_metric: Dict[str, str] = {}
        self._openai_prefetch_done = False
        # Fact-table-name (casefolded) -> {shape tuple (see
        # converter/time_intelligence_shapes.py) -> flag column name}
        # precomputed on that fact table's own enriched view by
        # snowflake_emitter.py::_create_enriched_view. Nested by fact table
        # rather than a flat shape->name map merged across every fact table
        # in the model: a fact table whose own MAX_DATE anchor couldn't be
        # established gets no entry here at all, and every consumer below
        # narrows to `self.anchor_flag_map.get(metric.dataset, {})` before
        # use -- so a metric can never resolve a flag column that only
        # exists on a *different* fact table's enriched view. Empty (the
        # default) preserves the inline MAX_DATE rendering exactly, e.g.
        # for callers with no live enrichment step (the schema-blind
        # dry-run/preview path).
        self.anchor_flag_map: Dict[str, Dict[Any, str]] = {}
        # Lazily created and reused across every translate() call made
        # through this instance (one per conversion run), so a provider's
        # per-run "unavailable after auth failure" cache
        # (Tier5Service._unavailable_providers) spans the whole run
        # instead of resetting on every metric.
        self._dax_translation_service = None
        # Same rationale, same fix shape, for the sibling deterministic
        # fallback path (_try_basic_dax_metric_fallback_expression below):
        # that method used to construct a fresh DAXTranslator() per metric,
        # which meant a fresh, empty-cache Tier5Service too (see
        # DAXTranslator.__init__'s own lazy _tier5_service), silently
        # discarding _unavailable_providers on every call and re-attempting
        # (and re-failing) a dead provider once per metric instead of once
        # per run.
        self._dax_translator = None

    def _get_dax_translation_service(self):
        if self._dax_translation_service is None:
            from semabridge.dax_translation.service import DaxTranslationService
            self._dax_translation_service = DaxTranslationService()
        return self._dax_translation_service

    def _get_dax_translator(self):
        if self._dax_translator is None:
            from semabridge.converter.dax_translator import DAXTranslator
            self._dax_translator = DAXTranslator()
        return self._dax_translator

    @staticmethod
    def fix_common_llm_issues(sql: str, dax: str = "") -> str:
        if not sql: return sql
        # Keep CURRENT_DATE() — Snowflake semantic views accept it natively.
        # Do NOT replace with MAX_DATE (a synthetic enriched-view column that
        # is not in scope inside semantic view metric expressions).
        # sql = sql.replace("CURRENT_DATE()", "MAX_DATE")   # removed
        # sql = sql.replace("CURRENT_DATE", "MAX_DATE")     # removed
        # Remove bare ALIAS placeholders that LLMs occasionally emit
        sql = re.sub(r'\bALIAS\."?[A-Z_][A-Z0-9_]*"?', '', sql, flags=re.IGNORECASE).strip()

        return sql

    @staticmethod
    def _dax_divide_lost_its_division(dax: str, sql: str) -> bool:
        """True if `dax` calls DIVIDE(...) as a function but `sql` contains
        no division operator — a known LLM failure mode where the numerator
        survives but the denominator is silently dropped. Keyed purely on
        DAX/SQL grammar shape (a DIVIDE call vs. an absent '/'), not on any
        specific measure name, so it catches this failure for any DIVIDE
        expression rather than one hardcoded pair of measures."""
        if not re.search(r"\bDIVIDE\s*\(", dax or "", re.IGNORECASE):
            return False
        return "/" not in (sql or "")

        
    def _sanitize_semantic_name(self, name: str) -> str:
        """Sanitize semantic name and ensure it does not start with a digit."""
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _auto_qualify_cross_table_refs(self, sql: str, dataset_aliases: Dict[str, str]) -> str:
        """
        Automatically qualify TABLE.COLUMN references with proper aliases.
        No hardcoding needed – works for any table.
        """
        if not sql or not dataset_aliases:
            return sql
        
        # Sort by length (longest first) to avoid partial matches
        sorted_tables = sorted(dataset_aliases.keys(), key=len, reverse=True)
        
        for table in sorted_tables:
            alias = dataset_aliases[table]
            
            # Pattern: TABLE.COLUMN or TABLE."COLUMN"
            pattern1 = rf'\b{re.escape(table)}\.\"([^"]+)\"'
            sql = re.sub(pattern1, rf'{alias}."\1"', sql, flags=re.IGNORECASE)
            
            pattern2 = rf'\b{re.escape(table)}\.([A-Za-z_][A-Za-z0-9_]*)'
            sql = re.sub(pattern2, rf'{alias}."\1"', sql, flags=re.IGNORECASE)
        
        return sql

    def _sanitize_sql_markdown(self, sql: str) -> str:
        if not sql: return ""
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
        return sql.strip()

    @staticmethod
    def _is_scalar_metric_sql(expr: str) -> bool:
        if not expr:
            return False
        upper = f" {expr.upper()} "
        forbidden = (
            " SELECT ",
            " FROM ",
            " JOIN ",
            " WITH ",
            " UNION ",
            ";",
        )
        return not any(token in upper for token in forbidden)

    def _extract_column_names_from_metric_expression(self, expr: Optional[str], dataset_name: str) -> set[tuple[str, str]]:
        if not expr or not isinstance(expr, str):
            return set()

        result = set()

        dax_patterns = [
            r"(?:'[^']*')?\s*\[([^\]]+)\]",
        ]
        for pattern in dax_patterns:
            matches = re.findall(pattern, expr)
            for col_name in matches:
                sanitized = self._id.sanitize_column(col_name)
                result.add((dataset_name, sanitized))

        sql_agg_pattern = r"(?:SUM|AVG|AVERAGE|COUNT|MIN|MAX|DISTINCTCOUNT|COUNT_DISTINCT|COUNT_IF)\s*\(\s*(?:DISTINCT\s+)?(?:\")?([A-Za-z_][A-Za-z0-9_]*)(?:\")?(?:\s+[A-Z]+)?\s*\)"
        matches = re.findall(sql_agg_pattern, expr, re.IGNORECASE)
        for col_name in matches:
            sanitized = self._id.sanitize_column(col_name)
            result.add((dataset_name, sanitized))

        return result

    def _try_basic_dax_metric_fallback_expression(
        self,
        metric: Any,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
        model: Optional[Any] = None,
        dataset_by_name: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        raw_expr = (metric.expression or "").strip()
        if not raw_expr:
            return None

        expr = " ".join(raw_expr.split())
        
        if "DATEADD" in expr.upper():
            logger.warning("High-risk time-intelligence DAX detected. Prefer parser-safe modeling.")
            return None

        if "[" in expr and "]" in expr and ("-" in expr or "+" in expr or "*" in expr or "/" in expr) and not re.search(r'(SUM|AVERAGE|MIN|MAX|COUNT)\(', expr, re.IGNORECASE):
            logger.warning("Measure dependency variance expression detected. Prefer explicit parser-safe formula.")
            if model is None:
                return None

        # SAMEPERIODLASTYEAR(SUM(...), ...) / TOTALYTD(SUM(...), ...) — a
        # direct-aggregate time-intelligence call — used to be special-cased
        # here with its own inline CASE WHEN, anchored to CURRENT_DATE()
        # (today's wall-clock date) rather than the enriched view's MAX_DATE
        # anchor (the actual latest date present in the fact data) — wrong
        # whenever the data doesn't extend all the way to today, and a
        # second, independent implementation of the exact same DAX shapes
        # dax_ast_parser.DaxSqlRenderer._render_period_to_date /
        # _render_lag_period already handle correctly (anchored to the real
        # data, and — see converter/time_intelligence_shapes.py — able to
        # reference a precomputed flag column instead of a raw MAX_DATE
        # reference, which Snowflake's semantic-view compiler rejects even
        # though the column physically exists). Falls through to the same
        # DAXTranslator().translate() call below that the measure-reference
        # form of these functions already used, instead of maintaining a
        # second, divergent path for the direct-aggregate form.
        known_cols = dataset_col_lookup.get(metric.dataset, set())

        if re.match(r"(?i)^COUNTROWS\(\s*'[^']+'\s*\)$", expr):
            return "COUNT(*)"

        m_blank = re.match(r"(?i)^COUNTBLANK\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
        if m_blank:
            col_name = self._id.sanitize_column(m_blank.group(1))
            if known_cols and col_name not in known_cols:
                return None
            return f'COUNT_IF({table_alias}."{col_name}" IS NULL)'

        m_agg = re.match(r"(?i)^(SUM|AVERAGE|MIN|MAX|COUNT|DISTINCTCOUNT)\(\s*(?:'[^']+'\s*)?\[([^\]]+)\]\s*\)$", expr)
        if m_agg:
            agg = m_agg.group(1).upper()
            col_name = self._id.sanitize_column(m_agg.group(2))
            if known_cols and col_name not in known_cols:
                return None

            if agg == "AVERAGE":
                return f'AVG({table_alias}."{col_name}")'
            if agg == "DISTINCTCOUNT":
                return f'COUNT(DISTINCT {table_alias}."{col_name}")'
            if agg == "SUM":
                return self._build_safe_sum_sql(f'{table_alias}."{col_name}"', col_name)
            return f'{agg}({table_alias}."{col_name}")'

        metrics_list = list(model.metrics) if (model is not None and getattr(model, "metrics", None)) else [metric]

        # TOTALYTD([SomeMeasure], 'Date'[Date]) — a time-intelligence function
        # wrapping a measure reference — used to be special-cased here with a
        # bare OVER(PARTITION BY ... ORDER BY ...) window function: invalid
        # for a Snowflake semantic-view METRICS clause, and with no
        # date-range bound at all (running total ordered by period, not
        # "up to today"). DAXTranslator.translate() below already handles
        # this shape correctly and generally via the AST renderer's
        # CASE-WHEN-bounded translation (see
        # dax_ast_parser.DaxSqlRenderer._render_period_to_date), so this now
        # falls straight through to that instead of a separate, broken path.
        try:
            translated = self._get_dax_translator().translate(
                raw_expr,
                table_alias,
                metric.dataset,
                metric_name=metric.unique_name,
                metrics_context=metrics_list,
                # Narrowed to this metric's own fact table -- see the
                # anchor_flag_map docstring in __init__ for why a flat,
                # unqualified map would risk resolving a flag column from a
                # *different* fact table's enriched view.
                anchor_flag_map=self.anchor_flag_map.get(str(metric.dataset or "").casefold(), {}),
            )
            if translated.is_success and translated.sql:
                return translated.sql
        except Exception as exc:
            logger.debug(
                "Deterministic DAX translation fallback failed for metric '%s': %s",
                metric.unique_name,
                exc,
            )

        return None

    def _try_multi_model_translation(
        self,
        dax_expression: str,
        metric: Any,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
    ) -> Optional[str]:
        """Try multiple LLM models via LangChain/Featherless."""
        try:
            schema_context = {
                ds_name: sorted(list(cols))
                for ds_name, cols in dataset_col_lookup.items()
                if cols
            }
            prompt = self._build_openai_dax_prompt(
                dax_expression=dax_expression,
                metric_name=str(getattr(metric, "unique_name", "") or ""),
                dataset_name=str(getattr(metric, "dataset", "") or ""),
                table_alias=table_alias,
                schema_context=schema_context,
            )
            
            from semabridge.converter.multi_model_translator import get_multi_model_translator
            translator = get_multi_model_translator()
            return translator.translate_with_failover(
                dax=dax_expression,
                metric_name=str(getattr(metric, "unique_name", "") or ""),
                prompt=prompt,
                fallback_translator=self,
                metric=metric,
                table_alias=table_alias,
                dataset_col_lookup=dataset_col_lookup,
            )
        except Exception as e:
            logger.debug(f"Multi-model translation failed: {e}")
            return None

    def _try_featherless_translation(
        self,
        dax_expression: str,
        metric: Any,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
    ) -> Optional[str]:
        """Attempt translation using Featherless API (LangChain)."""
        try:
            schema_context = {
                ds_name: sorted(list(cols))
                for ds_name, cols in dataset_col_lookup.items()
                if cols
            }
            prompt = self._build_openai_dax_prompt(
                dax_expression=dax_expression,
                metric_name=str(getattr(metric, "unique_name", "") or ""),
                dataset_name=str(getattr(metric, "dataset", "") or ""),
                table_alias=table_alias,
                schema_context=schema_context,
            )
            from semabridge.converter.featherless_translator import translate_with_featherless
            return translate_with_featherless(dax_expression, str(getattr(metric, "unique_name", "") or ""), prompt)
        except Exception as e:
            logger.debug(f"Featherless translation error: {e}")
            return None

    def _try_llm_metric_fallback_expression(
        self,
        *,
        metric: Any,
        metric_name: str,
        table_alias: str,
        alias_by_raw: Dict[str, str],
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_name_set: set[str],
        all_physical_col_names: set[str],
        emittable_metric_name_set: set[str],
        skipped_metric_names: set[str],
        metric_to_alias: Optional[Dict[str, str]] = None,
        dataset_col_types: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Optional[str]:
        """Thin shim onto DaxTranslationService (Step 2 of the approved
        consolidation migration). Same signature, same external contract
        (Optional[str]) as before this cutover — every existing caller is
        unaffected.

        Two pieces of the original implementation are deliberately kept
        here rather than delegated, because DaxTranslationService has no
        equivalent hook for either and dropping them would be a real
        regression, not a shape change:

        - The rule-based-translation-first step (dax_rule_translator.
          rule_based_translation). DaxTranslationService's Tier 1-4
          deliberately excludes this separate rule engine (see
          tiers_1_4.py) — some real metrics (e.g. proj-pbix-test's
          SENTIMENT_GAP) only resolve through it. Unchanged below.
        - The OpenAI batch-prefetch cache and the unresolved-metric-
          reference post-check (all_physical_col_names /
          emittable_metric_name_set / skipped_metric_names) — Pipeline-B-
          specific emission-time bookkeeping that a generic translation
          service has no business knowing about. Unchanged below.

        Everything else — Featherless/multi-model/OpenAI/Gemini candidate
        generation and the shared repair/validation loop — is now handled
        by DaxTranslationService, using the same validation logic
        (verbatim-salvaged into dax_translation/tier5/validation.py) this
        method used to call directly via self._validate_metric_column_references
        / self._normalize_metric_column_references. Those two methods are
        untouched on this class and still used for the two steps above.

        One deliberate behavior change, expected and explained (not a
        bug): DaxTranslationService.translate_metric() tries the
        deterministic Tier 1-4 translators (converter/dax_translator.py)
        BEFORE any LLM call — this method never did that itself before
        (that only happened one level up, in metrics_clause_builder.py,
        as a *second*, later fallback via
        _try_basic_dax_metric_fallback_expression). Some metrics that
        previously only reached the deterministic tiers after the rule
        engine AND every LLM provider had already declined will now
        resolve here, deterministically, without any LLM call — this is
        the intended fix for the "rule-engine+LLM tried before the
        correct deterministic pipeline" dispatch-order finding, arriving
        as a natural side effect of this cutover rather than a separate
        change.
        """

        def _passes_unresolved_metric_ref_guard(expr: str) -> bool:
            unresolved_metric_refs = [
                r for r in re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr)
                if r in metric_name_set
                and r not in all_physical_col_names
                and (
                    r not in emittable_metric_name_set
                    or r in skipped_metric_names
                )
                and r != metric_name
            ]
            return not unresolved_metric_refs

        dax_expression = (getattr(metric, "expression", None) or "").strip()
        if not dax_expression:
            return None

        # 1. Rule-based translation first — unchanged from the original.
        try:
            from semabridge.converter.dax_rule_translator import (
                is_simple_metric,
                rule_based_translation,
            )
            metric_label = metric.name if hasattr(metric, "name") else metric_name
            rule_based_sql = rule_based_translation(
                dax_expression, table_alias, metric_label,
                # Same fact-table narrowing as the deterministic AST path
                # above -- see anchor_flag_map's docstring in __init__.
                anchor_flag_map=self.anchor_flag_map.get(str(getattr(metric, "dataset", "") or "").casefold(), {}),
            )

            if rule_based_sql:
                normalized_rule_sql = self.fix_common_llm_issues(rule_based_sql, dax_expression)
                normalized_rule_sql = self._normalize_metric_column_references(
                    normalized_rule_sql,
                    metric.unique_name,
                    dataset_col_lookup,
                    dataset_aliases,
                    metric_names=metric_name_set,
                    preferred_table_alias=table_alias,
                    metric_to_alias=metric_to_alias,
                    original_dax=dax_expression,
                )
                is_valid, issues = self._validate_metric_column_references(
                    normalized_rule_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_name_set
                )
                if (
                    is_valid
                    and not issues
                    and not is_null_cast_sql(normalized_rule_sql)
                    and _passes_unresolved_metric_ref_guard(normalized_rule_sql)
                ):
                    logger.info(f"Rule-based translation for '{metric_name}' is valid.")
                    return normalized_rule_sql
                else:
                    logger.warning(
                        f"Rule-based translation for '{metric_name}' failed validation. Issues: {issues}."
                    )
        except Exception as ex:
            logger.debug(f"Local fallback unavailable for metric '{metric_name}': {ex}")

        # 2. OpenAI batch-prefetch cache — validated through the same
        # repair/validation calls the original used, unchanged.
        prefetched_sql = self._openai_prefetch_sql_by_metric.get(metric_name)
        if prefetched_sql:
            expr = self._sanitize_sql_markdown(prefetched_sql)
            if (
                expr
                and "SELECT" not in expr.upper()
                and self._is_scalar_metric_sql(expr)
                and not self._dax_divide_lost_its_division(dax_expression, expr)
            ):
                expr = self.fix_common_llm_issues(expr, dax_expression)
                expr = self._id.resolve_dot_notation(
                    expr, alias_by_raw, sanitize_col_fn=self._id.sanitize_column,
                )
                expr = self._normalize_metric_column_references(
                    expr,
                    metric.unique_name,
                    dataset_col_lookup,
                    dataset_aliases,
                    metric_names=metric_name_set,
                    preferred_table_alias=table_alias,
                    metric_to_alias=metric_to_alias,
                    original_dax=dax_expression,
                )
                is_valid, _ = self._validate_metric_column_references(
                    expr, metric.unique_name, dataset_col_lookup, dataset_aliases, metric_names=metric_name_set,
                )
                expr_upper = expr.upper().strip()
                if (
                    is_valid
                    and expr.strip()
                    and expr_upper != 'SUM(*)'
                    and not expr_upper.endswith('SUM(*)')
                    and not is_null_cast_sql(expr)
                    and _passes_unresolved_metric_ref_guard(expr)
                ):
                    logger.info(f"Recovered metric '{metric.unique_name}' via prefetch cache")
                    return self.fix_common_llm_issues(expr, dax_expression)

        # 3. Everything else — DaxTranslationService: deterministic Tier
        # 1-4 first, then the unified Tier 5 provider chain with mandatory
        # schema-existence validation for every candidate.
        try:
            from semabridge.dax_translation.types import TranslationRequest

            request = TranslationRequest(
                dax=dax_expression,
                dataset_name=str(getattr(metric, "dataset", "") or ""),
                table_alias=table_alias,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                metric_name=metric_name,
                dialect=self.dialect,
                metric_names=metric_name_set,
                dataset_col_types=dataset_col_types,
            )
            result = self._get_dax_translation_service().translate_metric(request)
            if (
                result.is_success
                and result.sql
                and not is_null_cast_sql(result.sql)
                and _passes_unresolved_metric_ref_guard(result.sql)
            ):
                logger.info(
                    f"Recovered metric '{metric.unique_name}' via DaxTranslationService "
                    f"(tier={result.tier}, provider={result.provider})"
                )
                return result.sql
        except Exception as exc:
            logger.debug(f"DaxTranslationService fallback failed for metric '{metric_name}': {exc}")

        return None

    def prefetch_openai_metric_translations(
        self,
        *,
        metrics: List[Any],
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
    ) -> None:
        """Batch translate complex DAX metrics with OpenAI (up to 20 per batch)."""
        if self._openai_prefetch_done:
            return
        self._openai_prefetch_done = True

        api_key = os.getenv("OPENAI_API_KEY") or "FAKE_KEY_FOR_TESTING"
        if not api_key:
            return
        try:
            from openai import OpenAI
        except Exception as exc:
            logger.warning("OpenAI prefetch unavailable: %s", exc)
            return

        pending: List[Any] = []
        for metric in metrics or []:
            expr = (getattr(metric, "expression", None) or "").strip()
            if not expr:
                continue
            pending.append(metric)
        if not pending:
            return

        schema_context = {
            ds_name: sorted(list(cols))
            for ds_name, cols in dataset_col_lookup.items()
            if cols
        }
        model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
        client = OpenAI(api_key=api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
        batch_size = 20

        for start in range(0, len(pending), batch_size):
            chunk = pending[start:start + batch_size]
            prompt = self._build_openai_batch_prompt(
                metrics=chunk,
                table_alias=table_alias,
                schema_context=schema_context,
            )
            try:
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You translate Power BI DAX measures to Snowflake Semantic View metric SQL. "
                                "Return JSON only."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                    max_tokens=int(os.getenv("OPENAI_DAX_BATCH_MAX_TOKENS", "2200")),
                    timeout=float(os.getenv("OPENAI_DAX_TIMEOUT", "30")),
                )
                payload = (response.choices[0].message.content or "").strip()
                parsed = self._parse_openai_batch_payload(payload)
                for metric in chunk:
                    name = str(getattr(metric, "unique_name", "") or "")
                    sql = self._sanitize_sql_markdown(parsed.get(name, ""))
                    if sql and self._is_safe_llm_metric_sql(sql):
                        self._openai_prefetch_sql_by_metric[name] = sql
            except Exception as exc:
                logger.warning("OpenAI prefetch batch failed for %d metrics: %s", len(chunk), exc)

        if self._openai_prefetch_sql_by_metric:
            logger.info(
                "OpenAI batch-prefetched %d metric SQL expressions.",
                len(self._openai_prefetch_sql_by_metric),
            )

    def _build_openai_batch_prompt(
        self,
        *,
        metrics: List[Any],
        table_alias: str,
        schema_context: Dict[str, list[str]],
    ) -> str:
        schema_lines = []
        for table, cols in sorted(schema_context.items()):
            schema_lines.append(f"- {table}: {', '.join(cols[:80])}")
        schema_text = "\n".join(schema_lines[:40]) or "- <schema unavailable>"

        metric_lines = []
        for metric in metrics:
            metric_name = str(getattr(metric, "unique_name", "") or "")
            dataset_name = str(getattr(metric, "dataset", "") or "")
            dax_expr = " ".join(str(getattr(metric, "expression", "") or "").split())
            metric_lines.append(
                f'{{"name":"{metric_name}","dataset":"{dataset_name}","dax":"{dax_expr}"}}'
            )

        return (
            "Dialect: Snowflake Semantic View METRICS clause\n"
            f"Default table alias: {table_alias}\n"
            "Rules:\n"
            "- Return ONLY valid JSON object mapping metric name to SQL expression.\n"
            "- No markdown, no extra keys, no prose.\n"
            "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
            "- Do not nest aggregate functions like SUM(MAX(...)).\n"
            "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
            "- If pattern is impossible in metric SQL, return CAST(NULL AS DOUBLE).\n"
            "Schema context:\n"
            f"{schema_text}\n"
            "Metrics:\n"
            + "\n".join(metric_lines)
        )

    def _parse_openai_batch_payload(self, payload: str) -> Dict[str, str]:
        cleaned = self._sanitize_sql_markdown(payload or "").strip()
        if not cleaned:
            return {}
        cleaned = re.sub(r"^json\s*", "", cleaned, flags=re.IGNORECASE)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str)}
        except Exception:
            pass
        return {}

    def _try_openai_dax_translation(
        self,
        *,
        dax_expression: str,
        metric: Any,
        table_alias: str,
        dataset_col_lookup: Dict[str, set[str]],
    ) -> Optional[str]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None

        try:
            from openai import OpenAI
        except Exception as exc:
            logger.warning("OpenAI DAX translation requested but openai package is unavailable: %s", exc)
            return None

        schema_context = {
            ds_name: sorted(list(cols))
            for ds_name, cols in dataset_col_lookup.items()
            if cols
        }
        model_name = os.getenv("OPENAI_DAX_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o"))
        prompt = self._build_openai_dax_prompt(
            dax_expression=dax_expression,
            metric_name=str(getattr(metric, "unique_name", "") or ""),
            dataset_name=str(getattr(metric, "dataset", "") or ""),
            table_alias=table_alias,
            schema_context=schema_context,
        )

        timeout = float(os.getenv("OPENAI_DAX_TIMEOUT", "120"))
        max_retries = int(os.getenv("OPENAI_DAX_MAX_RETRIES", "3"))
        sql = None
        for attempt in range(1, max_retries + 1):
            try:
                client = OpenAI(api_key=api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
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
                    timeout=timeout,
                )
                sql = (response.choices[0].message.content or "").strip()
                break
            except Exception as exc:
                logger.warning(
                    "OpenAI DAX translation attempt %d/%d failed for metric '%s': %s",
                    attempt,
                    max_retries,
                    getattr(metric, "unique_name", ""),
                    exc,
                )
                if attempt < max_retries:
                    import time

                    time.sleep(min(2 ** attempt, 10))
                else:
                    return None

        sql = self._sanitize_sql_markdown(sql)
        if not self._is_safe_llm_metric_sql(sql):
            logger.warning(
                "OpenAI DAX translation rejected for metric '%s': %s",
                getattr(metric, "unique_name", ""),
                sql[:120],
            )
            return None

        logger.info(
            "OpenAI translated DAX for metric '%s': %s",
            getattr(metric, "unique_name", ""),
            sql[:160],
        )
        return sql

    def _build_openai_dax_prompt(
        self,
        *,
        dax_expression: str,
        metric_name: str,
        dataset_name: str,
        table_alias: str,
        schema_context: Dict[str, list[str]],
    ) -> str:
        skill_dir = os.getenv("SEMABRIDGE_LLM_SKILLS_DIR", "prompts")
        skill_blocks: list[str] = []
        try:
            from pathlib import Path

            base = Path(skill_dir)
            if not base.is_absolute():
                base = (Path.cwd() / base).resolve()
            for name in (
                "system.md",
                "rules.md",
                "valid_examples.md",
                "invalid_examples.md",
                "rolling_windows.md",
                "calculate_filters.md",
                "schema_resolution.md",
                "time_intelligence.md",
            ):
                path = base / name
                if path.exists():
                    skill_blocks.append(path.read_text(encoding="utf-8").strip())
        except Exception as exc:
            logger.debug("LLM skill prompts not loaded: %s", exc)

        schema_lines = []
        for table, cols in sorted(schema_context.items()):
            schema_lines.append(f"- {table}: {', '.join(cols[:80])}")
        schema_text = "\n".join(schema_lines[:40]) or "- <schema unavailable>"

        return (
            ("\n\n".join([b for b in skill_blocks if b]) + "\n\n" if skill_blocks else "") +
            "Dialect: Snowflake Semantic View METRICS clause\n"
            f"Metric name: {metric_name}\n"
            f"Default dataset: {dataset_name}\n"
            f"Default table alias: {table_alias}\n"
            "Rules:\n"
            "- Return only a single SQL expression, no explanation.\n"
            "- Use table aliases and columns from the schema context when known.\n"
            "- Prefer aggregate expressions valid in a Snowflake semantic view metric.\n"
            "- Do not use SELECT, FROM, JOIN, CTEs, subqueries, OVER/window functions, DDL, or DML.\n"
            "- Do not nest aggregate functions like SUM(MAX(...)).\n"
            "- For CALCULATE/FILTER equality predicates, use SUM(CASE WHEN ... THEN measure_column ELSE 0 END).\n"
            "- Quote identifiers only when needed as ALIAS.\"COLUMN\"; uppercase Snowflake column names.\n"
            "- If a pattern is impossible in a metric expression, return CAST(NULL AS DOUBLE).\n"
            "Schema context:\n"
            f"{schema_text}\n"
            "DAX:\n"
            f"{dax_expression}"
        )

    def _is_safe_llm_metric_sql(self, sql: str) -> bool:
        if not sql or "{" in sql or "}" in sql:
            return False
        upper = sql.upper()
        forbidden = (
            " SELECT ",
            "(SELECT",
            " FROM ",
            " JOIN ",
            " WITH ",
            " OVER ",
            " DROP ",
            " DELETE ",
            " TRUNCATE ",
            " INSERT ",
            " UPDATE ",
            " ALTER ",
            ";",
        )
        padded = f" {upper} "
        if any(token in padded for token in forbidden):
            return False
        if re.search(r"\b(SUM|COUNT|AVG|MIN|MAX|ANY_VALUE)\s*\([^)]*\b(SUM|COUNT|AVG|MIN|MAX|ANY_VALUE)\s*\(", sql, re.IGNORECASE | re.DOTALL):
            return False
        return True

    def _get_effective_known_columns(self, dataset_name: str, dataset_col_lookup: Dict[str, Set[str]]) -> Set[str]:
        cols = set(dataset_col_lookup.get(dataset_name, set()))
        if getattr(self, "anchor_flag_map", None):
            ds_flags = self.anchor_flag_map.get(str(dataset_name or "").casefold()) or {}
            for flag_val in ds_flags.values():
                if isinstance(flag_val, str) and flag_val:
                    cols.add(flag_val)
        return cols

    def _validate_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        alias_to_dataset.update({str(v).lower(): k for k, v in dataset_aliases.items()})
        alias_to_dataset.update({str(v).upper(): k for k, v in dataset_aliases.items()})
        # Also accept raw dataset/table names as valid qualifiers (for cross-table refs
        # generated by rule translators, e.g. PRODUCT."ISVANARSDEL").
        raw_name_to_dataset = {k: k for k in dataset_col_lookup}
        raw_name_to_dataset.update({k.upper(): k for k in dataset_col_lookup})
        raw_name_to_dataset.update({k.lower(): k for k in dataset_col_lookup})

        patterns = [
            r'(\w+)\."([^"]+)"',
            r'(\w+)\.([A-Za-z_][A-Za-z0-9_]*)',
        ]

        all_refs = []
        for pattern in patterns:
            matches = re.findall(pattern, metric_sql)
            all_refs.extend(matches)

        if not all_refs:
            bare_identifiers = set(re.findall(r'"([A-Z_][A-Z0-9_$]*)"', metric_sql))
            for ident in sorted(bare_identifiers):
                if metric_names and ident in metric_names:
                    continue
                owners = [
                    ds_name for ds_name, cols in dataset_col_lookup.items()
                    if self._resolve_column_name_for_dataset(cols, ident)
                ]
                if owners:
                    error = (
                        f"Unqualified physical identifier '{ident}' in metric SQL; owners={sorted(set(owners))}"
                    )
                    logger.debug(f"Metric '{metric_name}': {error}")
                    print(f"DEBUG: Validation failed for '{metric_name}': {error}") # DEBUG PRINT
                    return False, error
            logger.debug(f"No cross-table references found in metric '{metric_name}'")
            return True, None

        for table_alias, col_name in all_refs:
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                # Fall back to checking raw table names (e.g. PRODUCT."ISVANARSDEL")
                dataset_name = raw_name_to_dataset.get(table_alias)
            if not dataset_name:
                dataset_name = self._heal_unknown_alias(table_alias, col_name, dataset_aliases, dataset_col_lookup, metric_names)
            if not dataset_name:
                error = f"Alias '{table_alias}' not found in dataset mapping"
                logger.warning(f"Metric '{metric_name}': {error}")
                return False, error

            known_columns = self._get_effective_known_columns(dataset_name, dataset_col_lookup)
            sanitized_col_name = self._id.sanitize_column(col_name)
            resolved_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=False,
            )
            if resolved_metric_ref:
                continue

            fuzzy_metric_ref = self._resolve_metric_reference_name(
                metric_names,
                sanitized_col_name,
                allow_fuzzy=True,
            )
            if fuzzy_metric_ref:
                continue
            resolved_col = self._resolve_column_name_for_dataset(
                known_columns,
                sanitized_col_name,
            )
            if not resolved_col:
                error = (
                    f"Column '{col_name}' (sanitized: '{sanitized_col_name}') not found in dataset '{dataset_name}'. Available columns: {sorted(known_columns)}"
                )
                logger.debug(f"Metric '{metric_name}': {error}")
                return False, error

        logger.debug(f"Metric '{metric_name}': All column references valid")
        return True, None

    def _extract_dax_table_hints(self, dax: Optional[str]) -> Dict[str, str]:
        """Sanitized-column-name -> DAX-source-table-name, from every
        'Table'[Column] / Table[Column] reference in the original DAX.

        Best-effort only: if the same sanitized column name is qualified
        against two different tables within one expression (rare -- most
        DAX measures reference a given column against a single table),
        the last one found wins; this is a hint used only to catch an
        already-qualified reference that disagrees with it, never a hard
        routing decision on its own (see _resolve_dax_hinted_dataset).
        """
        hints: Dict[str, str] = {}
        if not dax or not self._id:
            return hints
        for m in _DAX_QUALIFIED_COLUMN_PATTERN.finditer(dax):
            table_name = (m.group(1) or m.group(3) or "").strip()
            col_name = (m.group(2) or m.group(4) or "").strip()
            if not table_name or not col_name:
                continue
            hints[self._id.sanitize_column(col_name)] = table_name
        return hints

    def _resolve_dax_hinted_dataset(
        self,
        sanitized_col_name: str,
        current_dataset_name: Optional[str],
        dax_table_hints: Optional[Dict[str, str]],
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, set[str]],
    ) -> Optional[str]:
        """Real fix for the bug this closes: a metric's SQL can already be
        fully, "validly" qualified (a real alias, a real column on that
        alias's dataset) and still be WRONG relative to the DAX it came
        from -- e.g. Tier-5 output referencing kpi."RUNNING_YEAR" when the
        DAX explicitly wrote CALCULATE(..., 'Date'[Running Year]=1). Every
        existing check here only asks "does this alias/column combination
        exist," which this case already satisfies, so nothing upstream
        catches it. This asks a different, narrower question instead: does
        the DAX name a *different* table for this exact column, and does
        THAT table structurally have it too?

        Deliberately conservative -- never returns a dataset the hint
        merely names; only one confirmed (via dataset_col_lookup) to
        actually declare the column, same standard _heal_unknown_alias
        already holds itself to. Returns None (no override) whenever the
        hinted table doesn't resolve to a real dataset, that dataset
        doesn't have the column, or the hint agrees with what's already
        there -- so a metric with no bracket-qualified DAX references (the
        overwhelming majority) is completely unaffected by this check.
        """
        if not dax_table_hints:
            return None
        hinted_table = dax_table_hints.get(sanitized_col_name)
        if not hinted_table:
            return None
        hinted_cf = hinted_table.strip().strip("'").casefold()
        hinted_dataset = next(
            (ds for ds in dataset_aliases if ds.strip().casefold() == hinted_cf),
            None,
        )
        if not hinted_dataset or hinted_dataset == current_dataset_name:
            return None
        if not self._resolve_column_name_for_dataset(
            dataset_col_lookup.get(hinted_dataset, set()), sanitized_col_name
        ):
            return None
        return hinted_dataset

    def _normalize_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None,
        metric_to_alias: Optional[Dict[str, str]] = None,
        original_dax: Optional[str] = None,
    ) -> str:
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        alias_to_dataset.update({str(v).lower(): k for k, v in dataset_aliases.items()})
        alias_to_dataset.update({str(v).upper(): k for k, v in dataset_aliases.items()})
        # Empty when original_dax is None (the default for every existing
        # caller that hasn't opted in) -- _resolve_dax_hinted_dataset is then
        # a guaranteed no-op below, so this parameter is purely additive.
        dax_table_hints = self._extract_dax_table_hints(original_dax)
        normalized_sql = metric_sql
        normalized_sql = self._normalize_display_name_metric_references(normalized_sql, metric_names)

        try:
            from semabridge.utils.naming import to_alias as _to_alias
            legacy_alias_remap: Dict[str, str] = {}
            for ds_name, declared_alias in dataset_aliases.items():
                legacy = _to_alias(ds_name)
                if legacy and legacy != declared_alias and legacy not in alias_to_dataset:
                    legacy_alias_remap[legacy] = declared_alias
            if legacy_alias_remap:
                for legacy, declared in legacy_alias_remap.items():
                    normalized_sql = re.sub(
                        rf'\b{re.escape(legacy)}\.',
                        f'{declared}.',
                        normalized_sql,
                        flags=re.IGNORECASE,
                    )
        except Exception:
            pass

        def _format_metric_ref(table_alias: str, col_name: str) -> str:
            safe_alias = f'"{table_alias}"' if table_alias.lower() in self._id._reserved else table_alias
            if "$" in col_name or col_name.lower() in self._id._reserved:
                return f'{safe_alias}."{col_name}"'
            return f"{safe_alias}.{col_name}"

        quoted_pattern = r'(?:"(\w+)"|(\w+))\.(["\'])([^"\']+)\3'
        for match in re.finditer(quoted_pattern, normalized_sql):
            table_alias = match.group(1) or match.group(2)
            col_name = match.group(4)
            sanitized_col_name = self._id.sanitize_column(col_name)
            dataset_name = alias_to_dataset.get(table_alias)
            hinted_dataset = self._resolve_dax_hinted_dataset(
                sanitized_col_name, dataset_name, dax_table_hints, dataset_aliases, dataset_col_lookup,
            )
            if hinted_dataset:
                dataset_name = hinted_dataset
                table_alias = dataset_aliases.get(hinted_dataset, table_alias)
            elif not dataset_name:
                dataset_name = self._heal_unknown_alias(table_alias, col_name, dataset_aliases, dataset_col_lookup, metric_names)
                if dataset_name:
                    table_alias = dataset_aliases.get(dataset_name, table_alias)
            if not dataset_name:
                continue
            known_columns = self._get_effective_known_columns(dataset_name, dataset_col_lookup)
            resolved_col = self._resolve_column_name_for_dataset(known_columns, sanitized_col_name)
            if not resolved_col:
                owners = [ds for ds, cols in dataset_col_lookup.items() if self._resolve_column_name_for_dataset(cols, sanitized_col_name)]
                if len(owners) == 1:
                    owner_alias = dataset_aliases.get(owners[0])
                    if owner_alias:
                        owner_col = self._resolve_column_name_for_dataset(dataset_col_lookup.get(owners[0], set()), sanitized_col_name) or sanitized_col_name
                        old_ref = match.group(0)
                        new_ref = _format_metric_ref(owner_alias, owner_col)
                        normalized_sql = normalized_sql.replace(old_ref, new_ref)
                        logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {new_ref}")
                        continue

                resolved_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
                if resolved_metric_ref:
                    old_ref = match.group(0)
                    new_ref = f'"{resolved_metric_ref}"'
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                    continue

                fuzzy_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, f'"{fuzzy_metric_ref}"')
                    logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}")
                    continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue

            old_ref = match.group(0)
            new_ref = _format_metric_ref(table_alias, sanitized_col_name)
            normalized_sql = normalized_sql.replace(old_ref, new_ref)
            logger.debug(f"Normalized metric '{metric_name}': {old_ref} → {new_ref}")

        unquoted_pattern = r'(?:"(\w+)"|(\w+))\.([A-Za-z_][A-Za-z0-9_$]*)'
        for match in re.finditer(unquoted_pattern, normalized_sql):
            table_alias = match.group(1) or match.group(2)
            col_name = match.group(3)
            sanitized_col_name = self._id.sanitize_column(col_name)
            dataset_name = alias_to_dataset.get(table_alias)
            hinted_dataset = self._resolve_dax_hinted_dataset(
                sanitized_col_name, dataset_name, dax_table_hints, dataset_aliases, dataset_col_lookup,
            )
            if hinted_dataset:
                dataset_name = hinted_dataset
                table_alias = dataset_aliases.get(hinted_dataset, table_alias)
            elif not dataset_name:
                dataset_name = self._heal_unknown_alias(table_alias, col_name, dataset_aliases, dataset_col_lookup, metric_names)
                if dataset_name:
                    table_alias = dataset_aliases.get(dataset_name, table_alias)
            if not dataset_name:
                continue
            known_columns = self._get_effective_known_columns(dataset_name, dataset_col_lookup)
            resolved_col = self._resolve_column_name_for_dataset(known_columns, sanitized_col_name)
            if not resolved_col:
                owners = [ds for ds, cols in dataset_col_lookup.items() if self._resolve_column_name_for_dataset(cols, sanitized_col_name)]
                if len(owners) == 1:
                    owner_alias = dataset_aliases.get(owners[0])
                    if owner_alias:
                        owner_col = self._resolve_column_name_for_dataset(dataset_col_lookup.get(owners[0], set()), sanitized_col_name) or sanitized_col_name
                        old_ref = match.group(0)
                        new_ref = _format_metric_ref(owner_alias, owner_col)
                        normalized_sql = normalized_sql.replace(old_ref, new_ref)
                        logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {new_ref}")
                        continue

                resolved_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=False)
                if resolved_metric_ref:
                    old_ref = match.group(0)
                    new_ref = f'"{resolved_metric_ref}"'
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                    continue

                fuzzy_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, f'"{fuzzy_metric_ref}"')
                    logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}")
                    continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue
            else:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, sanitized_col_name)
                if old_ref != new_ref:
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue

            if col_name != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, sanitized_col_name)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                logger.debug(f"Normalized metric '{metric_name}': {old_ref} → {new_ref}")

        normalized_sql = self._normalize_display_name_metric_references(normalized_sql, metric_names)
        normalized_sql = self._qualify_bare_metric_references(normalized_sql, metric_to_alias)
        normalized_sql = self._quote_bare_metric_references(normalized_sql, metric_names)
        normalized_sql = self._rewrite_metric_aggregate_wrappers(normalized_sql, metric_names)
        normalized_sql = self._repair_bare_aggregate_identifiers(normalized_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._qualify_bare_column_identifiers(
            normalized_sql,
            dataset_col_lookup,
            dataset_aliases,
            metric_names,
            preferred_table_alias=preferred_table_alias,
        )
        normalized_sql = self._normalize_date_part_arguments(normalized_sql)
        normalized_sql = self._qualify_bare_partition_identifiers(normalized_sql, dataset_col_lookup, dataset_aliases, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._dedupe_qualified_column_tokens(normalized_sql)
        normalized_sql = self._rewrite_window_metric_expression(normalized_sql, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._normalize_rolling_monthindex_max_predicates(normalized_sql)
        normalized_sql = self._qualify_bare_metric_references(normalized_sql, metric_to_alias)
        normalized_sql = self._route_qualified_metric_owner_refs(normalized_sql, metric_to_alias)
        normalized_sql = self._normalize_string_boolean_comparisons(normalized_sql)

        return normalized_sql

    def _heal_unknown_alias(
        self,
        table_alias: str,
        col_name: str,
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, set[str]],
        metric_names: Optional[set[str]] = None,
    ) -> Optional[str]:
        """
        Dynamically heal unknown or mismatched table aliases to their correct dataset names.

        Resolved structurally — by checking which real dataset actually
        declares `col_name` — never by pattern-matching `table_alias`'s
        (already-unresolved) spelling.
        """
        sanitized_col_name = self._id.sanitize_column(col_name) if self._id else col_name.upper()

        # 1. If col_name is a known metric, treat alias as a dummy and let metric resolution happen
        if metric_names:
            resolved_metric = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
            if resolved_metric:
                if dataset_aliases:
                    return next(iter(dataset_aliases.keys()))

        # 2. Resolve via real schema: which dataset actually declares this column?
        owners = [
            ds_name for ds_name, cols in dataset_col_lookup.items()
            if self._resolve_column_name_for_dataset(cols, sanitized_col_name)
        ]
        if len(owners) == 1:
            return owners[0]

        return None

    def _qualify_bare_column_identifiers(
        self,
        metric_sql: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None,
    ) -> str:
        if not metric_sql:
            return metric_sql

        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        sanitized_ds_to_dataset = {self._id.sanitize_column(ds): ds for ds in dataset_aliases.keys()}

        keywords = {
            "AND", "AS", "ASC", "AVG", "BETWEEN", "BY", "CASE", "CAST", "COALESCE",
            "CURRENT", "CURRENT_DATE", "DATEADD", "DATEDIFF", "DAY", "DESC",
            "DISTINCT", "DIVIDE", "DOUBLE", "ELSE", "END", "FALSE", "FLOAT", "FROM",
            "GROUP", "IFF", "IN", "INT", "IS", "LAG", "LEFT", "LIKE", "MAX",
            "MIN", "MONTH", "NOT", "NULL", "NULLIF", "OR", "ORDER", "OVER",
            "PARTITION", "ROWS", "SUM", "THEN", "TO_DATE", "TRUE",
            "TRY_CAST", "TRY_TO_DATE", "VARCHAR", "WHEN", "WITH", "SYNONYMS", "YEAR",
        }

        def _resolve_owner(token: str) -> Optional[tuple[str, str]]:
            ident = self._id.sanitize_column(token)
            if metric_names and ident in metric_names:
                return None

            # If token matches a dataset alias/name, prefer column with same name
            dataset_name = alias_to_dataset.get(token) or sanitized_ds_to_dataset.get(token)
            if dataset_name:
                resolved_col = self._resolve_column_name_for_dataset(
                    dataset_col_lookup.get(dataset_name, set()),
                    ident,
                )
                if resolved_col:
                    return dataset_aliases.get(dataset_name), resolved_col

            # Prefer table alias if it contains the column
            if preferred_table_alias:
                preferred_dataset = alias_to_dataset.get(preferred_table_alias)
                if preferred_dataset:
                    resolved_col = self._resolve_column_name_for_dataset(
                        dataset_col_lookup.get(preferred_dataset, set()),
                        ident,
                    )
                    if resolved_col:
                        return preferred_table_alias, resolved_col

            owners = []
            for ds_name, cols in dataset_col_lookup.items():
                resolved_col = self._resolve_column_name_for_dataset(cols, ident)
                if resolved_col:
                    owners.append((ds_name, resolved_col))

            if len(owners) == 1:
                owner_ds, owner_col = owners[0]
                return dataset_aliases.get(owner_ds), owner_col

            return None

        def _replace(match: re.Match) -> str:
            token = match.group(1)
            upper = token.upper()
            if upper in keywords:
                return match.group(0)
            if metric_names and upper in metric_names:
                return match.group(0)
            if token.endswith("("):
                return match.group(0)
            owner = _resolve_owner(token)
            if not owner:
                return match.group(0)
            alias, col = owner
            if not alias:
                return match.group(0)
            return f'{alias}."{col}"'

        pattern = re.compile(r'(?<![\w\."])\b([A-Za-z_][A-Za-z0-9_$]*)\b(?![\w\."])')
        return pattern.sub(_replace, metric_sql)

    @staticmethod
    def _route_qualified_metric_owner_refs(metric_sql: str, metric_to_alias: Optional[Dict[str, str]]) -> str:
        if not metric_sql or not metric_to_alias:
            return metric_sql
        owner_by_metric = {str(name).upper(): str(alias).upper() for name, alias in metric_to_alias.items()}

        def _replace(match: re.Match) -> str:
            alias = match.group(1)
            metric_name = match.group(2).upper()
            owner_alias = owner_by_metric.get(metric_name)
            if owner_alias and alias.upper() != owner_alias:
                return f'"{metric_name}"'
            return match.group(0)

        return re.sub(r'\b([A-Za-z_][A-Za-z0-9_$]*)\."([A-Z_][A-Z0-9_$]*)"', _replace, metric_sql)

    def _normalize_display_name_metric_references(self, metric_sql: str, metric_names: Optional[set[str]]) -> str:
        """Rewrite quoted display-name metric refs to their emitted Snowflake names."""
        if not metric_sql or not metric_names:
            return metric_sql

        def _replace(match: re.Match) -> str:
            token = match.group(1)
            sanitized = self._id.sanitize_alias(token)
            if sanitized in metric_names and sanitized != token:
                return f'"{sanitized}"'
            resolved = self._resolve_metric_reference_name(metric_names, sanitized, allow_fuzzy=True)
            if resolved and resolved != token:
                return f'"{resolved}"'
            return match.group(0)

        def _replace_qualified(match: re.Match) -> str:
            alias = match.group(1)
            token = match.group(2)
            sanitized = self._id.sanitize_alias(token)
            resolved = sanitized if sanitized in metric_names else self._resolve_metric_reference_name(
                metric_names,
                sanitized,
                allow_fuzzy=True,
            )
            if resolved:
                return f'{alias.upper()}."{resolved}"'
            return match.group(0)

        normalized = re.sub(
            r'"([A-Za-z_][A-Za-z0-9_$]*)"\."([^"]+)"',
            _replace_qualified,
            metric_sql,
        )
        return re.sub(r'(?<!\.)"([^"]+)"', _replace, normalized)

    def _repair_bare_aggregate_identifiers(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None
    ) -> str:
        if not metric_sql:
            return metric_sql

        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        sanitized_ds_to_dataset = {self._id.sanitize_column(ds): ds for ds in dataset_aliases.keys()}

        agg_pattern = re.compile(r'\b(SUM|AVG|MIN|MAX|COUNT|DISTINCTCOUNT|COUNT_DISTINCT)\s*\(\s*(DISTINCT\s+)?"?([A-Z_][A-Z0-9_$]*)"?(?:\s*::\s*[A-Z0-9_]+)?\s*\)', flags=re.IGNORECASE)

        def _replace(match: re.Match) -> str:
            agg_fn = match.group(1).upper()
            distinct_kw = bool(match.group(2))
            ident = self._id.sanitize_column(match.group(3))

            if metric_names and ident in metric_names:
                return match.group(0)

            dataset_name = alias_to_dataset.get(ident) or sanitized_ds_to_dataset.get(ident)
            if dataset_name:
                dataset_alias = dataset_aliases.get(dataset_name)
                known_columns = dataset_col_lookup.get(dataset_name, set())
                preferred_col = self._pick_preferred_aggregate_column(metric_name, known_columns)
                if not dataset_alias or not preferred_col:
                    logger.warning("Metric '%s': unresolved aggregate identifier '%s' (dataset token path); coercing to NULL", metric_name, ident)
                    return "NULL"

                col_ref = f'{dataset_alias}."{preferred_col}"'
                if agg_fn in {"DISTINCTCOUNT", "COUNT_DISTINCT"}:
                    return f'COUNT(DISTINCT {col_ref})'
                if agg_fn == "SUM":
                    return self._build_safe_sum_sql(col_ref, preferred_col)
                if agg_fn == "COUNT" and distinct_kw:
                    return f'COUNT(DISTINCT {col_ref})'
                return f'{agg_fn}({col_ref})'

            owner_candidates: list[tuple[str, str]] = []
            if preferred_table_alias:
                preferred_dataset = alias_to_dataset.get(preferred_table_alias)
                if preferred_dataset:
                    preferred_col = self._resolve_column_name_for_dataset(dataset_col_lookup.get(preferred_dataset, set()), ident)
                    if preferred_col:
                        owner_candidates.append((preferred_dataset, preferred_col))

            if not owner_candidates:
                for ds_name, cols in dataset_col_lookup.items():
                    resolved_col = self._resolve_column_name_for_dataset(cols, ident)
                    if resolved_col:
                        owner_candidates.append((ds_name, resolved_col))

            if len(owner_candidates) != 1:
                logger.warning("Metric '%s': ambiguous/unresolved bare aggregate identifier '%s' owners=%s; coercing to NULL", metric_name, ident, sorted({ds for ds, _ in owner_candidates}))
                return "NULL"

            owner_ds, owner_col = owner_candidates[0]
            owner_alias = dataset_aliases.get(owner_ds)
            if not owner_alias:
                logger.warning("Metric '%s': no alias for resolved aggregate owner '%s'; coercing to NULL", metric_name, owner_ds)
                return "NULL"

            col_ref = f'{owner_alias}."{owner_col}"'
            if agg_fn in {"DISTINCTCOUNT", "COUNT_DISTINCT"}:
                return f'COUNT(DISTINCT {col_ref})'
            if agg_fn == "SUM":
                return self._build_safe_sum_sql(col_ref, owner_col)
            if agg_fn == "COUNT" and distinct_kw:
                return f'COUNT(DISTINCT {col_ref})'
            return f'{agg_fn}({col_ref})'

        return agg_pattern.sub(_replace, metric_sql)

    def _build_safe_sum_sql(
        self,
        expr_sql: str,
        identifier_hint: Optional[str] = None,
        column_data_types: Optional[Dict[Any, str]] = None,
    ) -> str:
        is_flag = False
        if identifier_hint:
            hint = identifier_hint.strip().upper().replace('"', '')
            col_name = hint.split('.')[-1] if '.' in hint else hint
            flag_patterns = [r'^IS_', r'^HAS_', r'^WAS_', r'^DID_', r'^DOES_', r'_FLAG$', r'_FLG$', r'^DELETED$', r'_DELETED$']
            is_flag = any(re.search(p, col_name) for p in flag_patterns)

            # Prevent false-positive boolean flag matching for string-typed columns
            type_lookup = column_data_types or getattr(self, "column_data_types", None) or {}
            if is_flag and type_lookup:
                col_type = ""
                for k, v in type_lookup.items():
                    k_str = str(k[1] if isinstance(k, tuple) else k).upper()
                    if k_str == col_name or k_str == hint:
                        col_type = str(v).lower()
                        break
                if any(st in col_type for st in ("string", "varchar", "char", "text")):
                    is_flag = False

        if is_flag:
            return f"SUM(IFF({expr_sql} = 1 OR {expr_sql} = TRUE, 1, 0))"

        if expr_sql.strip().upper().endswith("::FLOAT"):
            return f"SUM({expr_sql})"
        return f"SUM({expr_sql}::FLOAT)"

    def _normalize_string_boolean_comparisons(
        self,
        sql: str,
        column_data_types: Optional[Dict[Any, str]] = None,
    ) -> str:
        if not sql or ("= TRUE" not in sql.upper() and "= FALSE" not in sql.upper()):
            return sql

        type_lookup = column_data_types or getattr(self, "column_data_types", None) or {}
        if not type_lookup:
            return sql

        string_cols = set()
        for k, v in type_lookup.items():
            col_name = str(k[1] if isinstance(k, tuple) else k).upper()
            col_type = str(v).lower()
            if any(st in col_type for st in ("string", "varchar", "char", "text")):
                string_cols.add(col_name)

        if not string_cols:
            return sql

        for col in string_cols:
            pattern_true = re.compile(rf'((?:[A-Za-z0-9_]+\.)?"?{re.escape(col)}"?) = TRUE', re.IGNORECASE)
            pattern_false = re.compile(rf'((?:[A-Za-z0-9_]+\.)?"?{re.escape(col)}"?) = FALSE', re.IGNORECASE)
            sql = pattern_true.sub(r"\1 = 'Yes'", sql)
            sql = pattern_false.sub(r"\1 = 'No'", sql)

        return sql

    def _resolve_column_name_for_dataset(self, known_columns: set[str], candidate: str) -> Optional[str]:
        if not known_columns: return None
        # Case-insensitive check
        candidate_lower = candidate.lower()
        for col in known_columns:
            if col.lower() == candidate_lower:
                return col
        
        compact = candidate.replace("_", "").lower()
        for col in known_columns:
            if col.replace("_", "").lower() == compact: return col
            
        if candidate.lower().startswith("total_"):
            base = candidate[len("TOTAL_"):]
            base_lower = base.lower()
            for col in known_columns:
                if col.lower() == base_lower:
                    return col
        return None

    def _resolve_metric_reference_name(self, metric_names: Optional[set[str]], candidate: str, *, allow_fuzzy: bool = True) -> Optional[str]:
        if not metric_names: return None
        if candidate in metric_names: return candidate
        compact_candidate = candidate.replace("_", "")
        compact_matches = [m for m in metric_names if m.replace("_", "") == compact_candidate]
        if len(compact_matches) == 1: return compact_matches[0]
        return None

    # ================================================================
    # CRITICAL FIX: Qualify bare metric references WITH sanitization
    # ================================================================
    def _qualify_bare_metric_references(self, metric_sql: str, metric_to_alias: Optional[Dict[str, str]]) -> str:
        if not metric_to_alias:
            return metric_sql
        normalized = metric_sql
        # Loop through metrics sorted by length descending to prevent partial replacements
        for metric_name, owner_alias in sorted(metric_to_alias.items(), key=lambda x: len(x[0]), reverse=True):
            # ✅ CRITICAL: Sanitize metric name first (remove %, $, @, #, spaces)
            sanitized_metric_name = self._id.sanitize_column(metric_name)

            # 1. Match already quoted bare references: e.g. "SENTIMENT" not preceded by a dot
            quoted_pattern = rf'(?<!\.)"{re.escape(sanitized_metric_name)}"'
            normalized = re.sub(quoted_pattern, f'{owner_alias}."{sanitized_metric_name}"', normalized)

            # 2. Match unquoted bare references: e.g. SENTIMENT_GAP not preceded by dot, quotes or word chars
            unquoted_pattern = rf'(?<![\w\.\"])\b{re.escape(metric_name)}\b(?![\w\."])'
            normalized = re.sub(unquoted_pattern, f'{owner_alias}."{sanitized_metric_name}"', normalized)
            
        return normalized
    # ================================================================

    def _quote_bare_metric_references(self, metric_sql: str, metric_names: Optional[set[str]]) -> str:
        if not metric_names: return metric_sql
        normalized = metric_sql
        for metric_name in sorted(metric_names, key=len, reverse=True):
            pattern = rf'(?<![\w\.\"])\b{re.escape(metric_name)}\b(?![\w\."])'
            normalized = re.sub(pattern, f'"{metric_name}"', normalized)
        return normalized

    def _rewrite_metric_aggregate_wrappers(self, metric_sql: str, metric_names: Optional[set[str]]) -> str:
        if not metric_names: return metric_sql
        normalized = metric_sql
        agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*"([A-Z_][A-Z0-9_]*)"\s*\)(?!\s+OVER\b)'
        def _replace(match: re.Match) -> str:
            metric_name = match.group(2)
            if metric_name in metric_names: return f'"{metric_name}"'
            return match.group(0)
        normalized = re.sub(agg_pattern, _replace, normalized)
        composite_agg_pattern = r'\b(SUM|AVG|MIN|MAX|COUNT)\s*\(\s*((?:"[A-Z_][A-Z0-9_]*"\s*[+\-*/]\s*)+"[A-Z_][A-Z0-9_]*")\s*\)'
        def _replace_composite(match: re.Match) -> str:
            expr = match.group(2)
            metric_refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
            if metric_refs and all(ref in metric_names for ref in metric_refs): return expr
            return match.group(0)
        return re.sub(composite_agg_pattern, _replace_composite, normalized)

    def _normalize_date_part_arguments(self, metric_sql: str) -> str:
        normalized = metric_sql
        def _is_date_like(identifier: str) -> bool:
            upper = identifier.upper()
            return upper.endswith('.DATE') or upper.endswith('_DATE')
        def _wrap_try_to_date(match: re.Match) -> str:
            fn = match.group(1)
            arg = match.group(2).strip()
            if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper(): return f"{fn}(TRY_TO_DATE({arg}))"
            return match.group(0)
        normalized = re.sub(r'\b(YEAR|MONTH|DAY|WEEK|QUARTER)\s*\(\s*([^\)]+)\)', _wrap_try_to_date, normalized, flags=re.IGNORECASE)
        def _wrap_extract(match: re.Match) -> str:
            part = match.group(1)
            arg = match.group(2).strip()
            if _is_date_like(arg) and 'TRY_TO_DATE(' not in arg.upper(): return f"EXTRACT({part} FROM TRY_TO_DATE({arg}))"
            return match.group(0)
        normalized = re.sub(r'\bEXTRACT\s*\(\s*([A-Z_]+)\s+FROM\s+([^\)]+)\)', _wrap_extract, normalized, flags=re.IGNORECASE)
        return normalized

    def _normalize_rolling_monthindex_max_predicates(self, metric_sql: str) -> str:
        normalized = metric_sql
        pattern = r'(?P<id>[A-Z_][A-Z0-9_\.]+)\s*<=\s*MAX\(\s*(?P=id)\s*\)\s*AND\s*(?P=id)\s*>\s*MAX\(\s*(?P=id)\s*\)\s*-\s*12'
        def _replace(match: re.Match) -> str:
            month_index_id = match.group('id')
            return f"{month_index_id} > ((YEAR(CURRENT_DATE()) * 12) + MONTH(CURRENT_DATE()) - 12)"
        return re.sub(pattern, _replace, normalized, flags=re.IGNORECASE)

    def _qualify_bare_partition_identifiers(self, metric_sql: str, dataset_col_lookup: Dict[str, set[str]], dataset_aliases: Dict[str, str], preferred_table_alias: Optional[str] = None) -> str:
        if not metric_sql: return metric_sql
        alias_to_dataset = {alias: ds for ds, alias in dataset_aliases.items()}
        pattern = re.compile(r'(?i)(PARTITION\s+BY\s+)("?[A-Z_][A-Z0-9_]*"?)')
        def _replace(match: re.Match) -> str:
            prefix = match.group(1)
            raw_id = match.group(2)
            identifier = self._id.sanitize_column(raw_id.strip('"'))
            if preferred_table_alias:
                ds = alias_to_dataset.get(preferred_table_alias)
                if ds and self._resolve_column_name_for_dataset(dataset_col_lookup.get(ds, set()), identifier):
                    return f'{prefix}{preferred_table_alias}."{identifier}"'
            owners = [ds for ds, cols in dataset_col_lookup.items() if identifier in cols]
            if len(owners) == 1:
                alias = dataset_aliases.get(owners[0])
                if alias: return f'{prefix}{alias}."{identifier}"'
            return match.group(0)
        return pattern.sub(_replace, metric_sql)

    def _dedupe_qualified_column_tokens(self, metric_sql: str) -> str:
        if not metric_sql: return metric_sql
        repaired = metric_sql
        repaired = re.sub(r'(\b\w+\.)"([A-Z_][A-Z0-9_]*)"\.\2\b', r'\1"\2"', repaired)
        repaired = re.sub(r'(\b\w+\.)([A-Z_][A-Z0-9_]*)\.\2\b', r'\1\2', repaired)
        return repaired

    def _rewrite_window_metric_expression(self, metric_sql: str, preferred_table_alias: Optional[str] = None) -> str:
        if not metric_sql or "OVER" not in metric_sql.upper(): return metric_sql
        ratio_pattern = re.compile(r'(?is)^\s*DIV0\s*\(\s*SUM\((?P<num>[^\)]+)\)\s*,\s*SUM\((?P<den>[^\)]+)\)\s+OVER\s*\([^\)]*\)\s*\)\s*$')
        match = ratio_pattern.match(metric_sql.strip())
        if match:
            num = match.group("num").strip()
            den = match.group("den").strip()
            if num.upper() == den.upper(): return self._build_safe_sum_sql(num, num)
            return "NULL"
        ytd_like = re.compile(r'(?is)^\s*(SUM|AVG|COUNT|MIN|MAX)\s*\([^\)]+\)\s+OVER\s*\(\s*PARTITION\s+BY\s+.+?\s+ORDER\s+BY\s+.+?\)\s*$')
        if ytd_like.match(metric_sql.strip()):
            if preferred_table_alias:
                refs = set(a.upper() for a in re.findall(r'(?i)\b(\w+)\s*\.', metric_sql.split("OVER", 1)[1] if "OVER" in metric_sql.upper() else ""))
                if refs and any(a != preferred_table_alias.upper() for a in refs): return "NULL"
            return metric_sql
        sply_like = re.compile(r'(?is)^\s*(LAG|LEAD)\s*\(\s*.+?\)\s+OVER\s*\(.*\)\s*$')
        if sply_like.match(metric_sql.strip()): return metric_sql
        return "NULL"

    def _pick_preferred_aggregate_column(self, metric_name: str, known_columns: set[str]) -> Optional[str]:
        """Only auto-resolves when excluding structural key/FK/date-suffixed
        columns leaves exactly one candidate — never by scoring keyword
        overlap with the metric's own name, which can silently substitute
        the wrong column."""
        if not known_columns: return None
        excluded_suffixes = (
            "_CK", "_ID", "_KEY", "_DATE",
            "_TYPE", "_STATUS", "_FLAG", "_NAME", "_CODE",
            "_CLASS", "_SEGMENT", "_DESC", "_DESCRIPTION", "_GROUP", "_CATEGORY",
        )
        candidates = [col for col in known_columns if not col.upper().endswith(excluded_suffixes)]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _get_cached_or_translate(self, dax: str, metric_name: str) -> Optional[str]:
        """
        Gets a translation from the prefetch cache or triggers a new translation.
        This is a simplified helper for the validation method.
        """
        # Check prefetch cache first
        if metric_name in self._openai_prefetch_sql_by_metric:
            return self._openai_prefetch_sql_by_metric[metric_name]

        # Check the JSON file cache as a fallback
        cache_file = '.llm_dax_cache.json'
        if not hasattr(self, '_llm_cache') and os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    self._llm_cache = json.load(f)
            except (IOError, json.JSONDecodeError):
                self._llm_cache = {}
        
        if hasattr(self, '_llm_cache'):
            # Search for a key that matches the metric name and dax
            for key, value in self._llm_cache.items():
                if key.startswith(f"{metric_name}|") and key.endswith(f"|{dax}"):
                    return value
            # Fallback for older cache format
            cache_key = f"{metric_name}|{dax}"
            if cache_key in self._llm_cache:
                return self._llm_cache[cache_key]

        logger.info(f"Metric '{metric_name}' not found in any cache. A full translation would be triggered here.")
        return None

    def validate_and_test_translation(self, dax: str, metric_name: str, expected_patterns: dict = None) -> dict:
        """
        Validate DAX translation without calling OpenAI again.
        Uses cached translations or existing translation logic.
        """
        result = {
            "metric_name": metric_name,
            "dax": dax,
            "translated_sql": None,
            "is_valid": False,
            "issues": [],
            "confidence": 0.0
        }
        
        # Get translation from cache
        sql = self._get_cached_or_translate(dax, metric_name)
        
        if not sql:
            result["issues"].append("Translation failed - no SQL generated or found in cache")
            return result
        
        result["translated_sql"] = sql
        
        # Validation rules
        sql_upper = sql.upper()
        
        # Rule 1: No SELECT, FROM, JOIN
        forbidden = ["SELECT", "FROM", "JOIN", "WITH", "SUBQUERY"]
        for f in forbidden:
            if f in sql_upper:
                result["issues"].append(f"Contains forbidden keyword: {f}")
        
        # Rule 2: No nested aggregates
        if re.search(r'(SUM|AVG|COUNT)\(.*(SUM|AVG|COUNT)\(', sql, re.IGNORECASE):
            result["issues"].append("Contains nested aggregates")
        
        # Rule 3: YTD must use MAX_DATE, not CURRENT_DATE
        if "YTD" in metric_name.upper() or "TOTALYTD" in dax.upper():
            if "CURRENT_DATE" in sql_upper:
                result["issues"].append("YTD measure uses CURRENT_DATE, should use MAX_DATE")
            elif "MAX_DATE" not in sql_upper:
                result["issues"].append("YTD measure missing MAX_DATE anchor")
        
        # Rule 4: Filters must use CASE WHEN (heuristic)
        if "CALCULATE" in dax.upper() and "CASE WHEN" not in sql_upper and "=" in dax:
            result["issues"].append("CALCULATE filter not converted to CASE WHEN")
        
        # Rule 5: No window functions
        if "OVER" in sql_upper or "PARTITION BY" in sql_upper:
            result["issues"].append("Contains window function")
        
        # Rule 6: DIVIDE must use COALESCE + NULLIF
        if "DIVIDE" in dax.upper() and ("COALESCE" not in sql_upper or "NULLIF" not in sql_upper):
            result["issues"].append("DIVIDE not using COALESCE/NULLIF pattern")
        
        result["is_valid"] = len(result["issues"]) == 0
        result["confidence"] = 0.9 if result["is_valid"] else 0.3
        
        return result
