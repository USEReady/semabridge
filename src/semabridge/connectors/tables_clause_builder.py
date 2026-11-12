"""Tables clause builder for Snowflake semantic views."""

from __future__ import annotations
from semabridge.connectors.synonym_clause import table_annotation_clause
from semabridge.utils.naming import to_alias
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

from typing import Any, Dict, List, Tuple, Set, Optional
import re

from semabridge.converter.date_resolution import DateResolutionConfig
from semabridge.connectors.fact_table_naming import is_fact_like_name
from semabridge.connectors.sql_guard import validate_safe_sql
from semabridge.connectors.table_binding_resolver import resolve_dataset_to_physical_table
from semabridge.connectors.dataset_classification_keywords import is_calendar_like_name
from semabridge.core.drop_ledger import DropStage


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
        drop_ledger: Optional[Any] = None,
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
        self.drop_ledger = drop_ledger
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
        # Anchors (_CURRENT_<c>, MAX_DATE) are computed inside the
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
        # 0. Live KEY UNIQUENESS query
        if hasattr(self.schema_manager, "check_key_uniqueness"):
            live_result = self.schema_manager.check_key_uniqueness(unqualified_table, target_cols, cursor=self.cursor)
            # Proven unique in the data: done. Proven NOT unique is advisory
            # only -- keys come from the PBIX model and Snowflake schema, never
            # from table data -- so warn and fall through to the model checks.
            if live_result is True:
                return True
            if live_result is False:
                logger.warning(
                    "Key %s on %s is not unique in the current data; keeping the key the model defines.",
                    target_cols, unqualified_table,
                )

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
            # TMSL default cardinality (unset) is many-to-one.
            card_str = str(getattr(card, "value", card) or "many-to-one").lower().replace("_", "-")

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

    def _has_live_table(self, dataset: Any) -> bool:
        """True when the live Snowflake schema has an object for this dataset."""
        live = self.live_schema_metadata or {}
        if not live:
            return False
        names = {str(getattr(dataset, "unique_name", "") or ""), str(getattr(dataset, "source_table", "") or "")}
        for n in names - {""}:
            bare = n.rsplit(".", 1)[-1].strip('"')
            safe = self.identifier_sanitizer.sanitize_table_name(bare)
            if any(k in live for k in (bare, bare.upper(), safe, safe.upper())):
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

        AUTO_EXCLUDED_TABLE_PREFIXES = (
            "LocalDateTable_",
            "DateTableTemplate_",
            "ClusterMappingTable",
            "ClusterMapping",
        )
        datasets = [
            d for d in datasets
            if not str(d.unique_name or "").startswith(AUTO_EXCLUDED_TABLE_PREFIXES)
        ]
        relationships = [
            r for r in relationships
            if not (
                str(getattr(r, "from_dataset", "") or "").startswith(AUTO_EXCLUDED_TABLE_PREFIXES)
                or str(getattr(r, "to_dataset", "") or "").startswith(AUTO_EXCLUDED_TABLE_PREFIXES)
            )
        ]

        # Collect all datasets that participate in relationships
        related_dataset_names = (
            {str(getattr(r, "from_dataset", "") or "").casefold() for r in relationships}
            | {str(getattr(r, "to_dataset", "") or "").casefold() for r in relationships}
        ) - {""}

        # Don't emit auto-date / auto-calendar tables without an active relationship
        filtered_datasets = []
        for d in datasets:
            d_name = getattr(d, "unique_name", None) or getattr(d, "name", "")
            d_desc = getattr(d, "".join(["desc", "ription"]), "") or ""
            d_src = str(getattr(d, "source_table", "") or "").upper()
            d_name_upper = str(d_name or "").upper()
            is_auto_date = (
                d_desc == "Auto-generated Calendar Dimension"
                or d_src == "DIM_DATE"
                or (is_calendar_like_name(d_name) and d_desc == "Auto-generated Calendar Dimension")
                or "DATEAUTOTEMPLATE" in d_name_upper
                or "DATETABLETEMPLATE" in d_name_upper
                or "LOCALDATETABLE" in d_name_upper
            )
            if is_auto_date and str(d_name).casefold() not in related_dataset_names:
                logger.info(
                    "Omitting auto-date table '%s' with no relationships from semantic view TABLES clause",
                    d_name,
                )
                if self.drop_ledger is not None:
                    self.drop_ledger.record(
                        "table",
                        d_name,
                        DropStage.DDL_EMISSION,
                        "Auto-generated date table omitted: no active relationship to model tables",
                        by_design=True,
                    )
                continue

            # Calculated tables / field parameters have no physical source
            # table. Emit them only when a Snowflake object with their name
            # exists (the user materialized them); otherwise leave them out --
            # extraction already ledgered them as by-design with the reason.
            # Tables that host measures stay in so the measure-table
            # re-anchoring logic can move their metrics.
            d_kind = str(getattr(d, "dataset_kind", "table") or "table")
            if d_kind != "table" and not metric_counts_by_dataset.get(d_name, 0):
                if not self._has_live_table(d):
                    logger.info(
                        "Omitting %s '%s' from TABLES clause: no materialized Snowflake object",
                        d_kind, d_name,
                    )
                    continue
                if self.drop_ledger is not None:
                    self.drop_ledger.records = [
                        r for r in self.drop_ledger.records
                        if not (
                            r.entity_kind == "table"
                            and r.entity_name == str(d_name)
                            and r.stage == DropStage.EXTRACTION
                            and str(r.reason).startswith(("calculated table", "field parameter"))
                        )
                    ]

            # Omit unlinked measure-only tables (containers with no physical data columns and no relationships)
            cols = getattr(d, "columns", []) or []
            is_measure_table = getattr(d, "is_measure_table", False)
            non_dummy_cols = [
                c for c in cols
                if getattr(c, "unique_name", "") != "_"
                and not str(getattr(c, "unique_name", "")).startswith("_RowNumber")
            ]
            # A measure container in Power BI is a table that hosts measures,
            # has no relationships and only hidden placeholder column(s) (e.g.
            # "Value" from {1}, "Column1" from an empty #table) that no measure
            # reads. Those placeholders are not data -- treat the table as
            # measure-only so its measures are re-homed like any other.
            if (
                non_dummy_cols
                and metric_counts_by_dataset.get(d_name, 0) > 0
                and str(d_name).casefold() not in related_dataset_names
                and all(getattr(c, "is_hidden", False) for c in non_dummy_cols)
                and not self._columns_used_by_metrics(d_name, non_dummy_cols)
            ):
                non_dummy_cols = []
            if (is_measure_table or len(non_dummy_cols) == 0) and str(d_name).casefold() not in related_dataset_names:
                logger.info("Omitting unlinked measure-only table '%s' from semantic view TABLES clause", d_name)
                if self.drop_ledger is not None:
                    self.drop_ledger.record(
                        "table",
                        d_name,
                        DropStage.DDL_EMISSION,
                        "Measure-only table omitted: no relationships and no physical data columns "
                        "(only hidden placeholder columns no measure uses); its measures are placed on a data table",
                        by_design=True,
                    )
                continue

            filtered_datasets.append(d)
        datasets = filtered_datasets

        # Orient 1:1 relationships: point from the table with more metrics to the table with fewer
        for r in relationships:
            card = getattr(r, "cardinality", None)
            card_str = str(getattr(card, "value", card) or "").lower().replace("_", "-")
            if card_str in ("1:1", "one-to-one", "onetoone"):
                from_cnt = metric_counts_by_dataset.get(r.from_dataset, 0)
                to_cnt = metric_counts_by_dataset.get(r.to_dataset, 0)
                if to_cnt > from_cnt:
                    r.from_dataset, r.to_dataset = r.to_dataset, r.from_dataset
                    r.from_columns, r.to_columns = r.to_columns, r.from_columns

        # Fact classification relies strictly on active N:1 relationships (never 1:1, inactive, or table names).
        many_side_datasets: set[str] = set()
        one_side_datasets: set[str] = set()
        active_n1_rels: list[Any] = []
        for rel in relationships:
            if not getattr(rel, "is_active", True):
                continue
            card = getattr(rel, "cardinality", None)
            card_str = str(getattr(card, "value", card) or "many_to_one").lower().replace("-", "_").strip()
            if card_str in ("many_to_one", "manytoone", "m:1", "*:1", ""):
                active_n1_rels.append(rel)
                if getattr(rel, "from_dataset", None):
                    many_side_datasets.add(rel.from_dataset)
                if getattr(rel, "to_dataset", None):
                    one_side_datasets.add(rel.to_dataset)
            elif card_str in ("one_to_many", "onetomany", "1:m", "1:*"):
                active_n1_rels.append(rel)
                if getattr(rel, "to_dataset", None):
                    many_side_datasets.add(rel.to_dataset)
                if getattr(rel, "from_dataset", None):
                    one_side_datasets.add(rel.from_dataset)

        target_keys_by_dataset: dict[str, list[list[str]]] = {}
        for rel in relationships:
            if rel.to_dataset and rel.to_columns:
                if rel.to_dataset not in target_keys_by_dataset:
                    target_keys_by_dataset[rel.to_dataset] = []
                col_list = [c for c in rel.to_columns]
                if col_list not in target_keys_by_dataset[rel.to_dataset]:
                    target_keys_by_dataset[rel.to_dataset].append(col_list)
        # A 1:1 relationship proves uniqueness on BOTH ends, so its from-side
        # columns are key candidates too (the orientation above may have put
        # a table's real key on the from side). Appended after the to-side
        # targets; the key-column-first ordering in _build picks among them.
        for rel in relationships:
            card = getattr(rel, "cardinality", None)
            card_str = str(getattr(card, "value", card) or "").lower().replace("_", "-")
            if card_str in ("1:1", "one-to-one", "onetoone") and rel.from_dataset and rel.from_columns:
                col_list = [c for c in rel.from_columns]
                bucket = target_keys_by_dataset.setdefault(rel.from_dataset, [])
                if col_list not in bucket:
                    bucket.append(col_list)

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

            fn = getattr(self.schema_manager, "_physical_model_column_names", None)
            raw_physical = fn(dataset) if callable(fn) else None
            modeled_cols_list = (
                raw_physical
                if isinstance(raw_physical, list)
                else [c.unique_name for c in getattr(dataset, "columns", []) or []]
            )
            source_table = resolve_dataset_to_physical_table(
                dataset.unique_name,
                modeled_cols_list,
                self.live_schema_metadata,
                explicit_mapping=source_table_mapping,
                model_name=getattr(self._current_model, "unique_name", None) or getattr(self._current_model, "name", None),
            )
            if source_table == dataset.unique_name and getattr(dataset, "source_table", None):
                source_table = str(dataset.source_table)
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
            fn = getattr(self.schema_manager, "_physical_model_column_names", None)
            raw_physical = fn(dataset) if callable(fn) else None
            modeled_cols_list = (
                raw_physical
                if isinstance(raw_physical, list)
                else [c.unique_name for c in getattr(dataset, "columns", []) or []]
            )
            source_table = resolve_dataset_to_physical_table(
                dataset.unique_name,
                modeled_cols_list,
                self.live_schema_metadata,
                explicit_mapping=source_table_mapping,
                model_name=getattr(self._current_model, "unique_name", None) or getattr(self._current_model, "name", None),
            )
            if source_table == dataset.unique_name and getattr(dataset, "source_table", None):
                source_table = str(dataset.source_table)
            parts = [p.strip().strip('"') for p in source_table.split(".") if p.strip()]
            if len(parts) >= 3:
                safe_db = self.identifier_sanitizer.sanitize_table_name(parts[0])
                safe_schema = self.identifier_sanitizer.sanitize_table_name(parts[1])
                safe_tbl = self.identifier_sanitizer.sanitize_table_name(parts[2])
                full_table = f'"{safe_db}"."{safe_schema}"."{safe_tbl}"'
                unqualified_table = parts[2]
            elif len(parts) == 2:
                safe_schema = self.identifier_sanitizer.sanitize_table_name(parts[0])
                safe_tbl = self.identifier_sanitizer.sanitize_table_name(parts[1])
                full_table = f'"{self.config.database}"."{safe_schema}"."{safe_tbl}"'
                unqualified_table = parts[1]
            else:
                unqualified_table = parts[0] if parts else source_table
                safe_table = self.identifier_sanitizer.sanitize_table_name(unqualified_table)
                full_table = f'"{self.config.database}"."{self.config.schema_name}"."{safe_table}"'

            alias = self._get_unique_alias(dataset.unique_name, registry)
            if getattr(dataset, "name", None):
                registry.register_dataset_alias(dataset.name, alias)
            known_phys = dataset_col_lookup.get(dataset.unique_name, set())

            def _physical(col_name: str) -> str:
                if self.schema_manager is None:
                    return self.identifier_sanitizer.sanitize_column(col_name)
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

            # Facts come from active N:1 many-side relationships (never table names, 1:1, or inactive).
            # A key is the one-side column of a relationship, else the PBIX key column,
            # else a primary key declared in Snowflake (no columns[0] guessing).
            is_on_many_side = (dataset.unique_name in many_side_datasets and dataset.unique_name not in one_side_datasets)

            is_fact_table = (
                is_on_many_side
                or getattr(dataset, "table_type", "").upper() == "FACT"
                or (not active_n1_rels and getattr(dataset, "is_fact", False))
            )

            # Resolve primary key
            pk_cols: list[str] = []

            if is_measure_only:
                pk_cols = []
            elif resolved_target_keys:
                # Find the first target key whose uniqueness is guaranteed.
                # Keys made only of the model's own key columns (is_key) are
                # tried first, so a non-key column that some relationship
                # happens to target (e.g. a measure column) is never chosen
                # as PRIMARY KEY over the table's real key.
                _key_names = {
                    _physical(c.unique_name).upper()
                    for c in (dataset.columns or []) if getattr(c, "is_key", False)
                }
                resolved_target_keys = sorted(
                    resolved_target_keys,
                    key=lambda k: 0 if k and all(c.upper() in _key_names for c in k) else 1,
                )
                for tkey in resolved_target_keys:
                    if self._is_uniqueness_guaranteed(dataset, tkey, relationships, unqualified_table):
                        pk_cols = [f'"{c}"' for c in tkey]
                        break

                if not pk_cols:
                    if is_fact_table:
                        # Safeguard: unreferenced or unverified fact table keys are never guessed
                        pk_cols = []
                    else:
                        # Dimension table fallback: PBIX key or Snowflake declared PK only (never columns[0])
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
                # T8: Never declare UNIQUE without proof!
                if not self._is_uniqueness_guaranteed(dataset, tkey, relationships, unqualified_table):
                    logger.info("Skipping UNIQUE constraint for non-unique target key %s on %s", tkey, alias)
                    continue
                u_cols = ", ".join(f'"{c}"' for c in tkey)
                unique_clauses.append(f"UNIQUE ({u_cols})")
                declared_unique_keys.append(tkey)

            all_declared_keys = []
            if verified_pk:
                clean_pk = [c.strip('"') for c in verified_pk]
                declared_pk_by_alias[alias] = clean_pk
                all_declared_keys.append(clean_pk)
                for c in verified_pk:
                    relationship_target_alias[(dataset.unique_name, c.strip('"').upper())] = alias
                if hasattr(self.schema_manager, "warn_if_key_not_unique"):
                    self.schema_manager.warn_if_key_not_unique(
                        unqualified_table, clean_pk, cursor=self.cursor
                    )

            for ukey in declared_unique_keys:
                all_declared_keys.append(ukey)
                for c in ukey:
                    relationship_target_alias[(dataset.unique_name, c.upper())] = alias

            declared_pk_by_alias[f"__keys__{alias}"] = all_declared_keys

            pk_clause = f"PRIMARY KEY ({', '.join(verified_pk)})" if verified_pk else ""
            constraints = [c for c in [pk_clause, *unique_clauses] if c]
            pk_suffix = f" {' '.join(constraints)}" if constraints else ""

            if getattr(self.behavior.snowflake, "auto_add_anchors", True) and (is_fact_table or is_measure_only):
                full_table = self._build_source_query_with_anchors(full_table, dataset.unique_name, self._current_model)

            tables_lines.append(f'  {alias} AS {full_table}{pk_suffix}{table_annotation_clause(dataset)}')

        # Emit alias tables for inactive relationships ONLY when referenced by USERELATIONSHIP(...)
        from semabridge.utils.identifiers import inactive_role_alias_map, _relationship_signature

        # One unique alias per used inactive relationship (two inactive
        # relationships from the same FK to different dimension columns must
        # not share an alias -- a duplicate TABLES alias fails the view).
        role_aliases = inactive_role_alias_map(relationships, self._current_model)
        for rel in relationships:
            inact_alias = role_aliases.get(_relationship_signature(rel)) if not getattr(rel, "is_active", True) else None
            if inact_alias:
                dim_ds_name = getattr(rel, "to_dataset", None)
                dim_dataset = dataset_by_name.get(dim_ds_name)
                from_cols = getattr(rel, "from_columns", None) or []
                fk_col = from_cols[0] if from_cols else ""
                if dim_dataset and fk_col:
                    modeled_cols_list = [c.unique_name for c in getattr(dim_dataset, "columns", []) or []]
                    source_table = resolve_dataset_to_physical_table(
                        dim_dataset.unique_name,
                        modeled_cols_list,
                        self.live_schema_metadata,
                        explicit_mapping=source_table_mapping,
                        model_name=getattr(self._current_model, "unique_name", None) or getattr(self._current_model, "name", None),
                    )
                    parts = [p.strip().strip('"') for p in source_table.split(".") if p.strip()]
                    if len(parts) >= 3:
                        safe_db = self.identifier_sanitizer.sanitize_table_name(parts[0])
                        safe_schema = self.identifier_sanitizer.sanitize_table_name(parts[1])
                        safe_tbl = self.identifier_sanitizer.sanitize_table_name(parts[2])
                        dim_full_table = f'"{safe_db}"."{safe_schema}"."{safe_tbl}"'
                    elif len(parts) == 2:
                        safe_schema = self.identifier_sanitizer.sanitize_table_name(parts[0])
                        safe_tbl = self.identifier_sanitizer.sanitize_table_name(parts[1])
                        dim_full_table = f'"{self.config.database}"."{safe_schema}"."{safe_tbl}"'
                    else:
                        _unq = parts[0] if parts else source_table
                        safe_dim_table = self.identifier_sanitizer.sanitize_table_name(_unq)
                        dim_full_table = f'"{self.config.database}"."{self.config.schema_name}"."{safe_dim_table}"'

                    dim_alias = self._get_unique_alias(dim_dataset.unique_name, registry)
                    dim_pks = declared_pk_by_alias.get(dim_alias, [])
                    to_cols_raw = getattr(rel, "to_columns", None) or []
                    resolved_inact_pks = []
                    for c in to_cols_raw:
                        resolved = self.schema_manager._resolve_physical_column_name(dim_dataset, c) if dim_dataset else self.identifier_sanitizer.sanitize_column(c)
                        if resolved:
                            resolved_inact_pks.append(resolved)
                    inact_pks = resolved_inact_pks if resolved_inact_pks else dim_pks
                    pk_cols_str = ", ".join(f'"{c}"' for c in inact_pks)
                    dim_pk_suffix = f" PRIMARY KEY ({pk_cols_str})" if inact_pks else ""

                    line = f'  {inact_alias} AS {dim_full_table}{dim_pk_suffix}'
                    if line not in tables_lines:
                        tables_lines.append(line)
                        declared_pk_by_alias[inact_alias] = inact_pks
                        declared_pk_by_alias[f"__keys__{inact_alias}"] = [inact_pks]
                        registry.register_dataset_alias(inact_alias, inact_alias)
                        registry.register_dataset_alias(inact_alias.lower(), inact_alias)
                        registry.used_table_aliases.add(inact_alias)
                        dim_cols = dataset_col_lookup.get(dim_dataset.unique_name, set())
                        dataset_col_lookup[inact_alias] = set(dim_cols)
                        dataset_col_lookup[inact_alias.lower()] = set(dim_cols)

        return tables_lines, declared_pk_by_alias, relationship_target_alias, dataset_col_lookup, dataset_by_name, live_col_lookup

    def _columns_used_by_metrics(self, dataset_name: str, columns: List[Any]) -> bool:
        """True when any metric expression references one of ``columns`` of ``dataset_name``."""
        metrics = getattr(getattr(self, "_current_model", None), "metrics", None) or []
        tbl = re.escape(str(dataset_name))
        for c in columns:
            col = re.escape(str(getattr(c, "unique_name", "") or ""))
            if not col:
                continue
            # 'Table'[Col] / Table[Col], or a bare [Col] (not another table's column).
            dax_ref = re.compile(
                rf"(?:(?:'{tbl}'|(?<![\w']){tbl})\s*|(?<![\w'\]]))\[\s*{col}\s*\]", re.IGNORECASE
            )
            alias = re.escape(self.identifier_sanitizer.to_alias(str(dataset_name)))
            phys = re.escape(self.identifier_sanitizer.sanitize_column(str(c.unique_name)))
            sql_ref = re.compile(rf'(?<![\w"]){alias}\s*\.\s*"?{phys}\b', re.IGNORECASE)
            for m in metrics:
                expr = str(getattr(m, "expression", None) or "")
                sql = str(getattr(m, "sql_expression", None) or "")
                src = str(getattr(m, "source_column", None) or "")
                if dax_ref.search(expr) or (sql and sql_ref.search(sql)):
                    return True
                if src and src.casefold() == str(c.unique_name).casefold() and str(getattr(m, "dataset", "")).casefold() == str(dataset_name).casefold():
                    return True
        return False

    def _get_unique_alias(self, name: str, registry: Any) -> str:
        alias = registry.get_alias(name)
        if not alias:
            alias = self.identifier_sanitizer.to_alias(name)
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