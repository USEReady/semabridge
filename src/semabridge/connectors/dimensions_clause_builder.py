"""Builder for Snowflake semantic-view DIMENSIONS clause."""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.synonym_clause import synonyms_clause

logger = get_logger(__name__)


class DimensionsClauseBuilder:
    """Handles construction and validation of the DIMENSIONS clause."""

    def __init__(self, identifier_sanitizer: Any, schema_manager: Any, sanitizer: Any, translator: Any, behavior: Any):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer
        self.translator = translator
        self.behavior = behavior

    def build_for_sml(
        self,
        sml: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]]
    ) -> List[str]:
        """Build DIMENSIONS clause for SML model."""
        return self._build_dimensions(
            sml.dimensions, sml.datasets, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, measure_columns, sml.unique_name or sml.label, is_osi=False
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]]
    ) -> List[str]:
        """Build DIMENSIONS clause for OSI model."""
        return self._build_dimensions(
            osi.dimensions, osi.datasets, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, measure_columns, osi.unique_name or osi.label, is_osi=True
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
        is_osi: bool
    ) -> List[str]:
        dims_lines = []
        added_dimensions = set()
        added_physical_dimensions = set()
        used_dimension_aliases: Set[str] = set()

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
                
                known_phys = dataset_col_lookup.get(attr.dataset, set())
                if known_phys and phys_col not in known_phys:
                    continue
                    
                semantic_name = self.sanitizer.sanitize_semantic_name(attr.unique_name)
                dim_key = (alias, semantic_name, phys_col)
                physical_dim_key = (alias, phys_col)
                
                if self._is_measure_column(attr, phys_col, measure_columns, is_osi):
                    continue
                
                if dim_key not in added_dimensions and physical_dim_key not in added_physical_dimensions:
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic_name, used_dimension_aliases, attr.unique_name
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
                
            # Synthetic/projected columns injected by the enriched-view builder —
            # these are scalar subqueries, not physical base-table columns, and
            # Snowflake rejects them in semantic view DIMENSIONS clauses.
            _SYNTHETIC_COLS = {"MAX_DATE", "_CURRENT_FISCAL_PERIOD", "TOTAL_UNITS_ALL"}

            for col in dataset.columns:
                if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
                    continue
                if col.unique_name.upper() in _SYNTHETIC_COLS:
                    continue
                
                source_expr = getattr(col, 'source_expression', None)
                if source_expr and not IdentifierSanitizer.is_physical_source_column(source_expr):
                    continue
                
                semantic_source_name = str(getattr(col, "label", None) or col.unique_name)
                semantic_name = self.sanitizer.sanitize_semantic_name(semantic_source_name)
                if is_osi:
                    phys_col = self.identifier_sanitizer.sanitize_column(col.unique_name)
                else:
                    phys_col = self.schema_manager._resolve_physical_column_name(dataset, col.unique_name)
                
                known_phys = dataset_col_lookup.get(dataset.unique_name, set())
                if known_phys and phys_col not in known_phys:
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
                
                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name, used_dimension_aliases, semantic_source_name
                )
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS '
                    f'{self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                    f'{synonyms_clause(list(getattr(col, "synonyms", []) or []))}'
                )
                added_dimensions.add(dim_key)
                added_physical_dimensions.add(physical_dim_key)

        # Fallback
        if not dims_lines and datasets:
            self._apply_fallback(datasets[0], dataset_aliases, dataset_col_lookup, measure_columns, used_dimension_aliases, dims_lines, model_name, is_osi)

        return dims_lines

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
        dataset_obj = dataset_by_name.get(attr.dataset)
        if not dataset_obj:
            return []

        candidates = [
            getattr(attr, "source_column", None),
            getattr(attr, "dataset_column", None),
            getattr(attr, "unique_name", None),
            phys_col,
        ]
        columns = list(getattr(dataset_obj, "columns", []) or [])
        for candidate in candidates:
            if not candidate:
                continue
            col = dataset_obj.get_column(candidate) if hasattr(dataset_obj, "get_column") else None
            if not col:
                col = next(
                    (
                        item for item in columns
                        if str(getattr(item, "unique_name", "")).casefold() == str(candidate).casefold()
                    ),
                    None,
                )
            if col:
                return list(getattr(col, "synonyms", []) or [])
        return []

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
