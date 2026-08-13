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
from semabridge.connectors.snowflake_metric_sql import normalize_snowflake_metric_sql
from semabridge.connectors.synonym_clause import synonyms_clause, comment_clause
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.utils.null_sentinel import is_null_cast_sql
from semabridge.connectors.type_safety_validator import (
    build_dataset_col_types,
    detect_date_numeric_type_mismatch,
)
from semabridge.converter.time_intelligence_shapes import (
    ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE,
    enrichment_flag_column_if_referenced,
    metrics_with_time_intelligence_shapes,
)

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
        dup_name_repo: Any = None,
        drop_ledger: Optional[DropLedger] = None,
    ):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer
        self.translator = translator
        self.config = config
        self.dup_name_repo = dup_name_repo
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()

    def _no_tier5_provider_configured(self) -> bool:
        """True when zero LLM providers are available for Tier-5 fallback
        (no Settings-page key, no env var for any provider in
        Tier5Config.provider_order) — see dax_translation/tier5/config.py.

        Best-effort/advisory only: any failure here (DB unavailable, import
        error, etc.) must never affect whether a metric drops, only the
        wording of the drop reason if it does — so this degrades to False
        (i.e. "don't claim no provider is configured") rather than raising.
        """
        try:
            from semabridge.dax_translation.tier5.config import Tier5Config
            return not Tier5Config.resolve().enabled_provider_order()
        except Exception as exc:
            logger.debug("Tier5 provider-availability check skipped (non-fatal): %s", exc)
            return False

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

        # Every metric flows through unconditionally -- identifier_sanitizer's
        # general character-replacement table (sanitize_alias/sanitize_column)
        # already turns '$' into 'DOL', the same way it turns '@' into 'AT' and
        # '#' into 'NUM' for every other metric name. A metric named e.g.
        # "Sales $" needs no special-case handling here to become SALES_DOL --
        # dropping it outright used to bypass that path for this one character.
        valid_metrics = list(model.metrics)
        metric_name_set = {self.identifier_sanitizer.sanitize_alias(m.unique_name) for m in valid_metrics}

        # Build metric name to table alias mapping dynamically as metrics are emitted
        metric_to_alias: Dict[str, str] = {}

        # Build set of fact-table aliases so metric prefix resolution prefers them
        fact_aliases: Set[str] = {
            alias
            for ds_name, alias in dataset_aliases.items()
            if getattr(dataset_by_name.get(ds_name), "is_fact", False)
        }

        # Declared column types (from the model's own SML/OSI Column
        # objects) — the type-safety backstop below uses this to catch a
        # date-producing expression combined with an integer/number column
        # before it ever reaches Snowflake's DDL. See
        # type_safety_validator.py's module docstring for the incident this
        # closes.
        dataset_col_types = build_dataset_col_types(getattr(model, "datasets", None))

        # Resolved ONCE for the whole model (not per metric -- see
        # time_intelligence_shapes.py's own note on why) so the DDL-
        # emission schema-validation failure handler below can recognize
        # "this unknown column is exactly the enrichment flag column this
        # metric's own resolved shape predicts" instead of hard-failing
        # every enrichment-created column dry-run can't see.
        metric_shapes = metrics_with_time_intelligence_shapes(valid_metrics)

        # Resolved ONCE for the whole model, same rationale as metric_shapes
        # above: lets the generic "could not be translated" drop message
        # further down distinguish "no LLM provider is configured at all"
        # (a one-setting fix: add a key under Settings -> LLM Providers)
        # from "a provider was tried and still couldn't translate this" —
        # today both collapse into one identical message with no way for a
        # user to tell which applies without reading server logs.
        no_llm_provider_configured = self._no_tier5_provider_configured()

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

        for metric in valid_metrics:
            raw_name = metric.unique_name
            sanitized_name = self.identifier_sanitizer.sanitize_alias(raw_name)
            logger.info(f"🔍 METRIC SANITIZATION: raw='{raw_name}' → sanitized='{sanitized_name}'")

        for metric in valid_metrics:
            if metric.unique_name in skipped_metric_names:
                continue
            alias = dataset_aliases.get(metric.dataset)
            if not alias:
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    f"Metric's dataset '{metric.dataset}' has no TABLES alias in "
                    "this semantic view, so the metric has no table to anchor "
                    "against.",
                    dataset=metric.dataset,
                )
                continue

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
                metric_to_alias=metric_to_alias,
                dataset_col_types=dataset_col_types,
                metric_shapes=metric_shapes,
                no_llm_provider_configured=no_llm_provider_configured,
            )

            if expr:
                expr = normalize_snowflake_metric_sql(expr)

            if expr and not self._is_scalar_metric_sql(expr):
                logger.warning(
                    "Skipping metric '%s': non-scalar SQL detected (SELECT/JOIN/CTE).",
                    metric.unique_name,
                )
                skipped_metric_names.add(metric.unique_name)
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    "Translated SQL contains a non-scalar construct (SELECT/JOIN/CTE), "
                    "which Snowflake's semantic-view METRICS clause does not support.",
                    dataset=getattr(metric, "dataset", None), detail=expr[:200] if expr else None,
                )
                continue

            if expr:
                type_mismatch_reason = detect_date_numeric_type_mismatch(expr, dataset_aliases, dataset_col_types)
                if type_mismatch_reason:
                    logger.warning(
                        "Skipping metric '%s': %s",
                        metric.unique_name, type_mismatch_reason,
                    )
                    skipped_metric_names.add(metric.unique_name)
                    self.drop_ledger.record(
                        "metric", metric.unique_name, DropStage.DDL_EMISSION,
                        type_mismatch_reason,
                        dataset=getattr(metric, "dataset", None), detail=expr[:200],
                    )
                    continue

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
                    f'{comment_clause(getattr(metric, "description", None))}'
                )
                metric_to_alias[self.identifier_sanitizer.sanitize_alias(metric.unique_name)] = metric_entity_alias
                emittable_metric_name_set.add(self.identifier_sanitizer.sanitize_alias(metric.unique_name))

        # Map every DDL-visible metric name back to its original unique_name +
        # dataset, so drops detected below (line removed between before/after
        # snapshots of metrics_lines) can be recorded against the same
        # identity used everywhere else in this file, not the sanitized DDL
        # alias. `expected_metrics` covers every metric that reached alias
        # resolution, including ones later added by the OSI fallback loop.
        metric_by_unique_name = {m.unique_name: m for m in valid_metrics}
        ddl_name_to_unique: Dict[str, str] = {}
        ddl_name_to_dataset: Dict[str, Optional[str]] = {}
        for _alias, _metric_name, _orig_name in expected_metrics:
            _safe = self.identifier_sanitizer.sanitize_column(_metric_name)
            ddl_name_to_unique[_safe] = _orig_name
            _owner = metric_by_unique_name.get(_orig_name)
            ddl_name_to_dataset[_safe] = getattr(_owner, "dataset", None) if _owner else None

        # Pruning and Fallbacks...
        pre_prune_lines = list(metrics_lines)
        metrics_lines = self._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
        self._record_removed_metric_lines(
            pre_prune_lines, metrics_lines, ddl_name_to_unique, ddl_name_to_dataset,
            "Metric's expression referenced another metric's name that was "
            "never itself emitted (dropped earlier, or removed in this same "
            "cascading pruning pass) — Snowflake's METRICS clause cannot "
            "reference an undefined metric.",
        )

        # OSI Fallback Loop
        if is_osi:
            metrics_lines = self._apply_osi_fallbacks(
                metrics_lines, expected_metrics, model, dataset_aliases, dataset_col_lookup,
                alias_by_raw, metric_name_set, all_physical_col_names, emittable_metric_name_set,
                skipped_metric_names
            )
            pre_dedup_lines = list(metrics_lines)
            metrics_lines = deduplicate_metrics_lines_osi(metrics_lines)
        else:
            pre_dedup_lines = list(metrics_lines)
            metrics_lines = deduplicate_metrics_lines(metrics_lines)
        self._record_removed_metric_lines(
            pre_dedup_lines, metrics_lines, ddl_name_to_unique, ddl_name_to_dataset,
            "Duplicate metric definition (same table alias + metric name as "
            "an earlier-defined line) — the earlier line was kept and this "
            "later duplicate was dropped.",
        )

        final_metrics_lines = []
        for i, line in enumerate(metrics_lines):
            comma = "," if i < len(metrics_lines) - 1 else ""
            final_metrics_lines.append(f"{line}{comma}")

        return final_metrics_lines

    @staticmethod
    def _names_in_lines(lines: List[str]) -> Set[str]:
        """Extract the declared metric name (the quoted part before ``AS``)
        from each METRICS-clause line — used to diff a before/after pass of
        ``metrics_lines`` and detect which metrics a pruning/dedup step
        silently removed."""
        names: Set[str] = set()
        for line in lines:
            m = re.search(r'\."([^"]+)"\s+AS\s+', line)
            if m:
                names.add(m.group(1))
        return names

    def _record_removed_metric_lines(
        self,
        before: List[str],
        after: List[str],
        ddl_name_to_unique: Dict[str, str],
        ddl_name_to_dataset: Dict[str, Optional[str]],
        reason: str,
    ) -> None:
        """Record a DropLedger entry for every metric name present in
        ``before`` but missing from ``after`` — the generic hook that lets
        any current or future line-removal step (pruning, dedup, ...) stay
        reconciliation-safe without each one re-implementing its own
        bookkeeping."""
        removed = self._names_in_lines(before) - self._names_in_lines(after)
        for name in removed:
            self.drop_ledger.record(
                "metric", ddl_name_to_unique.get(name, name), DropStage.DDL_EMISSION,
                reason, dataset=ddl_name_to_dataset.get(name),
            )

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
            " OVER ",          # window functions not allowed in METRICS clause
            " PARTITION BY ",  # redundant but explicit
            ";",
        )
        return not any(token in upper for token in forbidden)

    def _precomputed_column_name(self, source_dataset: str, source_column: str) -> str:
        return self.identifier_sanitizer.sanitize_column(f"{source_dataset}_{source_column}")

    def _dataset_has_column(
        self,
        dataset_name: str,
        column_name: str,
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> bool:
        wanted = self.identifier_sanitizer.sanitize_column(column_name).upper()
        return wanted in {str(c).upper() for c in dataset_col_lookup.get(dataset_name, set())}

    def _rewrite_cross_dataset_sql_refs_to_precomputed(
        self,
        sql_expr: str,
        active_dataset: str,
        active_alias: str,
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> str:
        """Replace reachable cross-dataset refs with enriched-view columns.

        Snowflake semantic-view metrics cannot freely reference unrelated
        entities from a metric anchored on a fact table. When the enriched view
        already projects SOURCE_COLUMN as SOURCE_COLUMN into the metric dataset,
        this rewrite keeps the metric scalar and single-entity.
        """
        if not sql_expr or not active_dataset or not active_alias:
            return sql_expr

        rewritten = sql_expr
        for source_dataset, source_alias in (dataset_aliases or {}).items():
            if str(source_dataset).casefold() == str(active_dataset).casefold():
                continue
            tokens = {
                self.identifier_sanitizer.sanitize_alias(source_dataset),
                source_alias,
                source_dataset,
            }
            for token in sorted({t for t in tokens if t}, key=len, reverse=True):
                quoted_pattern = re.compile(rf'\b{re.escape(token)}\."([^"]+)"', re.IGNORECASE)

                def replace_quoted(match: re.Match[str]) -> str:
                    source_col = match.group(1)
                    precomputed_col = self._precomputed_column_name(source_dataset, source_col)
                    if self._dataset_has_column(active_dataset, precomputed_col, dataset_col_lookup):
                        return f'{active_alias}."{precomputed_col}"'
                    return match.group(0)

                rewritten = quoted_pattern.sub(replace_quoted, rewritten)

                bare_pattern = re.compile(rf'\b{re.escape(token)}\.([A-Za-z_][A-Za-z0-9_]*)', re.IGNORECASE)

                def replace_bare(match: re.Match[str]) -> str:
                    source_col = match.group(1)
                    precomputed_col = self._precomputed_column_name(source_dataset, source_col)
                    if self._dataset_has_column(active_dataset, precomputed_col, dataset_col_lookup):
                        return f'{active_alias}."{precomputed_col}"'
                    return match.group(0)

                rewritten = bare_pattern.sub(replace_bare, rewritten)
        return rewritten

    def _rewrite_cross_dataset_dax_refs_to_precomputed(
        self,
        dax_expr: str,
        active_dataset: str,
        dataset_col_lookup: Dict[str, Set[str]],
    ) -> str:
        if not dax_expr or not active_dataset:
            return dax_expr

        dataset_names = sorted(dataset_col_lookup.keys(), key=len, reverse=True)

        def replace_ref(match: re.Match[str]) -> str:
            source_dataset = match.group(1).strip().strip("'\"")
            source_column = match.group(2).strip()
            resolved_source = next(
                (ds for ds in dataset_names if ds.casefold() == source_dataset.casefold()),
                source_dataset,
            )
            if resolved_source.casefold() == str(active_dataset).casefold():
                return match.group(0)
            precomputed_col = self._precomputed_column_name(resolved_source, source_column)
            if not self._dataset_has_column(active_dataset, precomputed_col, dataset_col_lookup):
                return match.group(0)
            return f"'{active_dataset}'[{precomputed_col}]"

        pattern = re.compile(r"[\'\"]?([^\'\"\[\]\(\),]+)[\'\"]?\s*\[\s*([^\]]+?)\s*\]")
        return pattern.sub(replace_ref, dax_expr)

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

    def _find_physical_owner_for_column(self, col_name: str, dataset_col_lookup: Dict[str, Set[str]]) -> Optional[str]:
        for ds, cols in dataset_col_lookup.items():
            if self._is_virtual_measures_table(ds, dataset_col_lookup):
                continue
            resolved = self.translator._resolve_column_name_for_dataset(cols, col_name)
            if resolved:
                return ds
        return None

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
        metric_to_alias: Optional[Dict[str, str]] = None,
        dataset_col_types: Optional[Dict[str, Dict[str, str]]] = None,
        metric_shapes: Optional[Dict[str, Any]] = None,
        no_llm_provider_configured: bool = False,
    ) -> Optional[str]:
        # A by-design-excluded metric (constant expression / string-producing
        # root, classified earlier in the pipeline — see dax_ast_parser.py's
        # dax_has_zero_data_dependencies/dax_root_is_string_producing) was
        # never a translation candidate to begin with. Without this check,
        # every fallback below (LLM, then basic DAX patterns) gets an
        # independent shot at "translating" its raw DAX expression, and a
        # naive literal-conversion fallback can succeed at turning something
        # like a DAX empty-string literal into a valid — but meaningless —
        # SQL empty-string literal, silently undoing the earlier exclusion.
        from semabridge.converter.dax_ast_parser import is_by_design_excluded
        if is_by_design_excluded(getattr(metric, "sync_failure_reason", None)):
            logger.debug(
                "Metric '%s': by-design excluded — skipping DDL emission "
                "rather than re-attempting translation.",
                metric.unique_name,
            )
            self.drop_ledger.record(
                "metric", metric.unique_name, DropStage.DDL_EMISSION,
                metric.sync_failure_reason or "By-design excluded from metric translation.",
                dataset=getattr(metric, "dataset", None),
                by_design=True,
            )
            return None

        if (metric.source_column and metric.aggregation and
            (not getattr(metric, "sql_expression", None) or self._should_use_direct_metric_aggregation(metric))):
            
            col_name = (self.identifier_sanitizer.sanitize_column(metric.source_column) if is_osi 
                        else self.schema_manager._resolve_physical_column_name(dataset_by_name.get(metric.dataset), metric.source_column))
            agg = metric.aggregation.value.upper()
            
            # Check for physical owner
            owners = [ds for ds, cols in dataset_col_lookup.items() 
                      if self.translator._resolve_column_name_for_dataset(cols, col_name)]
            
            if owners:
                preferred_owner = None
                if metric.dataset in owners and not self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                    preferred_owner = metric.dataset
                else:
                    # Filter out virtual measures tables from owners
                    non_virtual_owners = [o for o in owners if not self._is_virtual_measures_table(o, dataset_col_lookup)]
                    if len(non_virtual_owners) == 1:
                        preferred_owner = non_virtual_owners[0]
                    elif len(non_virtual_owners) > 1:
                        # Prefer fact table if available
                        fact_owners = [o for o in non_virtual_owners if dataset_aliases.get(o) in (fact_aliases or set())]
                        preferred_owner = fact_owners[0] if fact_owners else non_virtual_owners[0]
                
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
                        self.drop_ledger.record(
                            "metric", metric.unique_name, DropStage.DDL_EMISSION,
                            f"Dataset '{metric.dataset}' is a virtual Power BI measures table "
                            "with no physical columns in Snowflake.",
                            dataset=metric.dataset,
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

        if sql_expr and is_null_cast_sql(sql_expr):
            # A prior stage (or a previous run's persisted translation) already
            # gave up on this metric and stored the NULL-cast placeholder as
            # its sql_expression. Treat it exactly like "no sql_expression" —
            # fall through to a DAX-expression retry if one exists (that path
            # records its own success/failure), rather than validating and
            # emitting the placeholder as if it were real SQL. Only record
            # here when there's no DAX fallback to attempt, so this doesn't
            # double up with the DAX-expression branch's own record below.
            if not dax_expr:
                logger.warning(
                    "Metric '%s': stored sql_expression is a NULL-cast placeholder "
                    "from an earlier translation attempt that declined to translate, "
                    "and no DAX expression exists to retry. Skipping rather than "
                    "emitting it as if valid.",
                    metric.unique_name,
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DAX_TRANSLATION,
                    "A prior translation stage stored a NULL-cast placeholder instead of "
                    "real SQL for this metric's expression, and no DAX expression exists "
                    "to retry — treating as untranslated rather than emitting a dead "
                    "metric silently.",
                    dataset=getattr(metric, "dataset", None),
                )
                return None
            logger.warning(
                "Metric '%s': stored sql_expression is a NULL-cast placeholder from "
                "an earlier translation attempt that declined to translate. Falling "
                "back to its DAX expression instead of emitting the placeholder as "
                "if valid.",
                metric.unique_name,
            )
            sql_expr = None

        if self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
            logger.warning(f"Metric '{metric.unique_name}': virtual measures table, trying DAX fallback")
            # Fall through to DAX expression path
            dax_expr = getattr(metric, "expression", None)
            if dax_expr:
                # Continue to DAX translation
                sql_expr = None
            else:
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    f"Dataset '{metric.dataset}' is a virtual Power BI measures table with no "
                    "physical columns, and the metric has no DAX expression to fall back to.",
                    dataset=metric.dataset,
                )
                return None
        
        # If we have SQL expression, use it directly
        if sql_expr:
            # Guard: Snowflake's METRICS clause only accepts scalar expressions.
            # Subqueries (SELECT …) and CTEs (WITH …) are illegal and cause
            # "unexpected 'SELECT'" DDL compilation errors.  This can happen when
            # an older translation stored a subquery-based expression, or if a new
            # translation path regresses.  Skip the metric rather than emitting
            # invalid DDL; the caller will surface a sync_failure_reason.
            if "SELECT" in sql_expr.upper():
                logger.warning(
                    "Metric '%s': sql_expression contains a subquery (SELECT) which is "
                    "not allowed in Snowflake METRICS clause. Skipping to prevent DDL "
                    "failure. Expression (first 120 chars): %s",
                    metric.unique_name,
                    sql_expr[:120],
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    "SQL expression contains a subquery (SELECT), which Snowflake's "
                    "semantic-view METRICS clause does not support.",
                    dataset=getattr(metric, "dataset", None), detail=sql_expr[:200],
                )
                return None
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
                    self.drop_ledger.record(
                        "metric", metric.unique_name, DropStage.DDL_EMISSION,
                        "Could not resolve all virtual measures-table column references to a "
                        "real fact-table owner.",
                        dataset=getattr(metric, "dataset", None),
                    )
                    return None
            expr = sql_expr
            
            # ✅ NEW: Qualify cross-table references FIRST (before normalization)
            expr = self.translator._auto_qualify_cross_table_refs(expr, dataset_aliases)
            expr = self._rewrite_cross_dataset_sql_refs_to_precomputed(
                expr,
                metric.dataset,
                alias,
                dataset_aliases,
                dataset_col_lookup,
            )
            
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
                # Before treating this as a genuine failure: is the
                # "unknown" column actually the enrichment flag column
                # this metric's own resolved time-intelligence shape
                # predicts (e.g. IS_YTD)? dry-run's dataset_col_lookup is
                # always built from the model's declared (pre-enrichment)
                # columns only -- it can never see a column
                # _create_enriched_view only creates at real-deploy time,
                # so a translation this codebase's own enrichment
                # mechanism already knows how to satisfy must not be
                # reported as a hard DDL-emission failure. See
                # time_intelligence_shapes.py's enrichment_flag_column_
                # if_referenced.
                shape = (metric_shapes or {}).get(metric.unique_name)
                predicted_flag_col = enrichment_flag_column_if_referenced(shape, expr)
                if predicted_flag_col:
                    honest_note = (
                        f"Cannot verify in dry-run — resolved by live enrichment at deploy time "
                        f"(references '{predicted_flag_col}', a flag column "
                        f"_create_enriched_view creates on '{metric.dataset}' for this metric's "
                        f"time-intelligence shape; dry-run has no live connection to confirm it "
                        f"directly)."
                    )
                    # OSI metrics (is_osi=True) have no advisory_notes/
                    # advisory_categories fields at all -- that's an SML-
                    # only concept (see osi_to_sml.py's own producer for
                    # the unreachable-dimension advisory). hasattr guards
                    # this generically rather than branching on is_osi
                    # directly; either way, returning expr below (not
                    # None) is the part that actually matters for both.
                    if hasattr(metric, "advisory_notes") and honest_note not in metric.advisory_notes:
                        metric.advisory_notes = list(metric.advisory_notes) + [honest_note]
                        metric.advisory_categories = list(metric.advisory_categories) + [
                            ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE
                        ]
                    logger.info(
                        "Metric '%s': column '%s' not in dry-run's declared schema, but matches "
                        "this metric's predicted enrichment flag column — emitting as unverified "
                        "rather than dropping.",
                        metric.unique_name, predicted_flag_col,
                    )
                    return expr
                logger.warning(
                    "Metric '%s': skipping invalid SQL expression after normalization: %s",
                    metric.unique_name,
                    reason or "unknown reference error",
                )
                self.drop_ledger.record(
                    "metric", metric.unique_name, DropStage.DDL_EMISSION,
                    f"SQL expression references a column that could not be resolved after "
                    f"normalization: {reason or 'unknown reference error'}",
                    dataset=getattr(metric, "dataset", None), detail=expr[:200] if expr else None,
                )
                return None
            return expr
        
        # If we have DAX expression, try to translate it
        if dax_expr:
            active_dataset = metric.dataset
            active_alias = alias
            
            if self._is_virtual_measures_table(metric.dataset, dataset_col_lookup):
                cols_in_dax = re.findall(r"\[([^\]]+)\]", dax_expr)
                for c in cols_in_dax:
                    sanitized_c = self.identifier_sanitizer.sanitize_column(c)
                    owner = self._find_physical_owner_for_column(sanitized_c, dataset_col_lookup)
                    if owner:
                        active_dataset = owner
                        active_alias = dataset_aliases.get(owner) or alias
                        break

            rewritten_dax_expr = self._rewrite_cross_dataset_dax_refs_to_precomputed(
                dax_expr,
                active_dataset,
                dataset_col_lookup,
            )
            
            class MetricWrapper:
                def __init__(self, orig, dataset, expression):
                    self._orig = orig
                    self.dataset = dataset
                    self.expression = expression
                def __getattr__(self, name):
                    return getattr(self._orig, name)
                    
            wrapped_metric = MetricWrapper(metric, active_dataset, rewritten_dax_expr)

            # Try LLM fallback first when configured (OpenAI is preferred inside
            # the translator). This keeps complex Fabric DAX from being dropped
            # before provider-backed translation gets a chance.
            translated = self.translator._try_llm_metric_fallback_expression(
                metric=wrapped_metric,
                metric_name=metric_name,
                table_alias=active_alias,
                alias_by_raw=alias_by_raw,
                dataset_col_lookup=dataset_col_lookup,
                dataset_aliases=dataset_aliases,
                metric_name_set=metric_name_set,
                all_physical_col_names=all_physical_col_names,
                emittable_metric_name_set=emittable_metric_name_set,
                skipped_metric_names=skipped_metric_names,
                metric_to_alias=metric_to_alias,
                dataset_col_types=dataset_col_types,
            )
            if translated:
                # ✅ NEW: Qualify cross-table references in translated SQL
                translated = self.translator._auto_qualify_cross_table_refs(translated, dataset_aliases)
                translated = self._rewrite_cross_dataset_sql_refs_to_precomputed(
                    translated,
                    active_dataset,
                    active_alias,
                    dataset_aliases,
                    dataset_col_lookup,
                )
                return translated

            # Try basic DAX translation first (COUNTROWS, COUNTBLANK, etc.)
            translated = self.translator._try_basic_dax_metric_fallback_expression(
                wrapped_metric, active_alias, dataset_col_lookup, model=None, dataset_by_name=dataset_by_name
            )
            if translated:
                translated = self._rewrite_cross_dataset_sql_refs_to_precomputed(
                    translated,
                    active_dataset,
                    active_alias,
                    dataset_aliases,
                    dataset_col_lookup,
                )
                return translated

            # Translation failed — skip this metric rather than emitting invalid SQL
            logger.warning(
                "Could not translate metric '%s' with expression '%s'. "
                "Skipping metric to prevent DDL compilation failure.",
                metric.unique_name, dax_expr[:100]
            )
            reason = (
                "DAX expression could not be translated to SQL at DDL-emission time "
                "(deterministic and rule-based tiers were unsuccessful, and "
            )
            reason += (
                "no LLM provider is configured, so Tier-5 fallback was never "
                "attempted — add a provider key under Settings → LLM Providers "
                "(or the corresponding env var) to let this metric fall back to "
                "LLM translation)."
                if no_llm_provider_configured else
                "the configured LLM provider(s) were also unable to translate it)."
            )
            self.drop_ledger.record(
                "metric", metric.unique_name, DropStage.DAX_TRANSLATION, reason,
                dataset=getattr(metric, "dataset", None), detail=dax_expr[:200] if dax_expr else None,
            )
            return None

        self.drop_ledger.record(
            "metric", metric.unique_name, DropStage.DDL_EMISSION,
            "Metric has no source_column+aggregation, no sql_expression, and no DAX "
            "expression to translate — nothing to emit.",
            dataset=getattr(metric, "dataset", None),
        )
        return None

    def _apply_osi_fallbacks(
        self, metrics_lines: List[str], expected_metrics: List[Any], osi: Any, 
        dataset_aliases: Dict[str, str], dataset_col_lookup: Dict[str, Set[str]],
        alias_by_raw: Dict[str, str], metric_name_set: Set[str], 
        all_physical_col_names: Set[str], emittable_metric_name_set: Set[str],
        skipped_metric_names: Set[str]
    ) -> List[str]:
        """OSI-specific metric fallbacks - generate from OSI structure."""
        metrics_list = getattr(osi, "metrics", None) or getattr(osi, "measures", [])
        # Exact-name membership, not substring: `metric_name in line` would
        # false-positive whenever a sibling metric's disambiguated name
        # ("X_2") contains this metric's base name ("X") as a substring,
        # wrongly concluding X already has a line and skipping its fallback
        # attempt (X still has a DropLedger record from the primary attempt
        # in _generate_metric_expression, but is denied a chance to recover).
        already_emitted = self._names_in_lines(metrics_lines)
        for alias, metric_name, original_name in expected_metrics:
            if metric_name not in already_emitted:
                # Find the OSI measure
                for measure in metrics_list:
                    if measure.unique_name == original_name:
                        # Generate SQL from measure's source column
                        if measure.source_column and measure.aggregation:
                            agg_val = measure.aggregation.value if hasattr(measure.aggregation, "value") else str(measure.aggregation)
                            agg_val = agg_val.upper()
                            sql = f'{agg_val}({alias}."{measure.source_column}")'
                            metrics_lines.append(f'  {alias}."{metric_name}" AS {sql}')
                            already_emitted.add(metric_name)
                        break
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
                            # Strip nested/inner WITH SYNONYMS clauses to avoid Snowflake DDL syntax errors
                            clean_ref_expr = re.sub(
                                r"\s+WITH\s+SYNONYMS\s*=\s*\((?:[^()']|'(?:''|[^'])*')*\)",
                                "",
                                ref_expr,
                                flags=re.IGNORECASE
                            )
                            expanded_expr = re.sub(rf'"{re.escape(ref_name)}"', f'({clean_ref_expr})', expanded_expr)
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
