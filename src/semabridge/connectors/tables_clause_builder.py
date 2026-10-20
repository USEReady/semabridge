"""Tables clause builder for Snowflake semantic views."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Set, Optional

from semabridge.converter.date_resolution import DateResolutionConfig
from semabridge.connectors.fact_table_naming import is_fact_like_name
from semabridge.connectors.sql_guard import validate_safe_sql
from semabridge.connectors.table_binding_resolver import resolve_dataset_to_physical_table


def resolve_source_table_mapping(
    behavior_mapping: Optional[Dict[str, str]],
    enriched_view_mapping: Optional[Dict[str, str]],
    failed_enrichment_datasets: Optional[Set[str]] = None,
) -> Dict[str, str]:
    """Merge an explicit behavior-config source_table_mapping with the
    emitter's own enriched-view redirect mapping (dataset -> "*_ENRICHED"
    view name, recorded when auto-enrichment succeeds).

    Runtime enrichment status always wins: an entry pointing to an enriched view
    (e.g., ending with "_ENRICHED" or containing "_ENRICHED") is used ONLY when enrichment for
    that dataset succeeded in this run. If a dataset failed enrichment in this run
    (or is recorded in failed_enrichment_datasets), any mapping to an enriched view
    is ignored so the dataset cleanly falls back to its base table.
    """
    class _CaseFoldMappingDict(dict):
        def get(self, key, default=None):
            if super().__contains__(key):
                return super().get(key)
            if isinstance(key, str):
                k_fold = key.casefold()
                for k, v in self.items():
                    if isinstance(k, str) and k.casefold() == k_fold:
                        return v
            return default

        def __getitem__(self, key):
            val = self.get(key)
            if val is not None:
                return val
            raise KeyError(key)

        def __contains__(self, key):
            if super().__contains__(key):
                return True
            if isinstance(key, str):
                k_fold = key.casefold()
                return any(isinstance(k, str) and k.casefold() == k_fold for k in self.keys())
            return False

    merged: Dict[str, str] = _CaseFoldMappingDict()
    failed_set = {s.casefold() for s in (failed_enrichment_datasets or set())}

    if enriched_view_mapping:
        for k, v in enriched_view_mapping.items():
            if k.casefold() not in failed_set:
                merged[k] = v
                merged[k.casefold()] = v
                merged[k.upper()] = v

    if behavior_mapping:
        for k, v in behavior_mapping.items():
            k_fold = k.casefold()
            is_enriched_target = bool(v and (v.upper().endswith("_ENRICHED") or "_ENRICHED" in v.upper()))
            if is_enriched_target and k_fold in failed_set:
                continue
            if is_enriched_target and enriched_view_mapping is not None and k_fold not in {x.casefold() for x in enriched_view_mapping}:
                continue
            merged[k] = v
            merged[k_fold] = v
            merged[k.upper()] = v
    return merged



class TablesClauseBuilder:
    def __init__(
        self,
        identifier_sanitizer: Any,
        schema_manager: Any,
        config: Any,
        behavior: Any,
        live_schema_metadata: dict[str, set[str]],
        cursor: Any = None,
        enriched_view_mapping: Optional[Dict[str, str]] = None,
        failed_enrichment_datasets: Optional[Set[str]] = None,
    ) -> None:
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.config = config
        self.behavior = behavior
        self.live_schema_metadata = live_schema_metadata
        self.cursor = cursor
        self.enriched_view_mapping: Dict[str, str] = (
            enriched_view_mapping if enriched_view_mapping is not None else {}
        )
        self.failed_enrichment_datasets: Set[str] = (
            failed_enrichment_datasets if failed_enrichment_datasets is not None else set()
        )
        self._anchor_literal_cache: dict[tuple, Optional[str]] = {}

    def _find_date_table(self, model: Any) -> Optional[Tuple[str, str, str]]:
        """
        Dynamically find the date/calendar table in the model.
        Returns (table_name, date_column, fiscal_period_column) or None.
        No hardcoding!
        """
        resolution = DateResolutionConfig().resolve(model)
        if not resolution:
            return None

        # Use fiscal period column if it exists; otherwise fall back to date column
        return (resolution.table, resolution.date_col, resolution.monthindex_col or resolution.date_col)

    def _build_source_query_with_anchors(self, source_fq: str, fact_table: str, model: Any) -> str:
        """
        Automatically inject anchors using dynamically detected date table.
        No hardcoded table names!
        """
        date_info = self._find_date_table(model)
        
        if not date_info:
            return source_fq

        date_table, date_col, fiscal_col = date_info
        source_table_mapping = resolve_source_table_mapping(
            getattr(self.behavior.snowflake, "source_table_mapping", None),
            self.enriched_view_mapping,
            self.failed_enrichment_datasets,
        )

        date_dataset = next(
            (d for d in getattr(model, "datasets", []) or [] if d.unique_name == date_table),
            None,
        )

        if date_dataset:
            resolved_source_table = source_table_mapping.get(
                date_dataset.unique_name,
                date_dataset.source_table or date_dataset.unique_name,
            )
            _unq = resolved_source_table.rsplit(".", 1)[-1] if "." in resolved_source_table else resolved_source_table
            safe_table = self.identifier_sanitizer.sanitize_table_name(_unq)
            date_table_ref = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
            resolved_date_col = self.schema_manager._resolve_physical_column_name(date_dataset, date_col, model=model)
            resolved_fiscal_col = self.schema_manager._resolve_physical_column_name(date_dataset, fiscal_col, model=model)
        else:
            _unq_dt = date_table.rsplit(".", 1)[-1] if "." in date_table else date_table
            safe_table = self.identifier_sanitizer.sanitize_table_name(_unq_dt)
            date_table_ref = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'
            resolved_date_col = self.identifier_sanitizer.sanitize_column(date_col)
            resolved_fiscal_col = self.identifier_sanitizer.sanitize_column(fiscal_col)

        # Guard: only inject the anchor when the fiscal column is confirmed to
        # exist in the live Snowflake schema.  If the calendar table doesn't
        # have MONTHINDEX (or whichever column was resolved), emitting a
        # subquery that references it produces Snowflake error 000904 inside a
        # multi-line TABLE subquery — a position the DDL sanitizer cannot reach.
        if self.live_schema_metadata:
            # live_schema_metadata keys may be bare table names or fully-qualified;
            # try bare sanitized name first, then the full FQ path.
            _live_cols = (
                self.live_schema_metadata.get(safe_table)
                or self.live_schema_metadata.get(safe_table.upper())
                or self.live_schema_metadata.get(date_table)
                or self.live_schema_metadata.get(date_table.upper())
                or set()
            )
            if _live_cols and resolved_fiscal_col not in _live_cols:
                # Fiscal column confirmed absent from live schema — skip anchor.
                import logging as _log
                _log.getLogger(__name__).debug(
                    "Skipping _CURRENT_FISCAL_PERIOD anchor: column '%s' not found "
                    "in live Snowflake schema for table '%s'.",
                    resolved_fiscal_col, safe_table,
                )
                return source_fq

        # Anchors (_CURRENT_FISCAL_PERIOD, MAX_DATE) are computed inside the
        # fact's enriched view (see SnowflakeEmitter._create_enriched_view),
        # never fetched from table data at sync time and spliced in here.
        return source_fq

    def build_for_sml(
        self,
        sml: Any,
        registry: Any,
        metric_counts_by_dataset: dict[str, int],
        related_datasets: set[str],
    ) -> Tuple[List[str], Dict[str, List[str]], Dict[tuple, str], Dict[str, set[str]], Dict[str, Any], Dict[str, set[str]]]:
        self._current_model = sml
        return self._build(sml.datasets, sml.relationships, registry, metric_counts_by_dataset, related_datasets, is_osi=False)

    def build_for_osi(
        self,
        osi: Any,
        registry: Any,
        metric_counts_by_dataset: dict[str, int],
        related_datasets: set[str],
    ) -> Tuple[List[str], Dict[str, List[str]], Dict[tuple, str], Dict[str, set[str]], Dict[str, Any], Dict[str, set[str]]]:
        self._current_model = osi
        return self._build(osi.datasets, osi.relationships, registry, metric_counts_by_dataset, related_datasets, is_osi=True)

    def _build(
        self,
        datasets: List[Any],
        relationships: List[Any],
        registry: Any,
        metric_counts_by_dataset: dict[str, int],
        related_datasets: set[str],
        is_osi: bool
    ) -> Tuple[List[str], Dict[str, List[str]], Dict[tuple, str], Dict[str, set[str]], Dict[str, Any], Dict[str, set[str]]]:
        tables_lines: List[str] = []
        relationship_target_alias: dict[tuple[str, str], str] = {}
        declared_pk_by_alias: dict[str, list[str]] = {}

        datasets = [
            d for d in datasets
            if not str(d.unique_name or "").startswith(("LocalDateTable_", "DateTableTemplate_"))
        ]
        relationships = [
            r for r in relationships
            if not (
                str(getattr(r, "from_dataset", "") or "").startswith(("LocalDateTable_", "DateTableTemplate_"))
                or str(getattr(r, "to_dataset", "") or "").startswith(("LocalDateTable_", "DateTableTemplate_"))
            )
        ]

        many_side_datasets = {rel.from_dataset for rel in relationships if getattr(rel, "from_dataset", None)}
        one_side_datasets = {rel.to_dataset for rel in relationships if getattr(rel, "to_dataset", None)}

        relationship_pk_map = {}
        for rel in relationships:
            if rel.is_active and rel.to_dataset and rel.to_columns:
                if rel.to_dataset not in relationship_pk_map:
                    relationship_pk_map[rel.to_dataset] = []
                for col in rel.to_columns:
                    if col not in relationship_pk_map[rel.to_dataset]:
                        relationship_pk_map[rel.to_dataset].append(col)

        dataset_col_lookup: dict[str, set[str]] = {}
        # live_col_lookup contains ONLY confirmed-live columns from Snowflake schema
        # metadata.  Unlike dataset_col_lookup it is NEVER populated with modeled_cols
        # as a fallback, so downstream code can use it to distinguish "physically
        # confirmed" from "modelled assumption".  Empty entry = schema unknown.
        live_col_lookup: dict[str, set[str]] = {}
        dataset_by_name: dict[str, Any] = {d.unique_name: d for d in datasets}
        source_table_mapping = resolve_source_table_mapping(
            getattr(self.behavior.snowflake, "source_table_mapping", None),
            self.enriched_view_mapping,
            self.failed_enrichment_datasets,
        )
        for dataset in datasets:
            if is_osi:
                modeled_cols = {self.identifier_sanitizer.sanitize_column(c.unique_name) for c in dataset.columns}
            else:
                modeled_cols = set(self.schema_manager._collect_physical_source_columns(dataset).keys()) | {
                    self.identifier_sanitizer.sanitize_column(c.unique_name) for c in getattr(dataset, "columns", []) or []
                }

            modeled_cols_list = [c.unique_name for c in getattr(dataset, "columns", []) or []]
            source_table = resolve_dataset_to_physical_table(
                dataset.unique_name,
                modeled_cols_list,
                self.live_schema_metadata,
                explicit_mapping=source_table_mapping,
                model_name=getattr(self._current_model, "unique_name", None) or getattr(self._current_model, "name", None),
            )
            _unq_src = source_table.rsplit(".", 1)[-1] if "." in source_table else source_table
            safe_source_key = self.identifier_sanitizer.sanitize_table_name(_unq_src)
            source_key = safe_source_key.upper()
            live_cols = (
                self.live_schema_metadata.get(source_key)
                or self.live_schema_metadata.get(safe_source_key)
                or self.live_schema_metadata.get(_unq_src.upper())
                or self.live_schema_metadata.get(_unq_src)
                or set()
            )
            dataset_col_lookup[dataset.unique_name] = (set(live_cols) | set(modeled_cols)) if live_cols else modeled_cols
            if live_cols:
                live_col_lookup[dataset.unique_name] = set(live_cols)

        for dataset in datasets:
            modeled_cols_list = [c.unique_name for c in getattr(dataset, "columns", []) or []]
            source_table = resolve_dataset_to_physical_table(
                dataset.unique_name,
                modeled_cols_list,
                self.live_schema_metadata,
                explicit_mapping=source_table_mapping,
                model_name=getattr(self._current_model, "unique_name", None) or getattr(self._current_model, "name", None),
            )
            unqualified_table = source_table.rsplit(".", 1)[-1] if "." in source_table else source_table
            safe_table = self.identifier_sanitizer.sanitize_table_name(unqualified_table)
            full_table = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'

            alias = self._get_unique_alias(dataset.unique_name, registry)

            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            relationship_pk_cols: list[str] = []
            if dataset.unique_name in relationship_pk_map:
                for rel_col in relationship_pk_map[dataset.unique_name]:
                    resolved = self.schema_manager._resolve_physical_column_name(dataset, rel_col, model=self._current_model)
                    if not known_phys or resolved in known_phys:
                        if resolved not in relationship_pk_cols:
                            relationship_pk_cols.append(resolved)

            is_measure_only = (
                metric_counts_by_dataset.get(dataset.unique_name, 0) > 0
                and dataset.unique_name not in related_datasets
                and not any(getattr(c, "is_key", False) for c in dataset.columns)
                and dataset.unique_name.upper() != "KPI"
            )

            # Facts and keys come from the PBIX model and the Snowflake schema
            # only, never from table data: a fact is on the many side of the
            # model's relationships (or flagged/named as one). A key is the
            # one-side column of a relationship, else the PBIX key column,
            # else a primary key declared in Snowflake, else the first column.
            is_on_many_side = (dataset.unique_name in many_side_datasets and dataset.unique_name not in one_side_datasets)

            is_fact_table = (
                getattr(dataset, "is_fact", False)
                or is_fact_like_name(dataset.unique_name)
                or getattr(dataset, "table_type", "").upper() == "FACT"
                or is_on_many_side
            )

            def _physical(col_name: str) -> str:
                return self.schema_manager._resolve_physical_column_name(dataset, col_name, model=self._current_model)

            if is_measure_only or is_fact_table:
                pk_cols = []
            elif relationship_pk_cols:
                pk_cols = [f'"{relationship_pk_cols[0]}"']
            else:
                key_cols = [c for c in dataset.columns if c.is_key]
                declared = []
                if not key_cols and hasattr(self.schema_manager, "declared_primary_keys"):
                    declared = self.schema_manager.declared_primary_keys(cursor=self.cursor).get(
                        str(unqualified_table).strip('"').upper(), []
                    )
                if key_cols:
                    pk_cols = [f'"{_physical(key_cols[0].unique_name)}"']
                elif declared:
                    pk_cols = [f'"{c}"' for c in declared]
                elif dataset.columns:
                    pk_cols = [f'"{_physical(dataset.columns[0].unique_name)}"']
                else:
                    pk_cols = []

            # Verification: only retain columns that actually exist in the physical table
            verified_pk = [p for p in pk_cols if p.strip('"') in known_phys]


            if verified_pk:
                declared_pk_by_alias[alias] = [c.strip('"') for c in verified_pk]
                relationship_target_alias[(dataset.unique_name, verified_pk[0].strip('"').upper())] = alias
                if hasattr(self.schema_manager, "warn_if_key_not_unique"):
                    self.schema_manager.warn_if_key_not_unique(
                        unqualified_table, [c.strip('"') for c in verified_pk], cursor=self.cursor
                    )

            pk_clause = f"PRIMARY KEY ({', '.join(verified_pk)})" if verified_pk else ""
            
            if getattr(self.behavior.snowflake, "auto_add_anchors", True) and (dataset.is_fact or is_measure_only or is_fact_like_name(dataset.unique_name)):
                full_table = self._build_source_query_with_anchors(full_table, dataset.unique_name, self._current_model)

            pk_suffix = f" {pk_clause}" if pk_clause else ""
            tables_lines.append(f'  {alias} AS {full_table}{pk_suffix}')

            for rel_pk in relationship_pk_cols[1:]:
                rel_alias = self._get_unique_alias(f"{dataset.unique_name}__BY_{rel_pk}", registry)
                tables_lines.append(f'  {rel_alias} AS {full_table} PRIMARY KEY ("{rel_pk}")')
                declared_pk_by_alias[rel_alias] = [rel_pk]
                relationship_target_alias[(dataset.unique_name, rel_pk.upper())] = rel_alias

        return tables_lines, declared_pk_by_alias, relationship_target_alias, dataset_col_lookup, dataset_by_name, live_col_lookup

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
