"""Tables clause builder for Snowflake semantic views."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Set, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TablesClauseBuilder:
    def __init__(
        self,
        identifier_sanitizer: Any,
        schema_manager: Any,
        config: Any,
        behavior: Any,
        live_schema_metadata: dict[str, set[str]],
    ) -> None:
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.config = config
        self.behavior = behavior
        self.live_schema_metadata = live_schema_metadata

    def build_for_sml(
        self,
        sml: Any,
        registry: Any,
        metric_counts_by_dataset: dict[str, int],
        related_datasets: set[str],
    ) -> Tuple[List[str], Dict[str, List[str]], Dict[tuple, str], Dict[str, set[str]], Dict[str, Any]]:
        return self._build(sml.datasets, sml.relationships, registry, metric_counts_by_dataset, related_datasets, is_osi=False)

    def build_for_osi(
        self,
        osi: Any,
        registry: Any,
        metric_counts_by_dataset: dict[str, int],
        related_datasets: set[str],
    ) -> Tuple[List[str], Dict[str, List[str]], Dict[tuple, str], Dict[str, set[str]], Dict[str, Any]]:
        return self._build(osi.datasets, osi.relationships, registry, metric_counts_by_dataset, related_datasets, is_osi=True)

    def _build(
        self,
        datasets: List[Any],
        relationships: List[Any],
        registry: Any,
        metric_counts_by_dataset: dict[str, int],
        related_datasets: set[str],
        is_osi: bool
    ) -> Tuple[List[str], Dict[str, List[str]], Dict[tuple, str], Dict[str, set[str]], Dict[str, Any]]:
        tables_lines: List[str] = []
        relationship_target_alias: dict[tuple[str, str], str] = {}
        declared_pk_by_alias: dict[str, list[str]] = {}

        # Only active relationships drive PK derivation and bridge detection.
        # Inactive rels (USERELATIONSHIP alternates) are skipped in the
        # RELATIONSHIPS clause and must not influence PK assignment either.
        active_relationships = [r for r in relationships if getattr(r, "is_active", True)]

        relationship_pk_map = {}
        for rel in active_relationships:
            if rel.to_dataset and rel.to_columns:
                if rel.to_dataset not in relationship_pk_map:
                    relationship_pk_map[rel.to_dataset] = []
                for col in rel.to_columns:
                    if col not in relationship_pk_map[rel.to_dataset]:
                        relationship_pk_map[rel.to_dataset].append(col)

        # Bridge tables appear as both a FK source (from_dataset) and a PK target
        # (to_dataset) in active relationships.  Their join columns are not unique,
        # so assigning a PRIMARY KEY would be incorrect and could mislead Snowflake's
        # query planner.  We suppress PK emission for these tables.
        active_from_datasets = {r.from_dataset for r in active_relationships}
        active_to_datasets = {r.to_dataset for r in active_relationships}
        bridge_datasets = active_from_datasets & active_to_datasets

        dataset_col_lookup: dict[str, set[str]] = {}
        dataset_by_name: dict[str, Any] = {d.unique_name: d for d in datasets}
        for dataset in datasets:
            if is_osi:
                modeled_cols = {self.identifier_sanitizer.sanitize_column(c.unique_name) for c in dataset.columns}
            else:
                modeled_cols = set(self.schema_manager._collect_physical_source_columns(dataset).keys())
            source_table = dataset.source_table or dataset.unique_name
            if str(source_table).upper().endswith("_SEMABRIDGE_FISCAL"):
                modeled_cols.add("_CURRENT_FISCAL_PERIOD")
            
            source_key = self.identifier_sanitizer.sanitize_table_name(source_table).upper()
            live_cols = self.live_schema_metadata.get(source_key, set())
            dataset_col_lookup[dataset.unique_name] = set(live_cols) if live_cols else modeled_cols

        for dataset in datasets:
            source_table = dataset.source_table or dataset.unique_name
            safe_table = self.identifier_sanitizer.sanitize_table_name(source_table)
            full_table = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'

            alias = self._get_unique_alias(dataset.unique_name, registry)
            is_bridge_table = dataset.unique_name in bridge_datasets

            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            relationship_pk_cols: list[str] = []
            if dataset.unique_name in relationship_pk_map:
                for rel_col in relationship_pk_map[dataset.unique_name]:
                    resolved = self.identifier_sanitizer.sanitize_column(rel_col) if is_osi else self.schema_manager._resolve_physical_column_name(dataset, rel_col)
                    if not known_phys or resolved in known_phys:
                        if resolved not in relationship_pk_cols:
                            relationship_pk_cols.append(resolved)

            is_measure_only = (
                metric_counts_by_dataset.get(dataset.unique_name, 0) > 0
                and dataset.unique_name not in related_datasets
                and not any(getattr(c, "is_key", False) for c in dataset.columns)
            )

            if is_measure_only:
                pk_cols = []
            elif is_bridge_table:
                # Bridge table: columns are not unique (they appear as both FK and PK
                # endpoints), so no PRIMARY KEY should be declared.
                pk_cols = []
                logger.info(
                    "Suppressing PRIMARY KEY for bridge table '%s': "
                    "it participates as both source and target in active relationships.",
                    dataset.unique_name,
                )
            elif relationship_pk_cols:
                pk_cols = [f'"{relationship_pk_cols[0]}"']
            else:
                key_cols = [c for c in dataset.columns if c.is_key]
                if key_cols:
                    res_col = self.identifier_sanitizer.sanitize_column(key_cols[0].unique_name) if is_osi else self.schema_manager._resolve_physical_column_name(dataset, key_cols[0].unique_name)
                    pk_cols = [f'"{res_col}"']
                else:
                    if getattr(self.behavior.snowflake, "pk_resolution_mode", None) == "strict":
                        raise ValueError(f"No PK found for {dataset.unique_name}")
                    col_name = dataset.columns[0].unique_name if dataset.columns else "ID"
                    pk_cols = [f'"{self.identifier_sanitizer.sanitize_column(col_name)}"']

            # Verification: use case-insensitive matching so relationship-derived PKs
            # (e.g. "Customer") are not silently dropped when live schema metadata
            # stores column names in a different case (e.g. "CUSTOMER").
            # Falling back to sorted(known_phys)[0] was the root cause of the bug
            # where CITY (alphabetically first) replaced CUSTOMER as the PK.
            if is_bridge_table:
                verified_pk = []
                pk_clause = ""
                tables_lines.append(f'  {alias} AS {full_table} {pk_clause}')
                continue
            known_phys_upper = {c.upper() for c in known_phys}
            verified_pk = [p for p in pk_cols if not known_phys or p.strip('"').upper() in known_phys_upper]
            if not verified_pk and known_phys:
                # Last resort: prefer the relationship-derived PK if it exists in the
                # live schema (case-insensitive), otherwise fall back to alphabetical.
                rel_pk_candidates = [
                    p for p in pk_cols
                    if p.strip('"').upper() in known_phys_upper
                ]
                if rel_pk_candidates:
                    verified_pk = rel_pk_candidates
                else:
                    # Resolve the canonical casing from live schema for the fallback
                    first_phys = sorted(known_phys, key=str.upper)[0]
                    verified_pk = [f'"{first_phys}"']

            if verified_pk:
                declared_pk_by_alias[alias] = [c.strip('"') for c in verified_pk]
                relationship_target_alias[(dataset.unique_name, verified_pk[0].strip('"').upper())] = alias

            pk_clause = f"PRIMARY KEY ({', '.join(verified_pk)})" if verified_pk else ""
            tables_lines.append(f'  {alias} AS {full_table} {pk_clause}')

            for rel_pk in relationship_pk_cols[1:]:
                rel_alias = self._get_unique_alias(f"{dataset.unique_name}__BY_{rel_pk}", registry)
                tables_lines.append(f'  {rel_alias} AS {full_table} PRIMARY KEY ("{rel_pk}")')
                declared_pk_by_alias[rel_alias] = [rel_pk]
                relationship_target_alias[(dataset.unique_name, rel_pk.upper())] = rel_alias

        return tables_lines, declared_pk_by_alias, relationship_target_alias, dataset_col_lookup, dataset_by_name

    def _get_unique_alias(self, name: str, registry: Any) -> str:
        alias = registry.get_alias(name)
        if not alias:
            alias = self.identifier_sanitizer.sanitize_alias(name)
            if alias in registry.used_table_aliases:
                idx = 2
                while True:
                    cand = f"{alias}_{idx}"
                    if cand not in registry.used_table_aliases:
                        alias = cand
                        break
                    idx += 1
            registry.used_table_aliases.add(alias)
            registry.register_dataset_alias(name, alias)
        return alias
