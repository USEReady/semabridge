"""Builder for Snowflake semantic-view DIMENSIONS clause."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.synonym_clause import synonyms_clause
from semabridge.core.drop_ledger import DropLedger, DropStage

logger = get_logger(__name__)


class DimensionsClauseBuilder:
    """Handles construction and validation of the DIMENSIONS clause."""

    def __init__(
        self,
        identifier_sanitizer: Any,
        schema_manager: Any,
        sanitizer: Any,
        translator: Any,
        behavior: Any,
        drop_ledger: Optional[DropLedger] = None,
    ):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer
        self.translator = translator
        self.behavior = behavior
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()

    def build_for_sml(
        self,
        sml: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]],
        live_col_lookup: Optional[Dict[str, Set[str]]] = None,
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """Build DIMENSIONS clause for SML model.

        Returns (dims_lines, missing_dims) where missing_dims maps
        dataset_name → [physical_col_name, ...] for columns that exist in
        the source model but could not be confirmed in the Snowflake physical
        schema and were therefore excluded from DIMENSIONS.
        """
        return self._build_dimensions(
            sml.dimensions, sml.datasets, dataset_aliases, dataset_by_name,
            dataset_col_lookup, measure_columns, sml.unique_name or sml.label,
            is_osi=False, live_col_lookup=live_col_lookup or {},
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]],
        live_col_lookup: Optional[Dict[str, Set[str]]] = None,
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        """Build DIMENSIONS clause for OSI model.

        Returns (dims_lines, missing_dims).  See build_for_sml for details.
        """
        return self._build_dimensions(
            osi.dimensions, osi.datasets, dataset_aliases, dataset_by_name,
            dataset_col_lookup, measure_columns, osi.unique_name or osi.label,
            is_osi=True, live_col_lookup=live_col_lookup or {},
        )

    def _build_dimensions(
        self,
        dimensions: List[Any],
        datasets: List[Any],
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]],
        model_name: str,
        is_osi: bool,
        live_col_lookup: Optional[Dict[str, Set[str]]] = None,
    ) -> Tuple[List[str], Dict[str, List[str]]]:
        dims_lines: List[str] = []
        # Tracks columns present in the source model but skipped because they
        # were not confirmed in the live Snowflake physical schema.
        # Structure: { dataset_unique_name: [phys_col, ...] }
        missing_dims: Dict[str, List[str]] = {}
        added_dimensions = set()
        added_physical_dimensions = set()
        used_dimension_aliases_by_dataset: Dict[str, Set[str]] = {}

        # 1. Add explicitly defined dimensions
        for dim in dimensions:
            for attr in dim.attributes:
                alias = dataset_aliases.get(attr.dataset)
                if not alias:
                    continue

                if is_osi:
                    phys_col = self.identifier_sanitizer.sanitize_column(attr.source_column)
                else:
                    raw_col = getattr(attr, "dataset_column", None) or attr.unique_name
                    dataset_obj = dataset_by_name.get(attr.dataset)
                    if not dataset_obj:
                        continue
                    phys_col = self.schema_manager._resolve_physical_column_name(dataset_obj, raw_col)
                
                # Prefer live (confirmed Snowflake) schema over modeled fallback.
                _live = (live_col_lookup or {}).get(attr.dataset)
                if _live is not None:
                    if phys_col not in _live:
                        missing_dims.setdefault(attr.dataset, [])
                        if phys_col not in missing_dims[attr.dataset]:
                            missing_dims[attr.dataset].append(phys_col)
                        self.drop_ledger.record(
                            "column", attr.unique_name, DropStage.SCHEMA_VALIDATION,
                            f"Column '{phys_col}' is present in the model but not found in the "
                            f"live Snowflake schema for dataset '{attr.dataset}'",
                            dataset=attr.dataset,
                        )
                        continue
                else:
                    known_phys = dataset_col_lookup.get(attr.dataset, set())
                    if known_phys and phys_col not in known_phys:
                        self.drop_ledger.record(
                            "column", attr.unique_name, DropStage.SCHEMA_VALIDATION,
                            f"Column '{phys_col}' is present in the model but not found in the "
                            f"known physical schema for dataset '{attr.dataset}' "
                            "(no live Snowflake schema was fetched to confirm it)",
                            dataset=attr.dataset,
                        )
                        continue

                semantic_name = self.sanitizer.sanitize_semantic_name(attr.unique_name)
                dim_key = (alias, semantic_name, phys_col)
                physical_dim_key = (alias, phys_col)
                
                if self._is_measure_column(attr, phys_col, measure_columns, is_osi):
                    continue
                
                if dim_key not in added_dimensions and physical_dim_key not in added_physical_dimensions:
                    ds_used = used_dimension_aliases_by_dataset.setdefault(alias, set())
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic_name, ds_used, attr.unique_name
                    )
                    col_synonyms = self._lookup_attribute_synonyms(
                        attr,
                        dataset_by_name,
                        phys_col,
                    )
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS '
                        f'{self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                        f'{synonyms_clause(col_synonyms)}'
                    )
                    added_dimensions.add(dim_key)
                    added_physical_dimensions.add(physical_dim_key)
                    
        # 2. Add raw attributes
        for dataset in datasets:
            alias = dataset_aliases.get(dataset.unique_name)
            if not alias:
                continue
                
            _SYNTHETIC_COLS = {
                "MAX_DATE", "_CURRENT_FISCAL_PERIOD",
                "MAX_MONTHINDEX", "MAX_YEARINDEX", "MAX_QUARTERINDEX", "MAX_WEEKINDEX",
            }
            _LIVE_ONLY_COLS = {"MONTHINDEX", "YEARINDEX", "QUARTERINDEX", "WEEKINDEX"}
            _has_live = dataset.unique_name in (live_col_lookup or {})

            for col in dataset.columns:
                if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                    continue
                if col.unique_name.upper() in _SYNTHETIC_COLS:
                    continue

                if is_osi:
                    phys_col = self.identifier_sanitizer.sanitize_column(col.unique_name)
                else:
                    phys_col = self.schema_manager._resolve_physical_column_name(dataset, col.unique_name)

                if not _has_live and col.unique_name.upper() in _LIVE_ONLY_COLS:
                    missing_dims.setdefault(dataset.unique_name, [])
                    if phys_col not in missing_dims[dataset.unique_name]:
                        missing_dims[dataset.unique_name].append(phys_col)
                    self.drop_ledger.record(
                        "column", col.unique_name, DropStage.SCHEMA_VALIDATION,
                        f"Column '{phys_col}' is a date-intelligence anchor only safe to emit "
                        f"when the live Snowflake schema confirms it exists; no live schema was "
                        f"available for dataset '{dataset.unique_name}'",
                        dataset=dataset.unique_name,
                    )
                    continue

                source_expr = getattr(col, 'source_expression', None)
                if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                    continue

                semantic_source_name = str(getattr(col, "label", None) or col.unique_name)
                semantic_name = self.sanitizer.sanitize_semantic_name(semantic_source_name)

                _live = (live_col_lookup or {}).get(dataset.unique_name)
                if _live is not None:
                    if phys_col not in _live:
                        missing_dims.setdefault(dataset.unique_name, [])
                        if phys_col not in missing_dims[dataset.unique_name]:
                            missing_dims[dataset.unique_name].append(phys_col)
                        self.drop_ledger.record(
                            "column", col.unique_name, DropStage.SCHEMA_VALIDATION,
                            f"Column '{phys_col}' is present in the model but not found in the "
                            f"live Snowflake schema for dataset '{dataset.unique_name}'",
                            dataset=dataset.unique_name,
                        )
                        continue
                else:
                    known_phys = dataset_col_lookup.get(dataset.unique_name, set())
                    if known_phys and phys_col not in known_phys:
                        self.drop_ledger.record(
                            "column", col.unique_name, DropStage.SCHEMA_VALIDATION,
                            f"Column '{phys_col}' is present in the model but not found in the "
                            f"known physical schema for dataset '{dataset.unique_name}' "
                            "(no live Snowflake schema was fetched to confirm it)",
                            dataset=dataset.unique_name,
                        )
                        continue

                dim_key = (alias, semantic_name, phys_col)
                physical_dim_key = (alias, phys_col)
                if dim_key in added_dimensions or physical_dim_key in added_physical_dimensions:
                    continue
                
                sync_all = self.behavior.semantic_model.sync_all_attributes
                if not is_osi and getattr(col, "is_measure_candidate", False) and not sync_all:
                    continue
                
                if self._measure_key(dataset.unique_name, col.unique_name) in measure_columns:
                    if not sync_all:
                        continue
                
                ds_used = used_dimension_aliases_by_dataset.setdefault(alias, set())
                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name, ds_used, semantic_source_name
                )
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS '
                    f'{self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                    f'{synonyms_clause(list(getattr(col, "synonyms", []) or []))}'
                )
                added_dimensions.add(dim_key)
                added_physical_dimensions.add(physical_dim_key)

        # 3. Add extra physical/synthetic columns present in live_col_lookup / dataset_col_lookup
        for dataset in datasets:
            alias = dataset_aliases.get(dataset.unique_name)
            if not alias:
                continue

            candidate_cols: Set[str] = set()
            if live_col_lookup and dataset.unique_name in live_col_lookup:
                candidate_cols.update(live_col_lookup[dataset.unique_name])
            if dataset_col_lookup and dataset.unique_name in dataset_col_lookup:
                candidate_cols.update(dataset_col_lookup[dataset.unique_name])

            if self.translator and getattr(self.translator, "anchor_flag_map", None):
                ds_flags = self.translator.anchor_flag_map.get(str(dataset.unique_name or "").casefold()) or {}
                for flag_val in ds_flags.values():
                    if isinstance(flag_val, str) and flag_val:
                        candidate_cols.add(flag_val)

            for phys_col in sorted(candidate_cols):
                if not phys_col or phys_col.startswith("RowNumber") or phys_col.startswith("_"):
                    continue

                physical_dim_key = (alias, phys_col)
                if physical_dim_key in added_physical_dimensions:
                    continue

                semantic_name = self.sanitizer.sanitize_semantic_name(phys_col)
                dim_key = (alias, semantic_name, phys_col)
                if dim_key in added_dimensions:
                    continue

                ds_used = used_dimension_aliases_by_dataset.setdefault(alias, set())
                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name, ds_used, phys_col
                )
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS '
                    f'{self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                )
                added_dimensions.add(dim_key)
                added_physical_dimensions.add(physical_dim_key)

        # Fallback
        if not dims_lines and datasets:
            fallback_alias = dataset_aliases.get(datasets[0].unique_name, "")
            ds_used = used_dimension_aliases_by_dataset.setdefault(fallback_alias, set())
            self._apply_fallback(datasets[0], dataset_aliases, dataset_col_lookup, measure_columns, ds_used, dims_lines, model_name, is_osi)

        return dims_lines, missing_dims

    def _is_measure_column(self, attr: Any, phys_col: str, measure_columns: Set[Tuple[str, str]], is_osi: bool) -> bool:
        if is_osi:
            return (
                self._measure_key(attr.dataset, phys_col) in measure_columns
                or self._measure_key(attr.dataset, attr.source_column) in measure_columns
                or self._measure_key(attr.dataset, attr.unique_name) in measure_columns
            )
        else:
            raw_col = getattr(attr, "dataset_column", None) or attr.unique_name
            return (
                self._measure_key(attr.dataset, raw_col) in measure_columns
                or self._measure_key(attr.dataset, attr.unique_name) in measure_columns
                or self._measure_key(attr.dataset, phys_col) in measure_columns
            )

    def _lookup_attribute_synonyms(
        self,
        attr: Any,
        dataset_by_name: Dict[str, Any],
        phys_col: str,
    ) -> List[str]:
        from semabridge.utils.synonyms import lookup_attribute_synonyms
        dataset_obj = dataset_by_name.get(attr.dataset)
        return lookup_attribute_synonyms(attr, dataset_obj, phys_col)


    def _measure_key(self, dataset_name: Optional[str], column_name: Optional[str]) -> Tuple[str, str]:
        return (
            str(dataset_name or "").strip().casefold(),
            self.identifier_sanitizer.sanitize_column(column_name or ""),
        )

    def _resolve_unique_dimension_alias(
        self,
        base_alias: str,
        used_aliases: Set[str],
        original_dimension_name: str
    ) -> str:
        """Ensure dimension alias is unique across a semantic view."""
        if base_alias not in used_aliases:
            used_aliases.add(base_alias)
            return base_alias

        idx = 2
        while True:
            candidate = self.sanitizer.sanitize_semantic_name(f"{base_alias}_{idx}")
            if candidate not in used_aliases:
                used_aliases.add(candidate)
                logger.warning(
                    "Dimension alias collision for '%s' (base '%s'); using '%s'",
                    original_dimension_name,
                    base_alias,
                    candidate,
                )
                return candidate
            idx += 1

    def _apply_fallback(
        self,
        first_ds: Any,
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]],
        used_dimension_aliases: Set[str],
        dims_lines: List[str],
        model_name: Optional[str],
        is_osi: bool
    ) -> None:
        alias = dataset_aliases.get(first_ds.unique_name)
        known_phys = dataset_col_lookup.get(first_ds.unique_name, set())
        for col in first_ds.columns:
            phys = (self.identifier_sanitizer.sanitize_column(col.unique_name) if is_osi 
                    else self.schema_manager._resolve_physical_column_name(first_ds, col.unique_name))
            
            if self._measure_key(first_ds.unique_name, col.unique_name) in measure_columns:
                continue
            
            if not is_osi and getattr(col, "is_measure_candidate", False):
                continue

            if not col.unique_name.startswith("_") and phys in known_phys:
                semantic = self.sanitizer.sanitize_semantic_name(col.unique_name)
                emitted_name = self._resolve_unique_dimension_alias(semantic, used_dimension_aliases, col.unique_name)
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS '
                    f'{self.sanitizer.format_physical_column_ref(alias, phys, model_name=model_name)}'
                    f'{synonyms_clause(list(getattr(col, "synonyms", []) or []))}'
                )
                return

        # Last resort
        if first_ds.columns:
            col = first_ds.columns[0]
            semantic = self.sanitizer.sanitize_semantic_name(col.unique_name)
            phys = (self.identifier_sanitizer.sanitize_column(col.unique_name) if is_osi 
                    else self.schema_manager._resolve_physical_column_name(first_ds, col.unique_name))
            emitted_name = self._resolve_unique_dimension_alias(semantic, used_dimension_aliases, col.unique_name)
            dims_lines.append(
                f'  {alias}."{emitted_name}" AS '
                f'{self.sanitizer.format_physical_column_ref(alias, phys, model_name=model_name)}'
                f'{synonyms_clause(list(getattr(col, "synonyms", []) or []))}'
            )
