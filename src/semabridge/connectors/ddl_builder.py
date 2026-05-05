"""Semantic view DDL orchestration for Snowflake emission."""

from __future__ import annotations

import re
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.intermediate.models import OSIDataset, OSIModel
from semabridge.sml.models import SMLDataset, SMLModel
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

logger = get_logger(__name__)


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
        translator: Any = None
    ) -> None:
        self.config = config
        self.behavior = behavior
        self.identifier_sanitizer = identifier_sanitizer
        self.live_schema_metadata = live_schema_metadata
        self.schema_manager = schema_manager
        self.dup_name_repo = dup_name_repo
        self.translator = translator
        
        # Modular components
        self.sanitizer = SemanticDDLSanitizer(identifier_sanitizer)
        self.snapshot_orchestrator = HistorySnapshotOrchestrator(identifier_sanitizer, schema_manager, config)
        self.relationships_builder = RelationshipsClauseBuilder(identifier_sanitizer, schema_manager, self.sanitizer)
        self.dimensions_builder = DimensionsClauseBuilder(
            identifier_sanitizer, schema_manager, self.sanitizer, translator, behavior
        )
        self.metrics_builder = MetricsClauseBuilder(
            identifier_sanitizer, schema_manager, self.sanitizer, translator, config, dup_name_repo
        )

    def generate_ddls(self, sml: SMLModel) -> list[str]:
        if not sml.datasets:
            return []

        logger.info(
            "generate_ddls: building semantic view for model=%s datasets=%s metrics=%s",
            sml.unique_name or sml.label or "<unnamed_sml_model>",
            len(getattr(sml, "datasets", []) or []),
            len(getattr(sml, "metrics", []) or []),
        )
        snapshot_ddls, source_overrides = self.snapshot_orchestrator.build_for_sml(sml)
        all_ddls = list(snapshot_ddls)

        original_sources: dict[str, str] = {}
        try:
            for dataset in sml.datasets:
                if dataset.unique_name in source_overrides:
                    original_sources[dataset.unique_name] = dataset.source_table
                    dataset.source_table = source_overrides[dataset.unique_name]

            semantic_ddl = self.sanitizer.sanitize_structure(self._generate_semantic_view(sml))

            self._guard_relationship_clause(
                model_name=sml.unique_name or sml.label or "model",
                relationships=getattr(sml, "relationships", []),
                semantic_ddl=semantic_ddl,
                fail_on_missing=bool(getattr(self.behavior.snowflake, "fail_on_missing_relationships", True)),
            )

            try:
                debug_file = Path("output/debug/debug_generated_sql.sql")
                debug_file.parent.mkdir(parents=True, exist_ok=True)
                debug_file.write_text(semantic_ddl, encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to persist debug DDL: %s", exc)
        finally:
            for dataset in sml.datasets:
                if dataset.unique_name in original_sources:
                    dataset.source_table = original_sources[dataset.unique_name]

        return [*all_ddls, semantic_ddl]

    def generate_ddls_from_osi(self, osi: OSIModel) -> list[str]:
        if not osi.datasets:
            return []

        snapshot_ddls, source_overrides = self.snapshot_orchestrator.build_for_osi(osi)
        all_ddls = list(snapshot_ddls)

        original_sources: dict[str, str] = {}
        try:
            for dataset in osi.datasets:
                if dataset.unique_name in source_overrides:
                    original_sources[dataset.unique_name] = dataset.source_table
                    dataset.source_table = source_overrides[dataset.unique_name]

            semantic_ddl = self.sanitizer.sanitize_structure(self._generate_semantic_view_from_osi(osi))

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
        tbuilder = TablesClauseBuilder(self.identifier_sanitizer, self.schema_manager, self.config, self.behavior, self.live_schema_metadata)
        tables_lines, declared_pk, rel_pk_map, ds_lookup, ds_by_name = tbuilder.build_for_sml(sml, registry, metric_counts, related_ds)
        if tables_lines: definitions.append("TABLES (\n" + ",\n".join(tables_lines) + "\n)")

        # RELATIONSHIPS
        rel_lines = self.relationships_builder.build_for_sml(sml, registry.dataset_aliases, ds_by_name, ds_lookup, declared_pk, rel_pk_map)
        if rel_lines: definitions.append("RELATIONSHIPS (\n" + ",\n".join(rel_lines) + "\n)")

        # DIMENSIONS
        measure_cols = self._collect_measure_columns(sml, ds_lookup)
        dims_lines = self.dimensions_builder.build_for_sml(sml, registry.dataset_aliases, ds_by_name, ds_lookup, measure_cols)
        dimensions_block_idx = None
        if dims_lines:
            dimensions_block_idx = len(definitions)
            definitions.append("DIMENSIONS (\n" + ",\n".join(dims_lines) + "\n)")

        # METRICS
        from semabridge.utils.identifier_normalizer import IdentifierNormalizer
        alias_by_raw = IdentifierNormalizer(self.identifier_sanitizer).build_alias_lookup(sml.datasets, registry.dataset_aliases)
        all_phys = {c for cols in ds_lookup.values() for c in cols}
        emittable = {self.identifier_sanitizer.sanitize_alias(m.unique_name) for m in sml.metrics if (m.source_column and m.aggregation) or m.sql_expression}
        
        metrics_lines = self.metrics_builder.build_for_sml(sml, registry.dataset_aliases, ds_by_name, ds_lookup, alias_by_raw, all_phys, emittable)
        
        # CROSS-CLAUSE DEDUPLICATION
        if dims_lines and metrics_lines and dimensions_block_idx is not None:
            metrics_lines, definitions[dimensions_block_idx] = self._deduplicate_cross_clause(metrics_lines, dims_lines, definitions[dimensions_block_idx])

        if metrics_lines: definitions.append("METRICS (\n" + ",\n".join(metrics_lines) + "\n)")

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
        tbuilder = TablesClauseBuilder(self.identifier_sanitizer, self.schema_manager, self.config, self.behavior, self.live_schema_metadata)
        tables_lines, declared_pk, rel_pk_map, ds_lookup, ds_by_name = tbuilder.build_for_osi(osi, registry, metric_counts, related_ds)
        if tables_lines: definitions.append("TABLES (\n" + ",\n".join(tables_lines) + "\n)")

        # RELATIONSHIPS
        rel_lines = self.relationships_builder.build_for_osi(osi, registry.dataset_aliases, ds_by_name, ds_lookup, declared_pk, rel_pk_map)
        if rel_lines: definitions.append("RELATIONSHIPS (\n" + ",\n".join(rel_lines) + "\n)")

        # DIMENSIONS
        measure_cols = self._collect_measure_columns(osi, ds_lookup)
        dims_lines = self.dimensions_builder.build_for_osi(osi, registry.dataset_aliases, ds_by_name, ds_lookup, measure_cols)
        dimensions_block_idx = None
        if dims_lines:
            dimensions_block_idx = len(definitions)
            definitions.append("DIMENSIONS (\n" + ",\n".join(dims_lines) + "\n)")

        # METRICS
        from semabridge.utils.identifier_normalizer import IdentifierNormalizer
        alias_by_raw = IdentifierNormalizer(self.identifier_sanitizer).build_alias_lookup(osi.datasets, registry.dataset_aliases)
        all_phys = {c for cols in ds_lookup.values() for c in cols}
        emittable = {self.identifier_sanitizer.sanitize_alias(m.unique_name) for m in osi.metrics if (m.source_column and m.aggregation) or getattr(m, "expression", None)}
        
        metrics_lines = self.metrics_builder.build_for_osi(osi, registry.dataset_aliases, ds_by_name, ds_lookup, alias_by_raw, all_phys, emittable)
        
        if dims_lines and metrics_lines and dimensions_block_idx is not None:
            metrics_lines, definitions[dimensions_block_idx] = self._deduplicate_cross_clause(metrics_lines, dims_lines, definitions[dimensions_block_idx])

        if metrics_lines: definitions.append("METRICS (\n" + ",\n".join(metrics_lines) + "\n)")

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

    def _deduplicate_cross_clause(self, metrics_lines: List[str], dims_lines: List[str], dims_block: str) -> Tuple[List[str], str]:
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
            if not getattr(rel, "is_active", True):
                continue
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
