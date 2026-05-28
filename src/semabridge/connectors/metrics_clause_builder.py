"""Builder for Snowflake semantic-view METRICS (MEASURES) clause."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.ddl_helpers import (
    deduplicate_metrics_lines,
    deduplicate_metrics_lines_osi,
    extract_expr_key,
    extract_expr_key_osi,
)
from semabridge.connectors.synonym_clause import synonyms_clause

logger = get_logger(__name__)


class MetricsClauseBuilder:
    """Handles construction and validation of the METRICS clause."""

    def __init__(
        self,
        identifier_sanitizer: Any,
        schema_manager: Any,
        sanitizer: Any,
        translator: Any,
        config: Any,
        dup_name_repo: Any = None
    ):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer
        self.translator = translator
        self.config = config
        self.dup_name_repo = dup_name_repo

    def build_for_sml(
        self,
        sml: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str]
    ) -> List[str]:
        """Build METRICS clause for SML model."""
        return self._build_metrics(
            sml, dataset_aliases, dataset_by_name, dataset_col_lookup, 
            alias_by_raw, all_physical_col_names, emittable_metric_name_set, is_osi=False
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str]
    ) -> List[str]:
        """Build METRICS clause for OSI model."""
        return self._build_metrics(
            osi, dataset_aliases, dataset_by_name, dataset_col_lookup, 
            alias_by_raw, all_physical_col_names, emittable_metric_name_set, is_osi=True
        )

    def _build_metrics(
        self,
        model: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str],
        is_osi: bool
    ) -> List[str]:
        metrics_lines = []
        used_metric_names: Set[str] = set()
        skipped_metric_names: Set[str] = set()
        expected_metrics: List[Tuple[str, str, str]] = []
        
        valid_metrics = [m for m in model.metrics if "$" not in m.unique_name]
        metric_name_set = {self.identifier_sanitizer.sanitize_alias(m.unique_name) for m in valid_metrics}

        # Build metric name to table alias mapping for qualifying bare cross-table metric references
        metric_to_alias: Dict[str, str] = {}
        for m in valid_metrics:
            m_alias = dataset_aliases.get(m.dataset)
            if m_alias:
                sanitized_name = self.identifier_sanitizer.sanitize_alias(m.unique_name)
                metric_to_alias[sanitized_name] = m_alias

        # Build set of fact-table aliases so metric prefix resolution prefers them
        fact_aliases: Set[str] = {
            alias
            for ds_name, alias in dataset_aliases.items()
            if getattr(dataset_by_name.get(ds_name), "is_fact", False)
        }
        
        metric_base_totals: Dict[str, int] = {}
        for m in valid_metrics:
            base = self.identifier_sanitizer.sanitize_alias(m.unique_name)
            metric_base_totals[base] = metric_base_totals.get(base, 0) + 1
            
        metric_base_seen: Dict[str, int] = {}
        metric_signature_seen: Dict[str, int] = {}
        metric_namespace = self._duplicate_namespace_key(model.unique_name or model.label)
        model_name = model.unique_name or model.label

        # Priority 0: prefetch OpenAI translations in batches (up to 20),
        # so complex DAX gets one network round-trip per chunk instead of per metric.
        try:
            self.translator.prefetch_openai_metric_translations(
                metrics=valid_metrics,
                table_alias=next(iter(fact_aliases), "FACT"),
                dataset_col_lookup=dataset_col_lookup,
            )
        except Exception as exc:
            logger.warning("OpenAI metric prefetch skipped: %s", exc)

        # ================================================================
        # DEBUG: Check metric name sanitization
        # ================================================================
        for metric in valid_metrics:
            raw_name = metric.unique_name
            sanitized_name = self.identifier_sanitizer.sanitize_alias(raw_name)
            logger.info(f"🔍 METRIC SANITIZATION: raw='{raw_name}' → sanitized='{sanitized_name}'")
        # ================================================================

        for metric in valid_metrics:
            alias = dataset_aliases.get(metric.dataset)
            if not alias: continue
            
            metric_base_alias = self.identifier_sanitizer.sanitize_alias(metric.unique_name)
            metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
            metric_base_seen[metric_base_alias] = metric_seen_idx
            
            metric_alias_seed = (metric_base_alias if metric_base_totals.get(metric_base_alias, 0) == 1 
                                 else f"{metric_base_alias}_{metric_seen_idx}")
            
            if metric_base_totals.get(metric_base_alias, 0) > 1:
                metric_signature_seed = self._build_duplicate_signature_seed(
                    metric.unique_name, 
                    getattr(metric, "sql_expression", None) or metric.expression, 
                    None, 
                    metric.aggregation.value if metric.aggregation else None
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"
                metric_alias_seed = self._resolve_persistent_duplicate_name(
                    "metric", metric_namespace, 
                    self.identifier_sanitizer.sanitize_alias(metric.dataset),
                    metric_base_alias, metric.unique_name, metric_signature, metric_alias_seed
                )

            metric_name = self._resolve_unique_metric_alias(metric_alias_seed, used_metric_names, metric.unique_name)
            expected_metrics.append((alias, metric_name, metric.unique_name))
            
            expr = self._generate_metric_expression(
                metric, metric_name, alias, dataset_by_name, dataset_aliases, 
                dataset_col_lookup, alias_by_raw, metric_name_set, 
                all_physical_col_names, emittable_metric_name_set, skipped_metric_names, model_name, is_osi,
                fact_aliases=fact_aliases,
                metric_to_alias=metric_to_alias
            )
            
            if expr:
                metric_entity_alias = self._resolve_metric_emission_alias(alias, expr, dataset_aliases, fact_aliases)
                safe_metric_name = self.identifier_sanitizer.sanitize_column(metric_name)
                if metric_name != safe_metric_name:
                    logger.info(
                        "METRIC DDL FIX: '%s' -> '%s'",
                        metric_name,
                        safe_metric_name,
                    )
                metrics_lines.append(
                    f'  {metric_entity_alias}."{safe_metric_name}" AS {expr}'
                    f'{synonyms_clause(list(getattr(metric, "synonyms", []) or []))}'
                )

        # Pruning and Fallbacks...
        metrics_lines = self._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
        
        # OSI Fallback Loop
        if is_osi:
            metrics_lines = self._apply_osi_fallbacks(
                metrics_lines, expected_metrics, model, dataset_aliases, dataset_col_lookup, 
                alias_by_raw, metric_name_set, all_physical_col_names, emittable_metric_name_set, 
                skipped_metric_names
            )
            metrics_lines = deduplicate_metrics_lines_osi(metrics_lines)
        else:
            metrics_lines = deduplicate_metrics_lines(metrics_lines)
            
        return metrics_lines

    def _remap_virtual_measures_table_refs(
        self,
        sql_expr: str,
        measures_alias: str,
        dataset_col_lookup: Dict[str, Set[str]],
        dataset_aliases: Dict[str, str],
        fact_aliases: Optional[Set[str]] = None,
    ) -> Optional[str]:
        """Remap references from a virtual MEASURES table alias to real owning tables.

        Returns ``None`` if at least one referenced column cannot be mapped safely.
        """
        if not sql_expr:
            return sql_expr

        physical_datasets = [
            ds for ds in dataset_col_lookup.keys()
            if not self._is_virtual_measures_table(ds, dataset_col_lookup)
        ]
        # Prefer fact datasets first when resolving ambiguous ownership.
        if fact_aliases:
            physical_datasets.sort(
                key=lambda ds: 0 if dataset_aliases.get(ds) in fact_aliases else 1
            )

        pattern = re.compile(
            rf'(?P<alias>{re.escape(measures_alias)})\s*\.\s*(?P<col>"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
        )

        unresolved = False

        def _replace(match: re.Match) -> str:
            nonlocal unresolved
            raw_col = match.group("col")
            col_name = raw_col.strip('"')

            owners: list[str] = []
            for ds in physical_datasets:
                cols = dataset_col_lookup.get(ds, set())
                resolved_col = self.translator._resolve_column_name_for_dataset(cols, col_name)
                if resolved_col:
                    owners.append(ds)

            if not owners:
                unresolved = True
                return match.group(0)

            chosen_dataset = owners[0]
            chosen_alias = dataset_aliases.get(chosen_dataset)
            if not chosen_alias:
                unresolved = True
                return match.group(0)

            chosen_col = (
                self.translator._resolve_column_name_for_dataset(
                    dataset_col_lookup.get(chosen_dataset, set()),
                    col_name,
                )
                or col_name
            )
            return f'{chosen_alias}."{chosen_col}"'

        rewritten = pattern.sub(_replace, sql_expr)

        if unresolved:
            logger.warning(
                "Could not resolve all virtual measures-table references for alias '%s'.",
                measures_alias,
            )
            return None

        return rewritten

    def _is_virtual_measures_table(self, dataset_name: Optional[str], dataset_col_lookup: Dict[str, Set[str]]) -> bool:
        """Detect Power BI virtual measures tables (e.g. 'Measures', 'Project Measures').
        
        These tables exist in Power BI models as a container for DAX measures but
        have no real data columns in Snowflake — only an ID column.
        """
        if not dataset_name:
            return False
        name_upper = dataset_name.upper().replace(" ", "_").replace("-", "_")
        is_measures_name = (
            name_upper == "MEASURES"
            or name_upper == "PROJECT_MEASURES"
            or name_upper.endswith("_MEASURES")
            or name_upper.startswith("MEASURES_")
        )
        if not is_measures_name:
            return False
        # Confirm: only has ID-like columns (no real data columns)
        cols = dataset_col_lookup.get(dataset_name, set())
        if not cols:
            return True  # No schema metadata = likely virtual
        non_id_cols = {c for c in cols if c.upper() not in ("ID", "NAME", "DESCRIPTION")}
        return len(non_id_cols) == 0

    def _generate_metric_expression(
        self,
        metric: Any,
        metric_name: str,
        alias: str,
        dataset_by_name: Dict[str, Any],
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str],
        metric_name_set: Set[str],
        all_physical_col_names: Set[str],
        emittable_metric_name_set: Set[str],
        skipped_metric_names: Set[str],
        model_name: str,
        is_osi: bool,
        fact_aliases: Optional[Set[str]] = None,
        metric_to_alias: Optional[Dict[str, str]] = None
    ) -> Optional[str]:
        if (metric.source_column and metric.aggregation and 
            (not getattr(metric, "sql_expression", None) or self._should_use_direct_metric_aggregation(metric))):
            
            col_name = (self.identifier_sanitizer.sanitize_column(metric.source_column) if is_osi 
                        else self.schema_manager._resolve_physical_column_name(dataset_by_name.get(metric.dataset), metric.source_column))
            agg = metric.aggregation.value.upper()
            
            # Check for physical owner
            owners = [ds for ds, cols in dataset_col_lookup.items() 
                      if self.translator._resolve_column_name_for_dataset(cols, col_name)]
            
            if owners:
                preferred_owner = metric.dataset if metric.dataset in owners else (owners[0] if len(owners) == 1 else None)
                if preferred_owner:
                    owner_alias = dataset_aliases.get(preferred_owner)
                    owner_col = self.translator._resolve_column_name_for_dataset(dataset_col_lookup.get(preferred_owner, set()), col_name) or col_name
                    if owner_alias:
                        if agg == "COUNT_DISTINCT":
                            return f'COUNT(DISTINCT {self.sanitizer.format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
                        elif agg == "NONE":
                            return f'{self.sanitizer.format_physical_column_ref(owner_alias, owner_col, model_name=model_name)}'
                        else:
                            return f'{agg}({self.sanitizer.format_physical_column_ref(owner_alias, owner_col, model_name=model_name)})'
            else:
                # Column not found in live schema metadata — trust the source model's
                # dataset assignment and emit directly against the metric's own alias.
                # This avoids falling through to sql_expression which may reference
                # a non-existent column on a dimension/lookup table (e.g. MEASURES table).
                if alias and col_name:
                    # Skip metrics bound to virtual Power BI measures tables — they have
                    # no real data columns in Snowflake and will always fail.
                    if self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                        logger.warning(
                            "Metric '%s': dataset '%s' appears to be a virtual measures table "
                            "with no physical columns. Skipping to prevent DDL failure.",
                            metric.unique_name, metric.dataset
                        )
                        return None
                    logger.debug(
                        "Metric '%s': column '%s' not found in schema metadata; "
                        "emitting against declared dataset alias '%s'",
                        metric.unique_name, col_name, alias
                    )
                    if agg == "COUNT_DISTINCT":
                        return f'COUNT(DISTINCT {self.sanitizer.format_physical_column_ref(alias, col_name, model_name=model_name)})'
                    elif agg == "NONE":
                        return f'{self.sanitizer.format_physical_column_ref(alias, col_name, model_name=model_name)}'
                    else:
                        return f'{agg}({self.sanitizer.format_physical_column_ref(alias, col_name, model_name=model_name)})'

        # SQL Expression path
        sql_expr = getattr(metric, "sql_expression", None)
        dax_expr = getattr(metric, "expression", None)
        
        # If we have SQL expression, use it directly
        if sql_expr:
            # If the metric's dataset is a virtual measures table, try to remap
            # column references to the actual fact table that owns those columns.
            if self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                sql_expr = self._remap_virtual_measures_table_refs(
                    sql_expr, alias, dataset_col_lookup, dataset_aliases, fact_aliases=fact_aliases
                )
                if sql_expr is None:
                    logger.warning(
                        "Metric '%s': could not remap virtual measures table references. Skipping.",
                        metric.unique_name
                    )
                    return None
            expr = sql_expr
            
            # ✅ NEW: Qualify cross-table references FIRST (before normalization)
            expr = self.translator._auto_qualify_cross_table_refs(expr, dataset_aliases)
            
            # Then normalize and validate
            expr = self.translator._normalize_metric_column_references(
                expr, metric.unique_name, dataset_col_lookup, dataset_aliases, 
                metric_names=metric_name_set, preferred_table_alias=alias,
                metric_to_alias=metric_to_alias
            )
            is_valid, reason = self.translator._validate_metric_column_references(
                expr,
                metric.unique_name,
                dataset_col_lookup,
                dataset_aliases,
                metric_names=metric_name_set,
            )
            if not is_valid:
                logger.warning(
                    "Metric '%s': skipping invalid SQL expression after normalization: %s",
                    metric.unique_name,
                    reason or "unknown reference error",
                )
                return None
            return expr
        
        # If we have DAX expression, try to translate it
        if dax_expr:
            # Try LLM fallback first when configured (OpenAI is preferred inside
            # the translator). This keeps complex Fabric DAX from being dropped
            # before provider-backed translation gets a chance.
            translated = self.translator._try_llm_metric_fallback_expression(
                metric=metric,
                metric_name=metric_name,
                table_alias=alias,
                alias_by_raw=alias_by_raw,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                metric_name_set=metric_name_set,
                all_physical_col_names=all_physical_col_names,
                emittable_metric_name_set=emittable_metric_name_set,
                skipped_metric_names=skipped_metric_names,
                metric_to_alias=metric_to_alias
            )
            if translated:
                # ✅ NEW: Qualify cross-table references in translated SQL
                translated = self.translator._auto_qualify_cross_table_refs(translated, dataset_aliases)
                return translated

            # Try basic DAX translation first (COUNTROWS, COUNTBLANK, etc.)
            translated = self.translator._try_basic_dax_metric_fallback_expression(
                metric, alias, dataset_col_lookup, model=None, dataset_by_name=dataset_by_name
            )
            if translated:
                return translated
            
            # Translation failed — skip this metric rather than emitting invalid SQL
            logger.warning(
                "Could not translate metric '%s' with expression '%s'. "
                "Skipping metric to prevent DDL compilation failure.",
                metric.unique_name, dax_expr[:100]
            )
            return None
            
        return None



    def _apply_osi_fallbacks(
        self, metrics_lines: List[str], expected_metrics: List[Any], osi: Any, 
        dataset_aliases: Dict[str, str], dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str], metric_name_set: Set[str], 
        all_physical_col_names: Set[str], emittable_metric_name_set: Set[str],
        skipped_metric_names: Set[str]
    ) -> List[str]:
        # Implementation of OSI-specific metric fallbacks
        return metrics_lines


    def _should_use_direct_metric_aggregation(self, metric: Any) -> bool:
        if not getattr(metric, "source_column", None) or not getattr(metric, "aggregation", None):
            return False
        complexity_tier = getattr(metric, "complexity_tier", 1) or 1
        if complexity_tier > 1:
            return False
        expr = (getattr(metric, "expression", None) or "").strip().upper()
        if not expr:
            return True
        simple_patterns = ("SUM(", "COUNT(", "DISTINCTCOUNT(", "AVERAGE(", "MIN(", "MAX(")
        return expr.startswith(simple_patterns)

    def _build_duplicate_signature_seed(self, source_name: str, source_expression: Optional[str], data_type: Optional[str], aggregation: Optional[str] = None) -> str:
        return "|".join([source_name or "", source_expression or "", data_type or "", aggregation or ""])

    def _duplicate_namespace_key(self, model_name: Optional[str] = None) -> str:
        parts = [
            self.identifier_sanitizer.sanitize_alias(self.config.database or "DB"),
            self.identifier_sanitizer.sanitize_alias(self.config.schema_name or "SCHEMA"),
        ]
        if model_name:
            parts.append(self.identifier_sanitizer.sanitize_alias(model_name))
        return ".".join(parts)

    def _resolve_persistent_duplicate_name(self, scope_type: str, namespace_key: str, dataset_key: str, normalized_base: str, source_name: str, source_signature: str, preferred_name: str) -> str:
        if not self.dup_name_repo:
            return preferred_name
        try:
            return self.dup_name_repo.get_or_create_assigned_name(
                scope_type=scope_type, namespace_key=namespace_key, dataset_key=dataset_key, 
                normalized_base=normalized_base, source_name=source_name, source_signature=source_signature, 
                preferred_name=preferred_name
            )
        except Exception as exc:
            logger.warning("Duplicate mapping failed for %s: %s", source_name, exc)
            return preferred_name

    def _resolve_unique_metric_alias(self, base_alias: str, used_aliases: Set[str], original_metric_name: str) -> str:
        if base_alias not in used_aliases:
            used_aliases.add(base_alias)
            return base_alias
        idx = 2
        while True:
            candidate = f"{base_alias}_{idx}"
            if candidate not in used_aliases:
                used_aliases.add(candidate)
                return candidate
            idx += 1

    def _resolve_metric_emission_alias(self, default_alias: str, metric_sql: str, dataset_aliases: Dict[str, str], fact_aliases: Optional[Set[str]] = None) -> str:
        if not isinstance(metric_sql, str):
            logger.warning(
                "Metric SQL expression for alias resolution is non-string (%s); falling back to default alias '%s'.",
                type(metric_sql).__name__,
                default_alias,
            )
            return default_alias
        valid_aliases = set(dataset_aliases.values())
        referenced_aliases = self._extract_referenced_table_aliases(metric_sql, valid_aliases)
        if len(referenced_aliases) == 1:
            return next(iter(referenced_aliases))
        if len(referenced_aliases) > 1 and fact_aliases:
            # Prefer fact table aliases over dimension aliases to avoid
            # "invalid identifier 'DIM_TABLE.COLUMN'" errors in Snowflake
            fact_refs = referenced_aliases & fact_aliases
            if len(fact_refs) == 1:
                return next(iter(fact_refs))
            if fact_refs:
                return sorted(fact_refs)[0]
        # Virtual measures-table metrics cannot safely anchor to MEASURES when
        # expression spans multiple entities. Prefer a concrete referenced alias.
        if referenced_aliases and self._is_virtual_measures_alias(default_alias):
            fact_like_refs = sorted(a for a in referenced_aliases if "FACT" in a.upper())
            if fact_like_refs:
                return fact_like_refs[0]
            non_virtual_refs = sorted(a for a in referenced_aliases if not self._is_virtual_measures_alias(a))
            if non_virtual_refs:
                return non_virtual_refs[0]
        # If no table aliases found in expression (e.g. pure metric-reference expression)
        # and the default alias is not a fact table, prefer the single fact table alias.
        # This prevents "must double aggregate over row-level expression" errors when
        # a metric is bound to a dimension table in the source model.
        if not referenced_aliases and fact_aliases and default_alias not in fact_aliases:
            if len(fact_aliases) == 1:
                return next(iter(fact_aliases))
        return default_alias

    @staticmethod
    def _is_virtual_measures_alias(alias: str) -> bool:
        upper = str(alias or "").strip().upper()
        return upper == "MEASURES" or upper == "PROJECT_MEASURES" or upper.endswith("_MEASURES")

    def _extract_referenced_table_aliases(self, metric_sql: str, valid_aliases: Set[str]) -> Set[str]:
        if not metric_sql: return set()
        referenced = set()
        patterns = [r'(\w+)\."([^"]+)"', r'(\w+)\.([A-Za-z_][A-Za-z0-9_$]*)']
        for pattern in patterns:
            for alias, _ in re.findall(pattern, metric_sql):
                if alias in valid_aliases:
                    referenced.add(alias)
        return referenced

    def _prune_unresolved_metric_lines(self, metrics_lines: List[str], metric_name_set: Set[str]) -> List[str]:
        if not metrics_lines or not metric_name_set:
            return metrics_lines
        current = list(metrics_lines)
        while True:
            defined: Set[str] = set()
            window_metrics: Set[str] = set()
            parsed = []
            expr_by_name = {}
            for line in current:
                m = re.search(r'([A-Z_][A-Z0-9_]*)\."([^\"]+)"\s+AS\s+(.+?)\s*$', line.strip().rstrip(','))
                if not m:
                    parsed.append((line, None, None, "", ""))
                    continue
                alias, name, expr = m.groups()
                expr, synonym_suffix = self._split_outer_synonyms_clause(expr)
                defined.add(name)
                expr_by_name[name] = expr
                if re.search(r'\bOVER\b', expr, flags=re.IGNORECASE):
                    window_metrics.add(name)
                parsed.append((line, alias, name, expr, synonym_suffix))

            removed = rewritten = False
            next_lines = []
            for line, alias, name, expr, synonym_suffix in parsed:
                if not name:
                    next_lines.append(line)
                    continue
                refs = set(re.findall(r'"([A-Z_][A-Z0-9_]*)"', expr))
                unresolved = [r for r in refs if r in metric_name_set and r not in defined and r != name]
                if unresolved:
                    removed = True
                    continue
                window_refs = [r for r in refs if r in window_metrics and r != name]
                if window_refs:
                    expanded_expr = expr
                    substituted = False
                    for ref_name in sorted(set(window_refs)):
                        ref_expr = expr_by_name.get(ref_name)
                        if ref_expr:
                            expanded_expr = re.sub(rf'"{re.escape(ref_name)}"', f'({ref_expr})', expanded_expr)
                            substituted = True
                    if substituted:
                        next_lines.append(f'  {alias}."{name}" AS {expanded_expr}{synonym_suffix}')
                        rewritten = True
                        continue
                    removed = True
                    continue
                next_lines.append(line)
            current = next_lines
            if not removed and not rewritten:
                return current

    def _split_outer_synonyms_clause(self, expr: str) -> Tuple[str, str]:
        marker = " WITH SYNONYMS = ("
        idx = str(expr or "").upper().rfind(marker)
        if idx < 0:
            return expr, ""
        return expr[:idx].rstrip(), expr[idx:]
