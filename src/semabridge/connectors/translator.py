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

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class MetricExpressionTranslator:
    def __init__(
        self,
        identifier_sanitizer: Any = None,
        dialect: str = "snowflake",
        behavior: Any = None,
    ) -> None:
        self._id = identifier_sanitizer
        self.dialect = dialect
        self.behavior = behavior
        self._common_dax_translator = None
    def _sanitize_semantic_name(self, name: str) -> str:
        """Sanitize semantic name and ensure it does not start with a digit."""
        sanitized = self._id.sanitize_column(name)
        if sanitized and sanitized[0].isdigit():
            sanitized = f"_{sanitized}"
        return sanitized

    def _sanitize_sql_markdown(self, sql: str) -> str:
        if not sql:
            return ""
        try:
            from semabridge.converter.dax_engine import sanitize_llm_sql

            return sanitize_llm_sql(sql)
        except Exception:
            sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
            sql = re.sub(r"(?m)^.*\bVAR\b.*$", "", sql, flags=re.IGNORECASE)
            if re.match(r"^\s*SELECT\s+", sql, re.IGNORECASE):
                match = re.search(r"SELECT\s+(.*?)\s+FROM\b", sql, re.IGNORECASE | re.DOTALL)
                if match:
                    sql = match.group(1)
            return sql.strip()


    # Implementations lifted from SnowflakeEmitter. These use the emitter
    # delegate only for small utility helpers (sanitizers, logger, and
    # fallback resolvers) so the main translation surface is owned here.
    
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

        # For more complex patterns reuse emitter deterministic DAX translator
        if model is not None and getattr(model, "metrics", None):
            m_totalytd_metric_ref = re.match(r"(?i)^TOTALYTD\(\s*\[([^\]]+)\]\s*,\s*(?:'[^']+'\s*)?\[[^\]]+\]\s*\)$", expr)
            if m_totalytd_metric_ref:
                ref_name = m_totalytd_metric_ref.group(1).strip()
                metrics_by_name = {
                    str(getattr(m, "unique_name", "")).strip().casefold(): m
                    for m in list(model.metrics)
                    if getattr(m, "unique_name", None)
                }
                ref_metric = metrics_by_name.get(ref_name.casefold())
                if ref_metric and str(getattr(ref_metric, "dataset", "")).casefold() == str(metric.dataset).casefold():
                    ref_metric_name = self._sanitize_semantic_name(str(getattr(ref_metric, "unique_name", ref_name)))
                    year_col = self._resolve_year_partition_column(known_cols)
                    order_col = self._resolve_ytd_order_column(known_cols)
                    if year_col and order_col:
                        return (
                            f'SUM({table_alias}."{ref_metric_name}") OVER '
                            f'(PARTITION BY {table_alias}."{year_col}" '
                            f'ORDER BY {table_alias}."{order_col}")'
                        )

            try:
                from semabridge.converter.dax_translator import DAXTranslator

                translated = DAXTranslator().translate(
                    raw_expr,
                    table_alias,
                    metric.dataset,
                    metric_name=metric.unique_name,
                    metrics_context=list(model.metrics),
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
        skipped_metric_names: set[str]
    ) -> Optional[str]:
        dax_expression = (getattr(metric, "expression", None) or "").strip()
        if not dax_expression:
            return None

        candidate_expressions: list[str] = []

        try:
            from semabridge.converter.dax_rule_translator import (
                is_simple_metric,
                rule_based_translation,
            )
            rule_table_alias = table_alias.lower()
            owner_match = re.search(
                r"\b(?:SUM|AVERAGE|COUNT|MIN|MAX)\s*\(\s*'([^']+)'\s*\[",
                dax_expression,
                re.IGNORECASE,
            )
            if owner_match:
                owner_dataset = owner_match.group(1)
                rule_table_alias = (
                    dataset_aliases.get(owner_dataset)
                    or alias_by_raw.get(owner_dataset)
                    or alias_by_raw.get(owner_dataset.upper())
                    or alias_by_raw.get(self._id.sanitize_alias(owner_dataset))
                    or rule_table_alias
                )
            local_expr = rule_based_translation(
                dax_expression,
                rule_table_alias,
                metric_name=getattr(metric, "unique_name", "") or metric_name,
                dialect=self.dialect,
            )
            if local_expr:
                candidate_expressions.append(local_expr)
            elif is_simple_metric(dax_expression):
                local_expr = rule_based_translation(
                    dax_expression,
                    table_alias.lower(),
                    dialect=self.dialect
                )
                if local_expr:
                    candidate_expressions.append(local_expr)
        except Exception as ex:
            logger.debug(f"Local fallback unavailable for metric '{metric.unique_name}': {ex}")

        if self._is_common_llm_dax_enabled():
            schema_context = {ds_name: sorted(list(cols)) for ds_name, cols in dataset_col_lookup.items()}
            llm_result = self._translate_with_common_dax_translator(
                dax=dax_expression,
                table_alias=table_alias.lower(),
                dataset_name=metric.dataset,
                metric_name=metric.unique_name,
                schema_context=schema_context,
            )

            if (
                llm_result
                and llm_result.is_valid
                and llm_result.sql
                and not getattr(llm_result, "fallback_used", False)
            ):
                candidate_expressions.append(llm_result.sql)
            else:
                logger.debug(f"LLM fallback failed for metric '{metric.unique_name}': {getattr(llm_result, 'error', 'invalid translation')}")

        for candidate_sql in candidate_expressions:
            expr = self._sanitize_sql_markdown(candidate_sql)
            if not expr:
                continue
            
            # CRITICAL: Reject expressions containing DAX 'VAR' keyword leakage
            if re.search(r'\bVAR\b', expr.upper()):
                logger.warning("Metric '%s': rejecting LLM translation containing 'VAR' keyword: %s", metric_name, expr)
                continue

            expr = self._id.resolve_dot_notation(
                expr,
                alias_by_raw,
                sanitize_col_fn=self._id.sanitize_column,
            )
            expr = self._normalize_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
                preferred_table_alias=table_alias,
            )

            is_valid, _ = self._validate_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
            )
            if not is_valid:
                continue

            if not expr.strip():
                continue
            expr_upper = expr.upper().strip()
            if expr_upper == 'SUM(*)' or expr_upper.endswith('SUM(*)'):
                continue

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
            if unresolved_metric_refs:
                continue

            logger.info(f"Recovered metric '{metric.unique_name}' via fallback translation")
            return expr

        return None

    def _is_common_llm_dax_enabled(self) -> bool:
        behavior = getattr(self, "behavior", None)
        dbx_behavior = getattr(behavior, "databricks", None)
        if dbx_behavior is not None:
            return bool(getattr(dbx_behavior, "enable_llm_dax_translation", False))
        return os.getenv("ENABLE_LLM_DAX_TRANSLATION", "").lower() in {"1", "true", "yes", "on"}

    def _translate_with_common_dax_translator(
        self,
        *,
        dax: str,
        table_alias: str,
        dataset_name: str,
        metric_name: str,
        schema_context: Dict[str, List[str]],
    ) -> Any:
        try:
            from semabridge.converter.common_dax_translator import CommonDAXTranslator, SQLDialect

            if self._common_dax_translator is None:
                dbx_behavior = getattr(getattr(self, "behavior", None), "databricks", None)
                self._common_dax_translator = CommonDAXTranslator(
                    dialect=SQLDialect.SNOWFLAKE,
                    provider_order=list(getattr(dbx_behavior, "llm_dax_provider_order", []) or []),
                    timeout_seconds=int(getattr(dbx_behavior, "llm_dax_timeout_seconds", 20) or 20),
                    cache_enabled=bool(getattr(dbx_behavior, "llm_dax_cache_enabled", True)),
                    fallback_to_placeholder=bool(
                        getattr(dbx_behavior, "llm_dax_fallback_to_placeholder", True)
                    ),
                    placeholder_sql="0",
                )
            return self._common_dax_translator.translate(
                dax=dax,
                table_alias=table_alias,
                dataset_name=dataset_name,
                metric_name=metric_name,
                schema_context=schema_context,
            )
        except Exception as ex:
            logger.debug("Common LLM fallback unavailable for metric '%s': %s", metric_name, ex)
            return None

    def _validate_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}

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
                    return False, error
            logger.debug(f"No cross-table references found in metric '{metric_name}'")
            return True, None

        for table_alias, col_name in all_refs:
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                error = f"Alias '{table_alias}' not found in dataset mapping"
                logger.debug(f"Metric '{metric_name}': {error}")
                return False, error

            known_columns = dataset_col_lookup.get(dataset_name, set())
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

    def _normalize_metric_column_references(
        self,
        metric_sql: str,
        metric_name: str,
        dataset_col_lookup: Dict[str, set[str]],
        dataset_aliases: Dict[str, str],
        metric_names: Optional[set[str]] = None,
        preferred_table_alias: Optional[str] = None
    ) -> str:
        alias_to_dataset = {v: k for k, v in dataset_aliases.items()}
        normalized_sql = metric_sql

        # Build a secondary lookup: to_alias(ds_name) -> declared_alias
        # This remaps legacy aliases (e.g. 'l_date', 'measures') produced by
        # to_alias() during DAX translation to the declared emitter aliases
        # (e.g. 'COL_DATE', 'MEASURES') so sql_expressions stored at conversion
        # time are consistent with what the emitter declares in the TABLES clause.
        try:
            from semabridge.utils.naming import to_alias as _to_alias
            legacy_alias_remap: Dict[str, str] = {}
            for ds_name, declared_alias in dataset_aliases.items():
                legacy = _to_alias(ds_name)
                if legacy and legacy != declared_alias and legacy not in alias_to_dataset:
                    legacy_alias_remap[legacy] = declared_alias
            if legacy_alias_remap:
                for legacy, declared in legacy_alias_remap.items():
                    # Replace legacy.col and legacy."col" patterns
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

        quoted_pattern = r'(\w+)\.(["\'])([^"\']+)\2'
        for match in re.finditer(quoted_pattern, normalized_sql):
            table_alias = match.group(1)
            col_name = match.group(3)
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                continue
            sanitized_col_name = self._id.sanitize_column(col_name)
            known_columns = dataset_col_lookup.get(dataset_name, set())
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
                    new_ref = resolved_metric_ref
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                    continue

                fuzzy_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, fuzzy_metric_ref)
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

        unquoted_pattern = r'(\w+)\.([A-Za-z_][A-Za-z0-9_$]*)'
        for match in re.finditer(unquoted_pattern, normalized_sql):
            table_alias = match.group(1)
            col_name = match.group(2)
            dataset_name = alias_to_dataset.get(table_alias)
            if not dataset_name:
                continue
            sanitized_col_name = self._id.sanitize_column(col_name)
            known_columns = dataset_col_lookup.get(dataset_name, set())
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
                    new_ref = resolved_metric_ref
                    normalized_sql = normalized_sql.replace(old_ref, new_ref)
                    continue

                fuzzy_metric_ref = self._resolve_metric_reference_name(metric_names, sanitized_col_name, allow_fuzzy=True)
                if fuzzy_metric_ref:
                    old_ref = match.group(0)
                    normalized_sql = normalized_sql.replace(old_ref, fuzzy_metric_ref)
                    logger.debug(f"Normalized metric '{metric_name}': remapped {old_ref} → {fuzzy_metric_ref}")
                    continue
            elif resolved_col != sanitized_col_name:
                old_ref = match.group(0)
                new_ref = _format_metric_ref(table_alias, resolved_col)
                normalized_sql = normalized_sql.replace(old_ref, new_ref)
                continue
            else:
                # Column found with same name — ensure it's quoted
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

        normalized_sql = self._quote_bare_metric_references(normalized_sql, metric_names)
        normalized_sql = self._rewrite_metric_aggregate_wrappers(normalized_sql, metric_names)
        normalized_sql = self._repair_bare_aggregate_identifiers(normalized_sql, metric_name, dataset_col_lookup, dataset_aliases, metric_names, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._normalize_date_part_arguments(normalized_sql)
        normalized_sql = self._qualify_bare_partition_identifiers(normalized_sql, dataset_col_lookup, dataset_aliases, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._dedupe_qualified_column_tokens(normalized_sql)
        normalized_sql = self._rewrite_window_metric_expression(normalized_sql, preferred_table_alias=preferred_table_alias)
        normalized_sql = self._normalize_rolling_monthindex_max_predicates(normalized_sql)

        return normalized_sql

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

            if len(owner_candidates) > 1:
                fact_like = [candidate for candidate in owner_candidates if "FACT" in candidate[0].upper()]
                if len(fact_like) == 1:
                    owner_candidates = fact_like

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

    def _build_safe_sum_sql(self, expr_sql: str, identifier_hint: Optional[str] = None) -> str:
        is_flag = False
        if identifier_hint:
            hint = identifier_hint.strip().upper().replace('"', '')
            col_name = hint.split('.')[-1] if '.' in hint else hint
            flag_patterns = [r'^IS_', r'^HAS_', r'^WAS_', r'^DID_', r'^DOES_', r'_FLAG$', r'_FLG$', r'^DELETED$', r'_DELETED$']
            is_flag = any(re.search(p, col_name) for p in flag_patterns)

        if is_flag:
            return f"SUM(IFF({expr_sql} = 1 OR {expr_sql} = TRUE, 1, 0))"

        expr = expr_sql.strip()
        if re.match(r"(?is)^CASE\b.*\bEND(?:\s*::\s*FLOAT)?$", expr):
            expr = re.sub(r"(?is)\s*::\s*FLOAT\s*$", "", expr).strip()
            return f"SUM(CAST(({expr}) AS FLOAT))"
        if expr.upper().endswith("::FLOAT"):
            return f"SUM({expr})"
        return f"SUM({expr}::FLOAT)"

    # Internal Translation Helpers (Migrated from Emitter)
    
    def _resolve_column_name_for_dataset(self, known_columns: set[str], candidate: str) -> Optional[str]:
        if not known_columns: return None
        if candidate in known_columns: return candidate
        compact = candidate.replace("_", "")
        for col in known_columns:
            if col.replace("_", "") == compact: return col
        if candidate.startswith("TOTAL_"):
            base = candidate[len("TOTAL_"):]
            if base in known_columns: return base
        return None

    def _resolve_metric_reference_name(self, metric_names: Optional[set[str]], candidate: str, *, allow_fuzzy: bool = True) -> Optional[str]:
        if not metric_names: return None
        if candidate in metric_names: return candidate
        compact_candidate = candidate.replace("_", "")
        compact_matches = [m for m in metric_names if m.replace("_", "") == compact_candidate]
        if len(compact_matches) == 1: return compact_matches[0]
        if not allow_fuzzy: return None
        suffix_matches = [m for m in metric_names if m.endswith(f"_{candidate}") or m.startswith(f"{candidate}_")]
        if len(suffix_matches) == 1: return suffix_matches[0]
        contains_matches = [m for m in metric_names if candidate in m]
        if len(contains_matches) == 1: return contains_matches[0]
        return None

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
        if not known_columns: return None
        metric_token_set = set(t for t in self._id.sanitize_column(metric_name).split("_") if t)
        value_terms = {"AMOUNT", "REVENUE", "SALES", "SPEND", "VALUE", "COST", "PRICE", "TOTAL", "QTY", "QUANTITY", "UNITS", "USD"}
        categorical_terms = {"TYPE", "CATEGORY", "STATUS", "FLAG", "NAME", "DESC", "DESCRIPTION", "CODE", "GROUP", "CLASS", "SEGMENT"}
        excluded_suffixes = ("_CK", "_ID", "_KEY", "_DATE")
        scored: list[tuple[int, str]] = []
        for col in sorted(known_columns):
            tokens = [t for t in col.split("_") if t]
            token_set = set(tokens)
            score = 0
            overlap = len(metric_token_set.intersection(token_set))
            score += overlap * 10
            if token_set.intersection(value_terms): score += 8
            if token_set.intersection(categorical_terms): score -= 18
            if col.endswith(excluded_suffixes): score -= 20
            if "AMOUNT" in token_set: score += 4
            scored.append((score, col))
        if not scored: return None
        scored.sort(key=lambda item: (item[0], -len(item[1])), reverse=True)
        best_score = scored[0][0]
        if best_score < 1: return None
        best = [col for score, col in scored if score == best_score]
        return sorted(best, key=lambda c: (len(c), c))[0]

    @staticmethod
    def _resolve_year_partition_column(known_cols: set[str]) -> Optional[str]:
        if not known_cols: return None
        for candidate in ["YEAR", "CALENDAR_YEAR", "FISCAL_YEAR"]:
            if candidate in known_cols: return candidate
        return None

    @staticmethod
    def _resolve_ytd_order_column(known_cols: set[str]) -> Optional[str]:
        if not known_cols: return None
        for candidate in ["PERIOD", "MONTH", "MONTH_NUM", "YEARPERIOD", "DATE", "PRIMARY_DATE", "PRIMARYDATE"]:
            if candidate in known_cols: return candidate
        return None
