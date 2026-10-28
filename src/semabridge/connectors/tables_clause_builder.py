"""Tables clause builder for Snowflake semantic views."""

from __future__ import annotations
from semabridge.utils.identifiers import inactive_alias

from typing import Any, Dict, List, Tuple, Set, Optional
import re

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

    def _is_uniqueness_guaranteed(
        self,
        dataset: Any,
        target_cols: list[str],
        relationships: list[Any],
        unqualified_table: str,
    ) -> bool:
        """Determine whether uniqueness of target_cols on dataset is guaranteed.

        Guaranteed if:
        1. Model column metadata explicitly marks all target columns with is_key=True.
        2. Snowflake schema metadata declares a primary key or unique key matching target_cols.
        3. Model relationships targeting this dataset on target_cols have 1:1 or Many-to-One
           cardinality (in Power BI / Tabular models, the 'One' side of 1:1 or M:1 is enforced
           unique by the engine).
        4. Or verified by schema_manager data checks if available.
        """
        # 1. Column metadata
        cols = getattr(dataset, "columns", []) or []
        col_map = {str(c.unique_name).upper(): c for c in cols}
        matched = [col_map[c.upper()] for c in target_cols if c.upper() in col_map]
        if len(matched) == len(target_cols) and all(getattr(c, "is_key", False) for c in matched):
            return True

        # 2. Snowflake schema metadata
        if hasattr(self.schema_manager, "declared_primary_keys"):
            pks = self.schema_manager.declared_primary_keys(cursor=self.cursor).get(
                str(unqualified_table).strip('"').upper(), []
            )
            if pks and [p.upper() for p in pks] == [c.upper() for c in target_cols]:
                return True

        # 3. Model relationship cardinality
        target_cols_upper = [c.upper() for c in target_cols]
        target_ds_names = {
            getattr(dataset, "unique_name", None),
            getattr(dataset, "name", None),
        } - {None}

        for rel in relationships:
            card = getattr(rel, "cardinality", None)
            card_str = str(getattr(card, "value", card) or "").lower().replace("_", "-")

            # Check if this rel targets dataset on target_cols
            if getattr(rel, "to_dataset", None) in target_ds_names:
                rel_to_cols = [
                    self.schema_manager._resolve_physical_column_name(dataset, c, model=self._current_model).upper()
                    if hasattr(self.schema_manager, "_resolve_physical_column_name")
                    else self.identifier_sanitizer.sanitize_column(c).upper()
                    for c in (getattr(rel, "to_columns", None) or [])
                ]
                if rel_to_cols == target_cols_upper:
                    if card_str in ("1:1", "one-to-one", "onetoone"):
                        return True
                    if card_str in ("m:1", "many-to-one", "manytoone"):
                        return True

            # Check if 1:1 where dataset is the from_dataset
            if getattr(rel, "from_dataset", None) in target_ds_names:
                rel_from_cols = [
                    self.schema_manager._resolve_physical_column_name(dataset, c, model=self._current_model).upper()
                    if hasattr(self.schema_manager, "_resolve_physical_column_name")
                    else self.identifier_sanitizer.sanitize_column(c).upper()
                    for c in (getattr(rel, "from_columns", None) or [])
                ]
                if rel_from_cols == target_cols_upper:
                    if card_str in ("1:1", "one-to-one", "onetoone"):
                        return True
                    if card_str in ("1:m", "one-to-many", "onetomany"):
                        return True

        return False

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

        target_keys_by_dataset: dict[str, list[list[str]]] = {}
        for rel in relationships:
            if rel.to_dataset and rel.to_columns:
                if rel.to_dataset not in target_keys_by_dataset:
                    target_keys_by_dataset[rel.to_dataset] = []
                col_list = [c for c in rel.to_columns]
                if col_list not in target_keys_by_dataset[rel.to_dataset]:
                    target_keys_by_dataset[rel.to_dataset].append(col_list)

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

            def _physical(col_name: str) -> str:
                return self.schema_manager._resolve_physical_column_name(dataset, col_name, model=self._current_model)

            # Resolve target keys for this dataset to physical column names
            raw_target_keys = target_keys_by_dataset.get(dataset.unique_name, [])
            resolved_target_keys: list[list[str]] = []
            for raw_key in raw_target_keys:
                resolved_cols = [_physical(c) for c in raw_key]
                if not known_phys or all(c in known_phys for c in resolved_cols):
                    if resolved_cols not in resolved_target_keys:
                        resolved_target_keys.append(resolved_cols)

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

            # Resolve primary key
            pk_cols: list[str] = []

            if is_measure_only:
                pk_cols = []
            elif resolved_target_keys:
                # Find the first target key whose uniqueness is guaranteed
                for tkey in resolved_target_keys:
                    if self._is_uniqueness_guaranteed(dataset, tkey, relationships, unqualified_table):
                        pk_cols = [f'"{c}"' for c in tkey]
                        break

                if not pk_cols:
                    if is_fact_table:
                        # Safeguard: unreferenced or unverified fact table keys are never guessed
                        pk_cols = []
                    else:
                        # Dimension table fallback
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
                if is_fact_table:
                    # Safeguard: fact table not targeted by any relationship has PK suppressed
                    pk_cols = []
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

            # Verification: only retain columns that actually exist in the physical table
            verified_pk = [p for p in pk_cols if p.strip('"') in known_phys] if known_phys else pk_cols

            # Resolve secondary UNIQUE constraints for remaining target keys
            unique_clauses: list[str] = []
            verified_pk_upper = [p.strip('"').upper() for p in verified_pk]
            declared_unique_keys: list[list[str]] = []

            for tkey in resolved_target_keys:
                tkey_upper = [c.upper() for c in tkey]
                if tkey_upper == verified_pk_upper:
                    continue
                if any(tkey_upper == [c.upper() for c in u] for u in declared_unique_keys):
                    continue
                if self._is_uniqueness_guaranteed(dataset, tkey, relationships, unqualified_table):
                    u_cols = ", ".join(f'"{c}"' for c in tkey)
                    unique_clauses.append(f"UNIQUE ({u_cols})")
                    declared_unique_keys.append(tkey)
                else:
                    import logging as _log
                    _log.getLogger(__name__).warning(
                        "Unresolved key constraint for relationship target '%s' on %s: uniqueness cannot be guaranteed.",
                        dataset.unique_name, tkey
                    )

            if verified_pk:
                declared_pk_by_alias[alias] = [c.strip('"') for c in verified_pk]
                for c in verified_pk:
                    relationship_target_alias[(dataset.unique_name, c.strip('"').upper())] = alias
                if hasattr(self.schema_manager, "warn_if_key_not_unique"):
                    self.schema_manager.warn_if_key_not_unique(
                        unqualified_table, [c.strip('"') for c in verified_pk], cursor=self.cursor
                    )

            for ukey in declared_unique_keys:
                for c in ukey:
                    relationship_target_alias[(dataset.unique_name, c.upper())] = alias

            pk_clause = f"PRIMARY KEY ({', '.join(verified_pk)})" if verified_pk else ""
            constraints = [c for c in [pk_clause, *unique_clauses] if c]
            pk_suffix = f" {' '.join(constraints)}" if constraints else ""

            if getattr(self.behavior.snowflake, "auto_add_anchors", True) and (dataset.is_fact or is_measure_only or is_fact_like_name(dataset.unique_name)):
                full_table = self._build_source_query_with_anchors(full_table, dataset.unique_name, self._current_model)

            tables_lines.append(f'  {alias} AS {full_table}{pk_suffix}')

        # Emit alias tables for inactive relationships
        for rel in relationships:
            if not getattr(rel, "is_active", True):
                dim_ds_name = getattr(rel, "to_dataset", None)
                dim_dataset = dataset_by_name.get(dim_ds_name)
                from_cols = getattr(rel, "from_columns", None) or []
                fk_col = from_cols[0] if from_cols else ""
                if dim_dataset and fk_col:
                    inact_alias = inactive_alias(dim_ds_name, fk_col, uppercase=True)
                    modeled_cols_list = [c.unique_name for c in getattr(dim_dataset, "columns", []) or []]
                    source_table = resolve_dataset_to_physical_table(
                        dim_dataset.unique_name,
                        modeled_cols_list,
                        self.live_schema_metadata,
                        explicit_mapping=source_table_mapping,
                        model_name=getattr(self._current_model, "unique_name", None) or getattr(self._current_model, "name", None),
                    )
                    _unq = source_table.rsplit(".", 1)[-1] if "." in source_table else source_table
                    safe_dim_table = self.identifier_sanitizer.sanitize_table_name(_unq)
                    dim_full_table = f'"{self.config.database}"."{self.config.schema_name}"."{safe_dim_table}"'

                    dim_alias = self._get_unique_alias(dim_dataset.unique_name, registry)
                    dim_pks = declared_pk_by_alias.get(dim_alias, [])
                    pk_cols_str = ", ".join(f'"{c}"' for c in dim_pks)
                    dim_pk_suffix = f" PRIMARY KEY ({pk_cols_str})" if dim_pks else ""

                    line = f'  {inact_alias} AS {dim_full_table}{dim_pk_suffix}'
                    if line not in tables_lines:
                        tables_lines.append(line)
                        declared_pk_by_alias[inact_alias] = dim_pks
                        registry.register_dataset_alias(inact_alias, inact_alias)
                        registry.register_dataset_alias(inact_alias.lower(), inact_alias)
                        registry.used_table_aliases.add(inact_alias)
                        dim_cols = dataset_col_lookup.get(dim_dataset.unique_name, set())
                        dataset_col_lookup[inact_alias] = set(dim_cols)
                        dataset_col_lookup[inact_alias.lower()] = set(dim_cols)

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
