"""Semantic view DDL orchestration for Snowflake emission."""

from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.intermediate.models import OSIDataset, OSIModel
from semabridge.sml.models import SMLDataset, SMLModel
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.logger import get_logger
from semabridge.connectors.ddl_helpers import (
    fix_global_sums,
    deduplicate_metrics_lines,
    deduplicate_metrics_lines_osi,
    extract_expr_key,
    extract_expr_key_osi,
    extract_referenced_table_aliases,
)
from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
from semabridge.connectors.history_snapshot_orchestrator import HistorySnapshotOrchestrator
from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.ddl_validator import validate_semantic_ddl
from semabridge.utils.null_sentinel import is_null_cast_sql
from semabridge.core.exceptions import ConnectorError
from semabridge.connectors.helper_columns import is_helper_column, collect_helper_columns_for_model

logger = get_logger(__name__)



def _debug_ddl_path(model) -> Path:
    """Per-view debug DDL file, so parallel models in one batch don't
    overwrite each other's debug output."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(getattr(model, "unique_name", "") or "")).strip("_")
    filename = f"debug_generated_sql_{name}.sql" if name else "debug_generated_sql.sql"
    return Path("output/debug") / filename

class SemanticViewBuilder:
    """Facade for semantic-view DDL orchestration.

    Delegates specific clause generation to dedicated builder components.
    """

    def __init__(
        self,
        *,
        config: Any,
        behavior: Any,
        identifier_sanitizer: Any,
        live_schema_metadata: dict[str, set[str]],
        dup_name_repo: Any = None,
        schema_manager: Any = None,
        translator: Any = None,
        drop_ledger: Optional[DropLedger] = None,
        enriched_view_mapping: Optional[Dict[str, str]] = None,
        failed_enrichment_datasets: Optional[Set[str]] = None,
    ) -> None:
        self.config = config
        self.behavior = behavior
        self.identifier_sanitizer = identifier_sanitizer
        self.live_schema_metadata = live_schema_metadata
        self.enriched_view_mapping: Dict[str, str] = (
            enriched_view_mapping if enriched_view_mapping is not None else {}
        )
        self.failed_enrichment_datasets: Set[str] = (
            failed_enrichment_datasets if failed_enrichment_datasets is not None else set()
        )
        self.schema_manager = schema_manager
        self.dup_name_repo = dup_name_repo
        self.translator = translator
        # Set by the deploying caller before generate_ddls()/generate_ddls_from_osi()
        # when a live Snowflake cursor is available. None (the default, e.g. for
        # DDL-preview-only callers with no live connection) means anchor values
        # that require a live lookup (see TablesClauseBuilder) are skipped rather
        # than embedded as an unsupported subquery.
        self.cursor: Any = None
        # Populated after generate_ddls() — maps dataset_name → [phys_col, ...]
        # for columns present in the source model but absent from the Snowflake
        # physical schema.  Callers can surface these as warnings/conflicts.
        self.missing_dims: Dict[str, List[str]] = {}
        # Shared across every sub-builder so all drop reasons (schema
        # mismatch, DDL-emission skips, unresolved references, dropped
        # relationships) land in one place — see core/drop_ledger.py.
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()

        # Modular components
        self.sanitizer = SemanticDDLSanitizer(identifier_sanitizer, drop_ledger=self.drop_ledger)
        self.snapshot_orchestrator = HistorySnapshotOrchestrator(identifier_sanitizer, schema_manager, config)
        self.relationships_builder = RelationshipsClauseBuilder(
            identifier_sanitizer, schema_manager, self.sanitizer, drop_ledger=self.drop_ledger
        )
        self.dimensions_builder = DimensionsClauseBuilder(
            identifier_sanitizer, schema_manager, self.sanitizer, translator, behavior,
            drop_ledger=self.drop_ledger,
        )
        self.no_populated_date_link_datasets: Set[str] = set()
        self.metrics_builder = MetricsClauseBuilder(
            identifier_sanitizer, schema_manager, self.sanitizer, translator, config, dup_name_repo,
            drop_ledger=self.drop_ledger,
            enriched_view_mapping=self.enriched_view_mapping,
            no_populated_date_link_datasets=self.no_populated_date_link_datasets,
            failed_enrichment_datasets=self.failed_enrichment_datasets,
            live_schema_metadata=self.live_schema_metadata,
        )

    def _precompute_suggestions(self, model: Any) -> dict[str, list[str]]:
        """
        Analyze model and suggest pre-computed columns for cross-table references.
        This runs automatically and tells you what columns to add to fact table.
        NO HARDCODING!
        """
        # Always ensure metrics are re-anchored to the correct fact tables before computing precompute suggestions
        if hasattr(self, "_reanchor_disconnected_fact_metrics"):
            self._reanchor_disconnected_fact_metrics(model)

        suggestions: dict[str, list[str]] = {}
        details: list[dict[str, str]] = []
        dataset_names = {
            str(getattr(ds, "unique_name", "")).strip().casefold(): str(getattr(ds, "unique_name", "")).strip()
            for ds in getattr(model, "datasets", []) or []
            if getattr(ds, "unique_name", None)
        }

        def resolve_dataset(name: str) -> str:
            clean = str(name or "").strip().strip("'\"")
            return dataset_names.get(clean.casefold(), clean)

        # Measure references (Table[Measure]) aren't columns to pre-compute.
        metric_names = {
            str(getattr(m, "unique_name", "") or "").strip().casefold()
            for m in getattr(model, "metrics", []) or []
        }
        columns_by_dataset = {
            str(getattr(ds, "unique_name", "")).strip().casefold(): {
                str(getattr(c, "unique_name", "") or "").strip().casefold()
                for c in getattr(ds, "columns", []) or []
            }
            for ds in getattr(model, "datasets", []) or []
        }

        def precomputed_name(source_table: str, column: str) -> str:
            return self.identifier_sanitizer.sanitize_column(f"{source_table}_{column}")

        def add_suggestion(target_dataset: str, source_table: str, column: str) -> None:
            target_dataset = resolve_dataset(target_dataset)
            source_table = resolve_dataset(source_table)
            column = str(column or "").strip()
            if not target_dataset or not source_table or not column:
                return
            if target_dataset.casefold() == source_table.casefold():
                return
            source_columns = columns_by_dataset.get(source_table.casefold())
            if source_columns is None:
                return
            if column.casefold() in metric_names and column.casefold() not in source_columns:
                return
            col_alias = precomputed_name(source_table, column)
            suggestions.setdefault(target_dataset, [])
            if col_alias not in suggestions[target_dataset]:
                suggestions[target_dataset].append(col_alias)
            key = (target_dataset.casefold(), source_table.casefold(), column.casefold())
            existing = {
                (d["target_dataset"].casefold(), d["source_dataset"].casefold(), d["source_column"].casefold())
                for d in details
            }
            if key not in existing:
                details.append(
                    {
                        "target_dataset": target_dataset,
                        "source_dataset": source_table,
                        "source_column": column,
                        "precomputed_column": col_alias,
                    }
                )

        # Build metric dependency graph to propagate lookup column requirements transitively.
        metric_by_norm: dict[str, Any] = {}
        for m in getattr(model, "metrics", []) or []:
            name = getattr(m, "unique_name", "") or ""
            if name:
                metric_by_norm[name.casefold()] = m
                clean_name = re.sub(r'[^a-zA-Z0-9]', '', name).casefold()
                metric_by_norm[clean_name] = m

        metric_direct_lookups: dict[str, set[tuple[str, str]]] = {}
        metric_dependencies: dict[str, set[str]] = {}

        for m in getattr(model, "metrics", []) or []:
            m_key = getattr(m, "unique_name", "")
            dax = getattr(m, 'expression', '') or ''
            lookups: set[tuple[str, str]] = set()
            deps: set[str] = set()

            # Detect RELATED('Table'[Column]) and RELATED(Table[Column]) patterns.
            related_pattern = r'RELATED\(\s*[\'"]?([^\'"\[\]]+)[\'"]?\s*\[\s*([^\]]+?)\s*\]\s*\)'
            for table, column in re.findall(related_pattern, dax, re.IGNORECASE):
                lookups.add((table.strip(), column.strip()))

            # Detect direct 'Table'[Column] references (cross-table).
            direct_pattern = r'[\'"]?([^\'"\[\]\(\),]+)[\'"]?\s*\[\s*([^\]]+?)\s*\]'
            for table, column in re.findall(direct_pattern, dax, re.IGNORECASE):
                t_clean = table.strip()
                c_clean = column.strip()
                if not t_clean or not c_clean:
                    continue
                if c_clean.casefold() in metric_by_norm or re.sub(r'[^a-zA-Z0-9]', '', c_clean).casefold() in metric_by_norm:
                    dep_m = metric_by_norm.get(c_clean.casefold()) or metric_by_norm.get(re.sub(r'[^a-zA-Z0-9]', '', c_clean).casefold())
                    if dep_m and getattr(dep_m, "unique_name", "") != m_key:
                        deps.add(getattr(dep_m, "unique_name", ""))
                else:
                    lookups.add((t_clean, c_clean))

            # Detect bare [Identifier] references
            for m_bare in re.findall(r'\[([^\]]+)\]', dax):
                b_clean = m_bare.strip()
                if b_clean.casefold() in metric_by_norm or re.sub(r'[^a-zA-Z0-9]', '', b_clean).casefold() in metric_by_norm:
                    dep_m = metric_by_norm.get(b_clean.casefold()) or metric_by_norm.get(re.sub(r'[^a-zA-Z0-9]', '', b_clean).casefold())
                    if dep_m and getattr(dep_m, "unique_name", "") != m_key:
                        deps.add(getattr(dep_m, "unique_name", ""))

            metric_direct_lookups[m_key] = lookups
            metric_dependencies[m_key] = deps

        # Transitive propagation: if metric M depends on metric M', M also needs M''s lookup columns
        all_metric_lookups = {k: set(v) for k, v in metric_direct_lookups.items()}
        changed = True
        while changed:
            changed = False
            for m_key, deps in metric_dependencies.items():
                cur_len = len(all_metric_lookups[m_key])
                for dep in deps:
                    all_metric_lookups[m_key].update(all_metric_lookups.get(dep, set()))
                if len(all_metric_lookups[m_key]) > cur_len:
                    changed = True

        # Project lookup column into enriched view of every fact whose metrics use it
        for metric in getattr(model, "metrics", []) or []:
            if getattr(metric, "is_placeholder", False) or is_null_cast_sql(getattr(metric, "sql_expression", None)):
                continue
            m_key = getattr(metric, "unique_name", "")
            metric_dataset = getattr(metric, "dataset", "") or ""
            if not metric_dataset:
                continue
            for table, column in all_metric_lookups.get(m_key, set()):
                add_suggestion(metric_dataset, table, column)

        self._precompute_details = details
        
        if suggestions:
            logger.debug(
                "Pre-compute suggestions for cross-table references: %s",
                "; ".join(f"{table}: {', '.join(cols)}" for table, cols in suggestions.items()),
            )
        
        return suggestions

    def get_precompute_details(self) -> list[dict[str, str]]:
        """Return rich precompute suggestions from the latest analysis run."""
        return list(getattr(self, "_precompute_details", []) or [])

    def _reanchor_disconnected_fact_metrics(self, model: Any) -> None:
        """Re-anchor metrics in multi-fact models placed on the wrong fact table.

        Rule: A metric is placed on a table that reaches every table its expression
        references via the model's many-to-one relationships. If none can, drop it with a reason.
        No hardcoded table or metric names!
        """
        metrics = getattr(model, "metrics", []) or []
        datasets = {getattr(d, "unique_name", None): d for d in getattr(model, "datasets", []) or [] if getattr(d, "unique_name", None)}
        if not metrics or not datasets:
            return

        def _clean_id(s: Any) -> str:
            return re.sub(r'[^a-zA-Z0-9]', '', str(s or '')).upper()

        # 1. Build reachability graph (many-to-one transitive closure)
        reach: dict[str, set[str]] = {ds_name: {ds_name} for ds_name in datasets}
        for rel in getattr(model, "relationships", []):
            c_val = getattr(getattr(rel, "cardinality", ""), "value", getattr(rel, "cardinality", ""))
            c_clean = str(c_val or "").lower().split(".")[-1].replace("_", "-")
            if c_clean in ("many-to-many", "m:m", "manytomany"):
                continue  # Many-to-many cannot provide reachability in semantic view
            f = getattr(rel, "from_dataset", None)
            t = getattr(rel, "to_dataset", None)
            if c_clean in ("one-to-many", "1:m", "onetomany", "1m"):
                f, t = t, f
            if f in reach and t in datasets:
                reach[f].add(t)

        changed = True
        while changed:
            changed = False
            for f, targets in reach.items():
                new_targets = set(targets)
                for t in list(targets):
                    new_targets.update(reach.get(t, set()))
                if len(new_targets) > len(targets):
                    reach[f] = new_targets
                    changed = True

        # 2. Map columns to datasets
        col_to_ds: dict[str, set[str]] = {}
        ds_cols: dict[str, set[str]] = {}
        for ds_name, ds in datasets.items():
            ds_cols[ds_name] = {_clean_id(c.unique_name) for c in getattr(ds, "columns", []) or []}
            for c in getattr(ds, "columns", []) or []:
                c_norm = _clean_id(c.unique_name)
                col_to_ds.setdefault(c_norm, set()).add(ds_name)

        metric_map = {_clean_id(m.unique_name): m for m in metrics}

        # 3. Iteratively resolve and re-anchor metrics
        for _ in range(3):
            for m in metrics:
                curr_ds = getattr(m, "dataset", None)
                m_name = getattr(m, "unique_name", "") or ""
                dax_expr = getattr(m, "expression", "") or ""
                sql_expr = getattr(m, "sql_expression", "") or ""
                ref_tables: set[str] = set()

                # Check explicit Table[ColOrMeasure] in DAX
                for t_match, col_match in re.findall(r"'?([a-zA-Z0-9_ ]+)'?\[([a-zA-Z0-9_ ]+)\]", dax_expr):
                    t_clean = _clean_id(t_match)
                    col_clean = _clean_id(col_match)
                    if col_clean in metric_map:
                        target_m = metric_map[col_clean]
                        if target_m.dataset:
                            ref_tables.add(target_m.dataset)
                    else:
                        for ds_name in datasets:
                            if _clean_id(ds_name) == t_clean:
                                ref_tables.add(ds_name)

                # Check explicit table.col in SQL
                for t_match, col_match in re.findall(r'([a-zA-Z0-9_]+)\."?([a-zA-Z0-9_]+)"?', sql_expr):
                    t_clean = _clean_id(t_match)
                    col_clean = _clean_id(col_match)
                    if col_clean in metric_map:
                        target_m = metric_map[col_clean]
                        if target_m.dataset:
                            ref_tables.add(target_m.dataset)
                    else:
                        matched_ds = None
                        for ds_name in datasets:
                            if _clean_id(ds_name) == t_clean:
                                matched_ds = ds_name
                                break
                        if matched_ds:
                            if col_clean in ds_cols.get(matched_ds, set()):
                                ref_tables.add(matched_ds)
                            else:
                                owners = col_to_ds.get(col_clean, set())
                                if len(owners) == 1:
                                    ref_tables.add(next(iter(owners)))
                                elif owners:
                                    ref_tables.update(owners)

                # Check bare [Identifier] in DAX
                for col_match in re.findall(r'\[([a-zA-Z0-9_ ]+)\]', dax_expr):
                    c_clean = _clean_id(col_match)
                    if c_clean in metric_map:
                        target_m = metric_map[c_clean]
                        if target_m.dataset:
                            ref_tables.add(target_m.dataset)
                    elif c_clean in col_to_ds:
                        owners = col_to_ds[c_clean]
                        if len(owners) == 1:
                            ref_tables.add(next(iter(owners)))
                        elif curr_ds not in owners:
                            ref_tables.update(owners)

                if not ref_tables:
                    continue

                if curr_ds and ref_tables.issubset(reach.get(curr_ds, set())):
                    continue

                candidates = [
                    ds_name for ds_name in datasets
                    if ref_tables.issubset(reach.get(ds_name, set()))
                ]

                if candidates:
                    candidates.sort(key=lambda c: len(reach[c]))
                    best_cand = candidates[0]
                    if best_cand != curr_ds:
                        logger.info("Re-anchoring metric '%s' from '%s' to '%s' (referenced tables: %s)", m_name, curr_ds, best_cand, sorted(ref_tables))
                        m.dataset = best_cand
                        sql_expr = getattr(m, "sql_expression", None)
                        if curr_ds and sql_expr:
                            m.sql_expression = re.sub(
                                rf'\b{re.escape(curr_ds.lower())}\b',
                                best_cand.lower(),
                                sql_expr,
                                flags=re.IGNORECASE
                            )

        # 4. Final verification: drop metrics that cannot reach all referenced tables
        surviving_metrics = []
        dropped_metrics = getattr(model, "dropped_metrics", []) or []
        for m in metrics:
            curr_ds = getattr(m, "dataset", None)
            m_name = getattr(m, "unique_name", "") or ""
            dax_expr = getattr(m, "expression", "") or ""
            sql_expr = getattr(m, "sql_expression", "") or ""
            ref_tables = set()

            for t_match, col_match in re.findall(r"'?([a-zA-Z0-9_ ]+)'?\[([a-zA-Z0-9_ ]+)\]", dax_expr):
                col_clean = _clean_id(col_match)
                if col_clean in metric_map:
                    if metric_map[col_clean].dataset:
                        ref_tables.add(metric_map[col_clean].dataset)
                else:
                    for ds_name in datasets:
                        if _clean_id(ds_name) == _clean_id(t_match):
                            ref_tables.add(ds_name)

            for t_match, col_match in re.findall(r'([a-zA-Z0-9_]+)\."?([a-zA-Z0-9_]+)"?', sql_expr):
                col_clean = _clean_id(col_match)
                if col_clean in metric_map:
                    if metric_map[col_clean].dataset:
                        ref_tables.add(metric_map[col_clean].dataset)
                else:
                    for ds_name in datasets:
                        if _clean_id(ds_name) == _clean_id(t_match) and col_clean in ds_cols.get(ds_name, set()):
                            ref_tables.add(ds_name)

            for col_match in re.findall(r'\[([a-zA-Z0-9_ ]+)\]', dax_expr):
                c_clean = _clean_id(col_match)
                if c_clean in metric_map:
                    if metric_map[c_clean].dataset:
                        ref_tables.add(metric_map[c_clean].dataset)
                elif c_clean in col_to_ds:
                    owners = col_to_ds[c_clean]
                    if len(owners) == 1:
                        ref_tables.add(next(iter(owners)))

            if not ref_tables or (curr_ds and ref_tables.issubset(reach.get(curr_ds, set()))):
                surviving_metrics.append(m)
            else:
                is_empty_string = not bool(dax_expr.strip().strip('"\'')) if dax_expr else False
                if is_empty_string:
                    reason = f"Unsupported (empty string constant)"
                    logger.warning("Dropping empty string constant metric '%s': %s", m_name, reason)
                    if getattr(self, "drop_ledger", None):
                        self.drop_ledger.record("metric", m_name, DropStage.DDL_EMISSION, reason, dataset=curr_ds)
                    if hasattr(model, "record_dropped_metric"):
                        model.record_dropped_metric(m_name, reason)
                    elif hasattr(dropped_metrics, "append"):
                        dropped_metrics.append({"name": m_name, "reason": reason})
                else:
                    is_m2m_cause = False
                    for rel in getattr(model, "relationships", []):
                        c_str = str(getattr(rel, "cardinality", "") or "").lower()
                        if c_str in ("many_to_many", "many-to-many", "m:m"):
                            if {rel.from_dataset, rel.to_dataset}.intersection(ref_tables):
                                is_m2m_cause = True
                                break
                    if is_m2m_cause:
                        reason = "many-to-many not supported in Snowflake semantic views; use a bridge table"
                    else:
                        reason = "placeholder: not computable"
                    logger.info("Emitting unreachable metric '%s' on '%s' as NULL placeholder: %s", m_name, curr_ds, reason)
                    m.sql_expression = "CAST(NULL AS DOUBLE)"
                    m.is_placeholder = True
                    m.sync_failure_reason = reason
                    surviving_metrics.append(m)
                    if getattr(self, "drop_ledger", None):
                        self.drop_ledger.record("metric", m_name, DropStage.DDL_EMISSION, reason, dataset=curr_ds)
                    if hasattr(model, "record_dropped_metric"):
                        model.record_dropped_metric(m_name, reason)
                    elif hasattr(dropped_metrics, "append"):
                        dropped_metrics.append({"name": m_name, "reason": reason})

        model.metrics = surviving_metrics


    def generate_ddls(self, sml: SMLModel) -> list[str]:
        if not sml.datasets:
            return []

        self._reanchor_disconnected_fact_metrics(sml)
        logger.info(
            "generate_ddls: building semantic view for model=%s datasets=%s metrics=%s",
            sml.unique_name or sml.label or "<unnamed_sml_model>",
            len(getattr(sml, "datasets", []) or []),
            len(getattr(sml, "metrics", []) or []),
        )
        self._precompute_suggestions(sml)
        snapshot_ddls, source_overrides = self.snapshot_orchestrator.build_for_sml(sml)
        all_ddls = list(snapshot_ddls)

        original_sources: dict[str, str] = {}
        try:
            for dataset in sml.datasets:
                if dataset.unique_name in source_overrides:
                    original_sources[dataset.unique_name] = dataset.source_table
                    dataset.source_table = source_overrides[dataset.unique_name]

            semantic_ddl = self._generate_semantic_view(sml)
            
            try:
                debug_file = _debug_ddl_path(sml)
                debug_file.parent.mkdir(parents=True, exist_ok=True)
                debug_file.write_text(semantic_ddl, encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to persist debug DDL: %s", exc)

            errors = validate_semantic_ddl(semantic_ddl)
            if errors:
                raise ConnectorError("Invalid semantic DDL: " + "; ".join(errors))

            self._guard_relationship_clause(
                model_name=sml.unique_name or sml.label or "model",
                relationships=getattr(sml, "relationships", []),
                semantic_ddl=semantic_ddl,
                fail_on_missing=bool(getattr(self.behavior.snowflake, "fail_on_missing_relationships", True)),
            )
        finally:
            for dataset in sml.datasets:
                if dataset.unique_name in original_sources:
                    dataset.source_table = original_sources[dataset.unique_name]

        return [*all_ddls, semantic_ddl]

    def generate_ddls_from_osi(self, osi: OSIModel) -> list[str]:
        if not osi.datasets:
            return []

        self._reanchor_disconnected_fact_metrics(osi)
        snapshot_ddls, source_overrides = self.snapshot_orchestrator.build_for_osi(osi)
        all_ddls = list(snapshot_ddls)

        original_sources: dict[str, str] = {}
        try:
            for dataset in osi.datasets:
                if dataset.unique_name in source_overrides:
                    original_sources[dataset.unique_name] = dataset.source_table
                    dataset.source_table = source_overrides[dataset.unique_name]

            semantic_ddl = self._generate_semantic_view_from_osi(osi)
            
            try:
                debug_file = _debug_ddl_path(osi)
                debug_file.parent.mkdir(parents=True, exist_ok=True)
                debug_file.write_text(semantic_ddl, encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to persist debug DDL: %s", exc)

            errors = validate_semantic_ddl(semantic_ddl)
            if errors:
                raise ConnectorError("Invalid semantic DDL: " + "; ".join(errors))

            self._guard_relationship_clause(
                model_name=osi.unique_name or osi.label or "model",
                relationships=getattr(osi, "relationships", []),
                semantic_ddl=semantic_ddl,
                fail_on_missing=bool(getattr(self.behavior.snowflake, "fail_on_missing_relationships", True)),
            )
        finally:
            for dataset in osi.datasets:
                if dataset.unique_name in original_sources:
                    dataset.source_table = original_sources[dataset.unique_name]

        return [*all_ddls, semantic_ddl]

    def _generate_semantic_view(self, sml: SMLModel) -> str:
        """SML implementation via builders."""
        self._migrate_numeric_leading_identifiers(sml)
        self._apply_proactive_suffixes(sml)
        
        from semabridge.utils.name_translator import get_target_deployment_name
        
        view_name_raw = sml.unique_name or sml.label or "model"
        safe_view_name = get_target_deployment_name(view_name_raw, "snowflake")
        full_view_name = f'"{self.config.database}"."{self.config.schema_name}"."{safe_view_name}"'
        
        lines = [f"CREATE OR REPLACE SEMANTIC VIEW {full_view_name}"]
        definitions = []
        registry = AliasRegistry()
        
        # PRE-COMPUTE: METRIC COUNTS AND RELATED DATASETS
        metric_counts = {}
        for m in sml.metrics:
            if m.dataset: metric_counts[m.dataset] = metric_counts.get(m.dataset, 0) + 1
        related_ds = {r.from_dataset for r in sml.relationships if r.is_active} | {r.to_dataset for r in sml.relationships if r.is_active}

        # TABLES
        tbuilder = TablesClauseBuilder(
            self.identifier_sanitizer, self.schema_manager, self.config, self.behavior,
            self.live_schema_metadata, cursor=self.cursor,
            enriched_view_mapping=self.enriched_view_mapping,
            failed_enrichment_datasets=self.failed_enrichment_datasets,
            drop_ledger=self.drop_ledger,
        )
        tables_lines, declared_pk, rel_pk_map, ds_lookup, ds_by_name, live_ds_lookup = tbuilder.build_for_sml(sml, registry, metric_counts, related_ds)
        if tables_lines: definitions.append("TABLES (\n" + ",\n".join(tables_lines) + "\n)")

        emission_plan = tbuilder.emission_plan
        norm_rels = emission_plan[0] if emission_plan else []
        role_tables = emission_plan[1] if emission_plan else []
        userel_measure_map = emission_plan[3] if emission_plan else {}

        # RELATIONSHIPS
        rel_lines = self.relationships_builder.build_for_sml(
            sml, registry.dataset_aliases, ds_by_name, ds_lookup, declared_pk, rel_pk_map,
            emission_plan=emission_plan
        )
        if rel_lines: definitions.append("RELATIONSHIPS (\n" + ",\n".join(rel_lines) + "\n)")

        # PRE-EMIT INTEGRITY CHECK
        self._validate_pre_emit_integrity(declared_pk, rel_lines, norm_rels or sml.relationships, registry.dataset_aliases, ds_by_name)

        # DIMENSIONS
        self.dimensions_builder.enriched_view_mapping = self.enriched_view_mapping
        self.dimensions_builder.precompute_details = self.get_precompute_details()
        measure_cols = self._collect_measure_columns(sml, ds_lookup)
        dims_lines, _missing = self.dimensions_builder.build_for_sml(
            sml, registry.dataset_aliases, ds_by_name, ds_lookup, measure_cols,
            live_col_lookup=live_ds_lookup, role_tables=role_tables
        )
        for _ds, _cols in _missing.items():
            self.missing_dims.setdefault(_ds, [])
            for _c in _cols:
                if _c not in self.missing_dims[_ds]:
                    self.missing_dims[_ds].append(_c)

        # FACTS (Private helper columns and metric-referenced numeric columns not declared as dimensions)
        facts_lines = self._build_private_facts(
            sml, registry.dataset_aliases, ds_lookup, live_ds_lookup, safe_view_name,
            declared_dims=dims_lines,
        )
        facts_block_idx = None
        if facts_lines:
            facts_block_idx = len(definitions)
            definitions.append("FACTS (\n" + ",\n".join(facts_lines) + "\n)")

        dimensions_block_idx = None
        if dims_lines:
            dimensions_block_idx = len(definitions)
            definitions.append("DIMENSIONS (\n" + ",\n".join(dims_lines) + "\n)")

        # METRICS
        if userel_measure_map:
            for m in sml.metrics:
                m_key = getattr(m, "unique_name", None) or getattr(m, "name", None)
                role_alias = userel_measure_map.get(m_key)
                if role_alias:
                    for rt in role_tables:
                        if rt.role_alias == role_alias:
                            base_alias = registry.get_alias(rt.base_dataset) or rt.base_dataset
                            patterns = {
                                base_alias,
                                rt.base_dataset,
                                base_alias.replace("_", ""),
                                rt.base_dataset.replace("_", ""),
                            }
                            if getattr(m, "sql_expression", None):
                                for pat in sorted(patterns, key=len, reverse=True):
                                    m.sql_expression = re.sub(rf'\b{re.escape(pat)}\b', role_alias, m.sql_expression, flags=re.IGNORECASE)
                            break

        # METRICS
        from semabridge.utils.identifier_normalizer import IdentifierNormalizer
        alias_by_raw = IdentifierNormalizer(self.identifier_sanitizer).build_alias_lookup(sml.datasets, registry.dataset_aliases)
        all_phys = {c for cols in ds_lookup.values() for c in cols}
        emittable = {self.identifier_sanitizer.sanitize_alias(m.unique_name) for m in sml.metrics if (m.source_column and m.aggregation) or m.sql_expression or getattr(m, "expression", None)}

        self.metrics_builder.enriched_view_mapping = self.enriched_view_mapping
        self.metrics_builder.failed_enrichment_datasets = self.failed_enrichment_datasets
        self.metrics_builder.live_schema_metadata = self.live_schema_metadata
        self.metrics_builder.no_populated_date_link_datasets = self.no_populated_date_link_datasets

        metrics_lines = self.metrics_builder.build_for_sml(sml, registry.dataset_aliases, ds_by_name, ds_lookup, alias_by_raw, all_phys, emittable)
        self._ensure_metric_referenced_facts(metrics_lines, dims_lines, facts_lines, definitions, facts_block_idx)

        # CROSS-CLAUSE DEDUPLICATION
        if dims_lines and metrics_lines and dimensions_block_idx is not None:
            metrics_lines, definitions[dimensions_block_idx] = self._deduplicate_cross_clause(metrics_lines, dims_lines, definitions[dimensions_block_idx])
            if not definitions[dimensions_block_idx].strip():
                definitions[dimensions_block_idx] = ""

        if metrics_lines: definitions.append("METRICS (\n" + "\n".join(metrics_lines) + "\n)")

        definitions = [d for d in definitions if d.strip()]
        final_ddl = lines[0] + "\n" + "\n".join(definitions) + ";"
        return fix_global_sums(final_ddl, self.translator)

    def _generate_semantic_view_from_osi(self, osi: OSIModel) -> str:
        """OSI implementation via builders."""
        self._apply_proactive_suffixes(osi)
        from semabridge.utils.name_translator import get_target_deployment_name
        
        view_name_raw = osi.unique_name or osi.label or "model"
        safe_view_name = get_target_deployment_name(view_name_raw, "snowflake")
        full_view_name = f'"{self.config.database}"."{self.config.schema_name}"."{safe_view_name}"'

        
        lines = [f"CREATE OR REPLACE SEMANTIC VIEW {full_view_name}"]
        definitions = []
        registry = AliasRegistry()
        
        metric_counts = {}
        for m in osi.metrics:
            if m.dataset: metric_counts[m.dataset] = metric_counts.get(m.dataset, 0) + 1
        related_ds = {r.from_dataset for r in osi.relationships if r.is_active} | {r.to_dataset for r in osi.relationships if r.is_active}

        # TABLES
        tbuilder = TablesClauseBuilder(
            self.identifier_sanitizer, self.schema_manager, self.config, self.behavior,
            self.live_schema_metadata, cursor=self.cursor,
            enriched_view_mapping=self.enriched_view_mapping,
            failed_enrichment_datasets=self.failed_enrichment_datasets,
            drop_ledger=self.drop_ledger,
        )
        tables_lines, declared_pk, rel_pk_map, ds_lookup, ds_by_name, live_ds_lookup = tbuilder.build_for_osi(osi, registry, metric_counts, related_ds)
        if tables_lines: definitions.append("TABLES (\n" + ",\n".join(tables_lines) + "\n)")

        emission_plan = tbuilder.emission_plan
        norm_rels = emission_plan[0] if emission_plan else []
        role_tables = emission_plan[1] if emission_plan else []
        userel_measure_map = emission_plan[3] if emission_plan else {}

        # RELATIONSHIPS
        rel_lines = self.relationships_builder.build_for_osi(
            osi, registry.dataset_aliases, ds_by_name, ds_lookup, declared_pk, rel_pk_map,
            emission_plan=emission_plan
        )
        if rel_lines: definitions.append("RELATIONSHIPS (\n" + ",\n".join(rel_lines) + "\n)")

        # PRE-EMIT INTEGRITY CHECK
        self._validate_pre_emit_integrity(declared_pk, rel_lines, norm_rels or osi.relationships, registry.dataset_aliases, ds_by_name)

        # FACTS (Private helper columns)
        self.dimensions_builder.enriched_view_mapping = self.enriched_view_mapping
        self.dimensions_builder.precompute_details = self.get_precompute_details()
        # DIMENSIONS
        measure_cols = self._collect_measure_columns(osi, ds_lookup)
        dims_lines, _missing = self.dimensions_builder.build_for_osi(
            osi, registry.dataset_aliases, ds_by_name, ds_lookup, measure_cols,
            live_col_lookup=live_ds_lookup, role_tables=role_tables
        )
        for _ds, _cols in _missing.items():
            self.missing_dims.setdefault(_ds, [])
            for _c in _cols:
                if _c not in self.missing_dims[_ds]:
                    self.missing_dims[_ds].append(_c)

        # FACTS (Private helper columns and metric-referenced numeric columns not declared as dimensions)
        facts_lines = self._build_private_facts(
            osi, registry.dataset_aliases, ds_lookup, live_ds_lookup, safe_view_name,
            declared_dims=dims_lines,
        )
        facts_block_idx = None
        if facts_lines:
            facts_block_idx = len(definitions)
            definitions.append("FACTS (\n" + ",\n".join(facts_lines) + "\n)")

        dimensions_block_idx = None
        if dims_lines:
            dimensions_block_idx = len(definitions)
            definitions.append("DIMENSIONS (\n" + ",\n".join(dims_lines) + "\n)")

        # METRICS
        if userel_measure_map:
            for m in osi.metrics:
                if m.unique_name in userel_measure_map:
                    role_alias = userel_measure_map[m.unique_name]
                    for rt in role_tables:
                        if rt.role_alias == role_alias:
                            base_alias = registry.get_alias(rt.base_dataset) or rt.base_dataset
                            if getattr(m, "sql_expression", None):
                                m.sql_expression = re.sub(rf'\b{re.escape(base_alias)}\b', role_alias, m.sql_expression, flags=re.IGNORECASE)
                                m.sql_expression = re.sub(rf'\b{re.escape(rt.base_dataset)}\b', role_alias, m.sql_expression, flags=re.IGNORECASE)
                            break

        # METRICS
        from semabridge.utils.identifier_normalizer import IdentifierNormalizer
        alias_by_raw = IdentifierNormalizer(self.identifier_sanitizer).build_alias_lookup(osi.datasets, registry.dataset_aliases)
        all_phys = {c for cols in ds_lookup.values() for c in cols}
        emittable = {self.identifier_sanitizer.sanitize_alias(m.unique_name) for m in osi.metrics if (m.source_column and m.aggregation) or getattr(m, "expression", None) or getattr(m, "sql_expression", None)}
        
        self.metrics_builder.enriched_view_mapping = self.enriched_view_mapping
        self.metrics_builder.failed_enrichment_datasets = self.failed_enrichment_datasets
        self.metrics_builder.live_schema_metadata = self.live_schema_metadata
        self.metrics_builder.no_populated_date_link_datasets = self.no_populated_date_link_datasets

        metrics_lines = self.metrics_builder.build_for_osi(osi, registry.dataset_aliases, ds_by_name, ds_lookup, alias_by_raw, all_phys, emittable)
        self._ensure_metric_referenced_facts(metrics_lines, dims_lines, facts_lines, definitions, facts_block_idx)
        
        if dims_lines and metrics_lines and dimensions_block_idx is not None:
            metrics_lines, definitions[dimensions_block_idx] = self._deduplicate_cross_clause(metrics_lines, dims_lines, definitions[dimensions_block_idx])
            if not definitions[dimensions_block_idx].strip():
                definitions[dimensions_block_idx] = ""

        if metrics_lines: definitions.append("METRICS (\n" + "\n".join(metrics_lines) + "\n)")

        definitions = [d for d in definitions if d.strip()]
        final_ddl = lines[0] + "\n" + "\n".join(definitions) + ";"
        return fix_global_sums(final_ddl, self.translator)

    @staticmethod
    def _sanitize_view_name(name: str) -> str:
        """Preserve model name casing for semantic-view object names."""
        raw = str(name or "").strip() or "model"
        clean = re.sub(r"[^A-Za-z0-9_$]", "_", raw)
        clean = re.sub(r"_+", "_", clean).strip("_")
        if not clean:
            clean = "model"
        if clean[0].isdigit():
            clean = f"_{clean}"
        return clean

    def _collect_measure_columns(self, model: Any, ds_lookup: Dict[str, Set[str]]) -> Set[Tuple[str, str]]:
        measure_columns = set()
        for metric in model.metrics:
            if metric.source_column:
                col_norm = self.identifier_sanitizer.sanitize_column(metric.source_column)
                measure_columns.add((str(metric.dataset or "").strip().casefold(), col_norm))
                for ds_name, cols in ds_lookup.items():
                    if col_norm in {c.upper() for c in cols}:
                        measure_columns.add((ds_name.strip().casefold(), col_norm))
        return measure_columns

    def _collect_columns_referenced_by_metrics(
        self,
        model: Any,
        dataset_aliases: Dict[str, str],
    ) -> List[Tuple[Any, Any]]:
        """Collect all (dataset, column_obj) referenced by any metric in the model."""
        alias_to_dataset: Dict[str, Any] = {}
        for ds in getattr(model, "datasets", []) or []:
            alias_to_dataset[ds.unique_name.casefold()] = ds
            a = dataset_aliases.get(ds.unique_name)
            if a:
                alias_to_dataset[a.casefold()] = ds

        metric_names = {
            str(getattr(m, "unique_name", "") or "").casefold()
            for m in (getattr(model, "metrics", []) or [])
            if getattr(m, "unique_name", None)
        }

        referenced: List[Tuple[Any, Any]] = []
        seen: Set[Tuple[str, str]] = set()

        def _record_ref(ds_obj: Any, col_obj: Any) -> None:
            key = (ds_obj.unique_name.casefold(), col_obj.unique_name.casefold())
            if key not in seen:
                seen.add(key)
                referenced.append((ds_obj, col_obj))

        for m in getattr(model, "metrics", []) or []:
            m_ds_name = str(getattr(m, "dataset", "") or "")
            m_ds = alias_to_dataset.get(m_ds_name.casefold())

            # 1. source_column
            src_col = getattr(m, "source_column", None)
            if src_col:
                found_col = False
                if m_ds:
                    for c in getattr(m_ds, "columns", []) or []:
                        if (c.unique_name.casefold() == str(src_col).casefold() or
                            str(getattr(c, "source_column", "") or "").casefold() == str(src_col).casefold()):
                            _record_ref(m_ds, c)
                            found_col = True
                            break
                if not found_col:
                    for ds in getattr(model, "datasets", []) or []:
                        for c in getattr(ds, "columns", []) or []:
                            if (c.unique_name.casefold() == str(src_col).casefold() or
                                str(getattr(c, "source_column", "") or "").casefold() == str(src_col).casefold()):
                                _record_ref(ds, c)
                                found_col = True
                                break
                        if found_col:
                            break

            # 2. expressions (sql_expression and expression)
            for expr in (getattr(m, "sql_expression", None), getattr(m, "expression", None)):
                if not expr or not isinstance(expr, str):
                    continue
                clean = re.sub(r'(?i)\bWITH\s+SYNONYMS\s*=\s*\(.*?\)', '', expr)
                clean = re.sub(r'(?i)\bWITH\s+SYNONYMS\b.*', '', clean)

                # Match SQL: TBL."COL", TBL.COL, "TBL"."COL", "TBL".COL
                for match in re.finditer(r'(?:"?([A-Za-z0-9_]+)"?)\.(?:"([^"]+)"|([A-Za-z0-9_]+))', clean):
                    tbl_ref = match.group(1)
                    col_ref = match.group(2) or match.group(3)
                    if not col_ref:
                        continue
                    ds = alias_to_dataset.get(tbl_ref.casefold())
                    if not ds:
                        continue
                    found_col = False
                    for c in getattr(ds, "columns", []) or []:
                        c_phys = (
                            self.schema_manager._resolve_physical_column_name(ds, c.unique_name)
                            if hasattr(self, "schema_manager") and hasattr(self.schema_manager, "_resolve_physical_column_name")
                            else c.unique_name
                        )
                        if (c.unique_name.casefold() == col_ref.casefold() or
                            str(getattr(c, "source_column", "") or "").casefold() == col_ref.casefold() or
                            str(c_phys).casefold() == col_ref.casefold()):
                            _record_ref(ds, c)
                            found_col = True
                            break
                    if not found_col and col_ref.casefold() in metric_names:
                        continue

                # Match DAX: Table[Col] or 'Table'[Col]
                for match in re.finditer(r"'?([A-Za-z0-9_ ]+)'?\[([^\]]+)\]", clean):
                    tbl_ref = match.group(1).strip()
                    col_ref = match.group(2).strip()
                    if not col_ref:
                        continue
                    ds = alias_to_dataset.get(tbl_ref.casefold())
                    if not ds:
                        continue
                    found_col = False
                    for c in getattr(ds, "columns", []) or []:
                        if (c.unique_name.casefold() == col_ref.casefold() or
                            str(getattr(c, "source_column", "") or "").casefold() == col_ref.casefold()):
                            _record_ref(ds, c)
                            found_col = True
                            break
                    if not found_col and col_ref.casefold() in metric_names:
                        continue

                # Match unqualified DAX: [Col]
                for match in re.finditer(r'(?<![A-Za-z0-9_\]])\[([^\]]+)\]', clean):
                    col_ref = match.group(1).strip()
                    if not col_ref:
                        continue
                    found_col = False
                    if m_ds:
                        for c in getattr(m_ds, "columns", []) or []:
                            if (c.unique_name.casefold() == col_ref.casefold() or
                                str(getattr(c, "source_column", "") or "").casefold() == col_ref.casefold()):
                                _record_ref(m_ds, c)
                                found_col = True
                                break
                    if not found_col:
                        for ds in getattr(model, "datasets", []) or []:
                            for c in getattr(ds, "columns", []) or []:
                                if (c.unique_name.casefold() == col_ref.casefold() or
                                    str(getattr(c, "source_column", "") or "").casefold() == col_ref.casefold()):
                                    _record_ref(ds, c)
                                    found_col = True
                                    break
                            if found_col:
                                break
                    if not found_col and col_ref.casefold() in metric_names:
                        continue

        return referenced

    def _build_private_facts(
        self,
        model: Any,
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
        live_col_lookup: Optional[Dict[str, Set[str]]],
        model_name: str,
        declared_dims: Optional[List[str]] = None,
    ) -> List[str]:
        facts_lines: List[str] = []
        seen_facts: Set[Tuple[str, str]] = set()

        def _is_numeric_type(dtype: Any) -> bool:
            if dtype is None:
                return False
            val = getattr(dtype, "value", str(dtype)).lower()
            return val in {
                "integer", "int", "int64", "bigint", "smallint", "tinyint",
                "decimal", "numeric", "number", "float", "double", "real",
                "fixed", "float4", "float8", "int2", "int4", "int8",
            }

        ref_cols = self._collect_columns_referenced_by_metrics(model, dataset_aliases)

        # Declared dimension keys (to avoid duplicate expression names across FACTS and DIMENSIONS)
        declared_dim_keys: Set[str] = set()
        if declared_dims:
            for d_line in declared_dims:
                key = extract_expr_key(d_line)
                if key:
                    declared_dim_keys.add(key.upper())
                target_m = re.search(r'\bAS\s+(\w+)\."([^\"]+)"', d_line, re.IGNORECASE)
                if target_m:
                    declared_dim_keys.add(f"{target_m.group(1).upper()}.{target_m.group(2).upper()}")

        # 1. Helper columns
        enriched_datasets = set((self.enriched_view_mapping or {}).keys())
        known_helpers = collect_helper_columns_for_model(
            model,
            precompute_details=self.get_precompute_details(),
            anchor_flag_map=getattr(self.translator, "anchor_flag_map", None) if self.translator else None,
            enriched_datasets=enriched_datasets,
            dataset_col_lookup=dataset_col_lookup,
            live_col_lookup=live_col_lookup,
        )

        for dataset in getattr(model, "datasets", []) or []:
            ds_name = dataset.unique_name
            alias = dataset_aliases.get(ds_name)
            if not alias:
                continue

            candidate_cols: Set[str] = set()
            if live_col_lookup and ds_name in live_col_lookup:
                candidate_cols.update(live_col_lookup[ds_name])
            elif dataset_col_lookup and ds_name in dataset_col_lookup:
                candidate_cols.update(dataset_col_lookup[ds_name])

            is_enriched = (
                ds_name in (self.enriched_view_mapping or {})
                or str(ds_name).casefold() in {k.casefold() for k in (self.enriched_view_mapping or {})}
            )
            if is_enriched:
                for d in self.get_precompute_details():
                    if str(d.get("target_dataset", "")).casefold() == str(ds_name).casefold():
                        candidate_cols.add(d["precomputed_column"])
                if self.translator and getattr(self.translator, "anchor_flag_map", None):
                    ds_flags = self.translator.anchor_flag_map.get(str(ds_name).casefold()) or {}
                    for flag_val in ds_flags.values():
                        if isinstance(flag_val, str) and flag_val:
                            candidate_cols.add(flag_val)

            if live_col_lookup and ds_name in live_col_lookup:
                live_upper = {x.upper() for x in live_col_lookup[ds_name]}
                candidate_cols = {c for c in candidate_cols if c.upper() in live_upper}

            for col in sorted(candidate_cols):
                if not col:
                    continue
                if is_helper_column(col, ds_name, enriched_datasets, known_helpers):
                    fact_key = (alias.upper(), col.upper())
                    if fact_key in seen_facts:
                        continue
                    expr_key = f"{alias.upper()}.{col.upper()}"
                    sanitized_name = self.sanitizer.sanitize_semantic_name(col).upper()
                    alt_expr_key = f"{alias.upper()}.{sanitized_name}"
                    if expr_key in declared_dim_keys or alt_expr_key in declared_dim_keys:
                        continue  # Already declared as a dimension; do not duplicate in FACTS!
                    seen_facts.add(fact_key)
                    phys_ref = self.sanitizer.format_physical_column_ref(alias, col, model_name=model_name)
                    facts_lines.append(f'  PRIVATE {alias}."{col}" AS {phys_ref}')

        # 2. Numeric columns referenced by metrics (public if visible, private if hidden)

        for ds, col_obj in ref_cols:
            if not _is_numeric_type(col_obj.data_type):
                continue
            alias = dataset_aliases.get(ds.unique_name)
            if not alias:
                continue
            col_name = col_obj.unique_name
            fact_key = (alias.upper(), col_name.upper())
            if fact_key in seen_facts:
                continue

            expr_key = f"{alias.upper()}.{col_name.upper()}"
            sanitized_name = self.sanitizer.sanitize_semantic_name(col_name).upper()
            alt_expr_key = f"{alias.upper()}.{sanitized_name}"
            if expr_key in declared_dim_keys or alt_expr_key in declared_dim_keys:
                continue  # Already declared as a dimension; do not move to FACTS!

            seen_facts.add(fact_key)

            phys_col = (
                self.schema_manager._resolve_physical_column_name(ds, col_name)
                if hasattr(self, "schema_manager") and hasattr(self.schema_manager, "_resolve_physical_column_name")
                else col_name
            )
            phys_ref = self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)
            visibility = "PRIVATE" if bool(getattr(col_obj, "is_hidden", False)) else "PUBLIC"
            facts_lines.append(f'  {visibility} {alias}."{col_name}" AS {phys_ref}')

        return facts_lines

    def _ensure_metric_referenced_facts(
        self,
        metrics_lines: List[str],
        dims_lines: Optional[List[str]],
        facts_lines: List[str],
        definitions: List[str],
        facts_block_idx: Optional[int],
    ) -> None:
        """Ensure all columns referenced in METRICS expressions are declared under FACTS or DIMENSIONS."""
        declared_col_keys: Set[Tuple[str, str]] = set()
        for d_line in (dims_lines or []):
            m = re.search(r'(?:(?:PRIVATE|PUBLIC)\s+)?([A-Za-z0-9_]+)\.("[^"]+"|\[[^\]]+\]|[A-Za-z0-9_]+)', d_line, re.IGNORECASE)
            if m:
                tbl = m.group(1).upper()
                col = m.group(2).strip('"\\[\\]').upper()
                declared_col_keys.add((tbl, col))
                clean_c = re.sub(r'[^A-Za-z0-9]', '', col)
                if clean_c:
                    declared_col_keys.add((tbl, clean_c))
        for f_line in (facts_lines or []):
            m = re.search(r'(?:(?:PRIVATE|PUBLIC)\s+)?([A-Za-z0-9_]+)\.("[^"]+"|\[[^\]]+\]|[A-Za-z0-9_]+)', f_line, re.IGNORECASE)
            if m:
                tbl = m.group(1).upper()
                col = m.group(2).strip('"\\[\\]').upper()
                declared_col_keys.add((tbl, col))
                clean_c = re.sub(r'[^A-Za-z0-9]', '', col)
                if clean_c:
                    declared_col_keys.add((tbl, clean_c))

        declared_metric_keys: Set[Tuple[str, str]] = set()
        for m_line in (metrics_lines or []):
            m = re.match(r'^\s*(?:(?:PRIVATE|PUBLIC)\s+)?([A-Za-z0-9_]+)\.("[^"]+"|\[[^\]]+\]|[A-Za-z0-9_]+)\s+AS\s+', m_line, re.IGNORECASE)
            if m:
                tbl = m.group(1).upper()
                met = m.group(2).strip('"\\[\\]').upper()
                declared_metric_keys.add((tbl, met))
                clean_m = re.sub(r'[^A-Za-z0-9]', '', met)
                if clean_m:
                    declared_metric_keys.add((tbl, clean_m))

        extra_facts: List[str] = []
        seen_extra: Set[Tuple[str, str]] = set()
        for m_line in (metrics_lines or []):
            parts = m_line.split(" AS ", 1)
            if len(parts) < 2:
                continue
            expr = parts[1]
            clean_expr = re.sub(r'(?i)\bWITH\s+SYNONYMS\s*=\s*\(.*?\)', '', expr)
            clean_expr = re.sub(r'(?i)\bWITH\s+SYNONYMS\b.*', '', clean_expr)
            for m in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*|"[^"]+")', clean_expr):
                ref_tbl = m.group(1).upper()
                ref_col = m.group(2).strip('"')
                col_upper = ref_col.upper()
                col_clean = re.sub(r'[^A-Za-z0-9]', '', col_upper)
                if (ref_tbl, col_upper) in declared_col_keys or (ref_tbl, col_clean) in declared_col_keys:
                    continue
                if (ref_tbl, col_upper) in declared_metric_keys or (ref_tbl, col_clean) in declared_metric_keys:
                    continue
                if (ref_tbl, col_upper) in seen_extra:
                    continue
                seen_extra.add((ref_tbl, col_upper))
                extra_facts.append(f'  PUBLIC {ref_tbl}."{ref_col}" AS {ref_tbl}."{ref_col}"')

        if extra_facts:
            facts_lines.extend(extra_facts)
            facts_block = "FACTS (\n" + ",\n".join(facts_lines) + "\n)"
            if facts_block_idx is not None:
                definitions[facts_block_idx] = facts_block
            else:
                definitions.insert(0, facts_block)

    def _deduplicate_cross_clause(
        self,
        metrics_lines: List[str],
        dims_lines: List[str],
        dims_block: str,
    ) -> Tuple[List[str], str]:
        metric_keys = {extract_expr_key(line) for line in metrics_lines if extract_expr_key(line)}
        filtered_dims = [line for line in dims_lines if extract_expr_key(line) not in metric_keys]
        if len(filtered_dims) < len(dims_lines):
            logger.warning("Removed duplicate entries from DIMENSIONS that exist in METRICS")
            dims_block = "DIMENSIONS (\n" + ",\n".join(filtered_dims) + "\n)" if filtered_dims else ""
        return metrics_lines, dims_block

    def _precompute_duplicate_mappings(self, model: Any, is_osi: bool = False) -> None:
        """Persist duplicate mappings for all datasets/metrics before emit."""
        if is_osi:
            self._precompute_duplicate_mappings_for_osi(model)
        else:
            self._precompute_duplicate_mappings_for_sml(model)

    def _precompute_duplicate_mappings_for_sml(self, sml: SMLModel) -> None:
        for dataset in sml.datasets:
            self._collect_physical_source_columns(dataset)

        metric_base_totals: dict[str, int] = {}
        for metric in sml.metrics:
            metric_base_alias = self.identifier_sanitizer.sanitize_alias(metric.unique_name)
            metric_base_totals[metric_base_alias] = metric_base_totals.get(metric_base_alias, 0) + 1

        metric_namespace = self._duplicate_namespace_key(sml.unique_name or sml.label)
        metric_base_seen: dict[str, int] = {}
        metric_signature_seen: dict[str, int] = {}
        
        for metric in sml.metrics:
            metric_base_alias = self.identifier_sanitizer.sanitize_alias(metric.unique_name)
            if metric_base_totals.get(metric_base_alias, 0) > 1:
                metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
                metric_base_seen[metric_base_alias] = metric_seen_idx
                
                entity_name = metric.dataset or "UnknownEntity"
                field_name = metric.unique_name or "UnknownField"
                collision_hash = self._generate_deterministic_hash(entity_name, field_name)
                preferred_name = f"{metric_base_alias}_{collision_hash}".upper()

                metric_signature_seed = self._build_duplicate_signature_seed(
                    source_name=metric.unique_name,
                    source_expression=getattr(metric, "sql_expression", None) or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"

                self._resolve_persistent_duplicate_name(
                    scope_type="metric",
                    namespace_key=metric_namespace,
                    dataset_key=self.identifier_sanitizer.sanitize_alias(metric.dataset),
                    normalized_base=metric_base_alias,
                    source_name=metric.unique_name,
                    source_signature=metric_signature,
                    preferred_name=preferred_name,
                )

    def _precompute_duplicate_mappings_for_osi(self, osi: OSIModel) -> None:
        for dataset in osi.datasets:
            self._collect_physical_source_columns_osi(dataset)

        metric_base_totals: dict[str, int] = {}
        for metric in osi.metrics:
            base_alias = self.identifier_sanitizer.sanitize_alias(metric.unique_name)
            metric_base_totals[base_alias] = metric_base_totals.get(base_alias, 0) + 1

        metric_namespace = self._duplicate_namespace_key(osi.unique_name or osi.label)
        metric_base_seen: dict[str, int] = {}
        metric_signature_seen: dict[str, int] = {}
        for metric in osi.metrics:
            metric_base_alias = self.identifier_sanitizer.sanitize_alias(metric.unique_name)
            if metric_base_totals.get(metric_base_alias, 0) > 1:
                metric_seen_idx = metric_base_seen.get(metric_base_alias, 0) + 1
                metric_base_seen[metric_base_alias] = metric_seen_idx
                
                entity_name = metric.dataset or "UnknownEntity"
                field_name = metric.unique_name or "UnknownField"
                collision_hash = self._generate_deterministic_hash(entity_name, field_name)
                preferred_name = f"{metric_base_alias}_{collision_hash}".upper()

                metric_signature_seed = self._build_duplicate_signature_seed(
                    source_name=metric.unique_name,
                    source_expression=getattr(metric, "sql_expression", None) or metric.expression,
                    data_type=None,
                    aggregation=metric.aggregation.value if metric.aggregation else None,
                )
                sig_idx = metric_signature_seen.get(metric_signature_seed, 0) + 1
                metric_signature_seen[metric_signature_seed] = sig_idx
                metric_signature = f"{metric_signature_seed}::occ{sig_idx}"

                self._resolve_persistent_duplicate_name(
                    scope_type="metric",
                    namespace_key=metric_namespace,
                    dataset_key=self.identifier_sanitizer.sanitize_alias(metric.dataset),
                    normalized_base=metric_base_alias,
                    source_name=metric.unique_name,
                    source_signature=metric_signature,
                    preferred_name=preferred_name,
                )

    def _collect_physical_source_columns(self, dataset: SMLDataset) -> None:
        valid_columns = []
        base_totals: dict[str, int] = {}
        for col in dataset.columns:
            if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                continue
            source_expr = getattr(col, 'source_expression', None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                continue
            valid_columns.append(col)
            safe_base = self.identifier_sanitizer.sanitize_column(col.unique_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self.identifier_sanitizer.sanitize_alias(dataset.source_table or dataset.unique_name)
        
        for col in valid_columns:
            safe_base = self.identifier_sanitizer.sanitize_column(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) > 1:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                
                entity_name = dataset.source_table or dataset.unique_name or "UnknownEntity"
                field_name = col.unique_name or "UnknownField"
                collision_hash = self._generate_deterministic_hash(entity_name, field_name)
                preferred_name = f"{safe_base}_{collision_hash}".upper()
                self._resolve_persistent_duplicate_name(
                    scope_type="column",
                    namespace_key=namespace_key,
                    dataset_key=dataset_key,
                    normalized_base=safe_base,
                    source_name=col.unique_name,
                    source_signature=source_signature,
                    preferred_name=preferred_name,
                )

    def _collect_physical_source_columns_osi(self, dataset: OSIDataset) -> None:
        valid_columns = []
        base_totals: dict[str, int] = {}
        for col in dataset.columns:
            if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                continue
            source_expr = getattr(col, "source_expression", None)
            if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                continue
            valid_columns.append(col)
            safe_base = self.identifier_sanitizer.sanitize_column(col.unique_name)
            base_totals[safe_base] = base_totals.get(safe_base, 0) + 1

        base_seen: dict[str, int] = {}
        signature_seen: dict[str, int] = {}
        namespace_key = self._duplicate_namespace_key()
        dataset_key = self.identifier_sanitizer.sanitize_alias(dataset.source_table or dataset.unique_name)
        for col in valid_columns:
            safe_base = self.identifier_sanitizer.sanitize_column(col.unique_name)
            next_idx = base_seen.get(safe_base, 0) + 1
            base_seen[safe_base] = next_idx

            if base_totals.get(safe_base, 0) > 1:
                signature_seed = self._build_duplicate_signature_seed(
                    source_name=col.unique_name,
                    source_expression=getattr(col, "source_expression", None),
                    data_type=str(getattr(col, "data_type", "")),
                )
                sig_idx = signature_seen.get(signature_seed, 0) + 1
                signature_seen[signature_seed] = sig_idx
                source_signature = f"{signature_seed}::occ{sig_idx}"
                
                entity_name = dataset.source_table or dataset.unique_name or "UnknownEntity"
                field_name = col.unique_name or "UnknownField"
                collision_hash = self._generate_deterministic_hash(entity_name, field_name)
                preferred_name = f"{safe_base}_{collision_hash}".upper()
                self._resolve_persistent_duplicate_name(
                    scope_type="column",
                    namespace_key=namespace_key,
                    dataset_key=dataset_key,
                    normalized_base=safe_base,
                    source_name=col.unique_name,
                    source_signature=source_signature,
                    preferred_name=preferred_name,
                )

    @staticmethod
    def _generate_deterministic_hash(entity_name: str, field_name: str) -> str:
        """Generate a consistent 4-character uppercase hash for a given entity and field."""
        seed_str = f"{entity_name}{field_name}".encode('utf-8')
        full_hash = hashlib.sha256(seed_str).hexdigest()
        return full_hash[:4].upper()

    def _duplicate_namespace_key(self, model_name: Optional[str] = None) -> str:
        parts = [
            self.identifier_sanitizer.sanitize_alias(self.config.database or "DB"),
            self.identifier_sanitizer.sanitize_alias(self.config.schema_name or "SCHEMA"),
        ]
        if model_name:
            parts.append(self.identifier_sanitizer.sanitize_alias(model_name))
        return ".".join(parts)

    def _build_duplicate_signature_seed(self, source_name: str, source_expression: Optional[str], data_type: Optional[str], aggregation: Optional[str] = None) -> str:
        return "|".join([source_name or "", source_expression or "", data_type or "", aggregation or ""])

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

    def _apply_proactive_suffixes(self, model: Any) -> None:
        """Proactively append structural suffixes for specific naming strategies.

        Only activates when naming_strategy is 'source_prefix' or 'entity_suffix'
        at the project level. For 'deterministic_hash' (the default), collision
        suffixes are applied reactively in the duplicate-mapping pipeline.
        """
        naming_strategy = str(getattr(self.config, "naming_strategy", "deterministic_hash")).strip().lower()
        if naming_strategy not in ("source_prefix", "entity_suffix"):
            return

        suffix = "_HK"
        for ds in getattr(model, "datasets", []):
            for col in getattr(ds, "columns", []):
                col_name = str(col.unique_name or "").upper()
                # Apply suffix to ID fields if not already suffixed
                if col_name.endswith("_ID") and not col_name.endswith(suffix):
                    col.unique_name = f"{col.unique_name}{suffix}"

    def _migrate_numeric_leading_identifiers(self, model: Any) -> None:
        """Snowflake semantic identifiers cannot reliably start with digits."""
        for ds in model.datasets:
            if ds.unique_name and ds.unique_name[0].isdigit():
                ds.unique_name = f"_{ds.unique_name}"
            for col in ds.columns:
                if col.unique_name and col.unique_name[0].isdigit():
                    col.unique_name = f"_{col.unique_name}"
        for metric in model.metrics:
            if metric.unique_name and metric.unique_name[0].isdigit():
                metric.unique_name = f"_{metric.unique_name}"
            if metric.dataset and metric.dataset[0].isdigit():
                metric.dataset = f"_{metric.dataset}"

    def _guard_relationship_clause(
        self,
        model_name: str,
        relationships: list[Any],
        semantic_ddl: str,
        *,
        fail_on_missing: bool,
    ) -> None:
        """Detect and block relationship loss between model and emitted DDL."""
        active_relationships = 0
        for rel in relationships or []:
            if not getattr(rel, "is_active", True) or getattr(rel, "is_skipped", False):
                continue
            card_val = getattr(getattr(rel, "cardinality", ""), "value", getattr(rel, "cardinality", ""))
            card_str = str(card_val or "").lower().split(".")[-1].replace("_", "-")
            if card_str in ("many-to-many", "m:m", "manytomany"):
                continue  # Many-to-many relationships are skipped by design
            if (
                getattr(rel, "from_dataset", None)
                and getattr(rel, "to_dataset", None)
                and (getattr(rel, "from_columns", None) or [])
                and (getattr(rel, "to_columns", None) or [])
            ):
                active_relationships += 1

        if active_relationships == 0:
            return

        has_relationship_clause = bool(
            re.search(r"\bRELATIONSHIPS\s*\(", semantic_ddl or "", re.IGNORECASE)
        )
        if has_relationship_clause:
            return

        msg = (
            f"Model '{model_name}' has {active_relationships} active relationship(s), "
            "but generated semantic-view DDL has no RELATIONSHIPS clause. "
            "Aborting deploy to prevent relationship loss in Snowflake."
        )
        if fail_on_missing:
            logger.error(msg)
            raise ValueError(msg)
        logger.warning(msg)

    @staticmethod
    def _select_semantic_view_ddl(ddls: list[str]) -> str:
        """Pick the semantic-view statement from a list of DDLs."""
        for ddl in ddls:
            if re.search(r"\bCREATE\s+OR\s+REPLACE\s+SEMANTIC\s+VIEW\b", ddl, re.IGNORECASE):
                return ddl
        return ddls[0] if ddls else ""

    def _validate_pre_emit_integrity(
        self,
        declared_pk: dict[str, list[str]],
        rel_lines: list[str],
        relationships: Any,
        dataset_aliases: dict[str, str],
        ds_by_name: dict[str, Any],
    ) -> None:
        """Fails the dry run/emission if:
        1. A PRIMARY KEY is declared on a fact table, or
        2. Any relationship's columns differ from the model relationship's bound columns.

        Both from the model only; whether a key is unique in the data is a
        warning (schema_manager.warn_if_key_not_unique), never a failure.
        """
        from semabridge.connectors.relationship_emission_helper import EmissionRelationship

        many_side: set[str] = set()
        one_side: set[str] = set()
        for rel in relationships or []:
            if isinstance(rel, EmissionRelationship):
                if rel.is_skipped:
                    continue
                if rel.cardinality == "one-to-one":
                    one_side.add(rel.from_dataset)
                    one_side.add(rel.to_dataset)
                    continue
                many_side.add(rel.from_dataset)
                target = rel.role_alias if rel.is_role_playing and rel.role_alias else rel.to_dataset
                one_side.add(target)
                one_side.add(rel.to_dataset)
            else:
                c_val = getattr(getattr(rel, "cardinality", ""), "value", getattr(rel, "cardinality", ""))
                c_clean = str(c_val or "").lower().split(".")[-1].replace("_", "-")
                if c_clean in ("many-to-many", "m:m", "manytomany"):
                    continue
                f = getattr(rel, "from_dataset", None)
                t = getattr(rel, "to_dataset", None)
                if c_clean in ("one-to-one", "1:1", "onetoone", "11"):
                    if f: one_side.add(f)
                    if t: one_side.add(t)
                    continue
                if c_clean in ("one-to-many", "1:m", "onetomany", "1m"):
                    f, t = t, f
                if f: many_side.add(f)
                if t: one_side.add(t)

        fact_datasets = {ds for ds in many_side if ds not in one_side}
        fact_aliases = {dataset_aliases.get(ds, ds).upper() for ds in fact_datasets}

        for alias, cols in declared_pk.items():
            if alias.upper() in fact_aliases and cols:
                raise ValueError(
                    f"Pre-emit check failed: Fact table '{alias}' declared PRIMARY KEY {cols}, "
                    f"but fact tables must have no primary key."
                )

        # 2. Validate relationships match model's bound columns
        active_rels = [
            r for r in relationships
            if getattr(r, "is_active", True) and not getattr(r, "is_skipped", False)
        ]
        for rel in active_rels:
            from_ds = getattr(rel, "from_dataset", "")
            to_ds = getattr(rel, "to_dataset", "")
            role_alias = getattr(rel, "role_alias", None)
            is_role_playing = getattr(rel, "is_role_playing", False)

            from_alias = dataset_aliases.get(from_ds, from_ds)
            to_alias = role_alias if (is_role_playing and role_alias) else dataset_aliases.get(to_ds, to_ds)

            from_cols = [
                self.schema_manager._resolve_physical_column_name(ds_by_name.get(from_ds), c)
                if ds_by_name.get(from_ds) else self.identifier_sanitizer.sanitize_column(c)
                for c in rel.from_columns
            ]
            to_cols = [
                self.schema_manager._resolve_physical_column_name(ds_by_name.get(to_ds), c)
                if ds_by_name.get(to_ds) else self.identifier_sanitizer.sanitize_column(c)
                for c in (rel.to_columns or [])
            ]

            matched_line = None
            for rl in rel_lines:
                if f"AS {from_alias} " in rl and (f"REFERENCES {to_alias} " in rl or f"REFERENCES {to_alias}(" in rl or f"REFERENCES {to_alias}__BY_" in rl):
                    matched_line = rl
                    break

            if matched_line:
                for fc in from_cols:
                    if f'"{fc}"' not in matched_line and fc not in matched_line:
                        raise ValueError(
                            f"Pre-emit check failed: Relationship from '{from_ds}' to '{to_ds}' emitted columns "
                            f"differ from model bound from_column '{fc}'. Emitted: {matched_line.strip()}"
                        )
                for tc in to_cols:
                    if f'"{tc}"' not in matched_line and tc not in matched_line:
                        raise ValueError(
                            f"Pre-emit check failed: Relationship from '{from_ds}' to '{to_ds}' emitted columns "
                            f"differ from model bound to_column '{tc}'. Emitted: {matched_line.strip()}"
                        )

