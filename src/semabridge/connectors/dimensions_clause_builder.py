"""Builder for Snowflake semantic-view DIMENSIONS clause."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Set, Tuple, Optional

from semabridge.connectors.synonym_clause import synonyms_clause

from semabridge.utils.logger import get_logger
from semabridge.utils.identifiers import IdentifierSanitizer

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
        measure_columns: Set[Tuple[str, str]],
        relationship_columns: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[str]:
        """Build DIMENSIONS clause for SML model."""
        return self._build_dimensions(
            sml.dimensions, sml.datasets, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, measure_columns, sml.unique_name or sml.label, is_osi=False,
            relationship_columns=relationship_columns or set(),
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]],
        relationship_columns: Optional[Set[Tuple[str, str]]] = None,
    ) -> List[str]:
        """Build DIMENSIONS clause for OSI model."""
        return self._build_dimensions(
            osi.dimensions, osi.datasets, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, measure_columns, osi.unique_name or osi.label, is_osi=True,
            relationship_columns=relationship_columns or set(),
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
        relationship_columns: Set[Tuple[str, str]],
    ) -> List[str]:
        dims_lines = []
        added_dimensions = set()
        used_dimension_aliases: Set[str] = set()

        modeled_phys_by_dataset: Dict[str, Set[str]] = {}
        for dataset in datasets:
            modeled_phys_by_dataset[dataset.unique_name] = {
                self.identifier_sanitizer.sanitize_column(getattr(col, "unique_name", ""))
                for col in getattr(dataset, "columns", []) or []
                if getattr(col, "unique_name", None)
            }

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
                modeled_phys = modeled_phys_by_dataset.get(attr.dataset, set())
                if not self._column_allowed_by_schema_guard(phys_col, known_phys, modeled_phys):
                    continue
                    
                semantic_name = self.sanitizer.sanitize_semantic_name(attr.unique_name)
                dim_key = (alias, semantic_name, phys_col)
                
                if self._is_measure_column(attr, phys_col, measure_columns, is_osi):
                    continue
                if self._is_relationship_column(attr.dataset, raw_column=phys_col, relationship_columns=relationship_columns):
                    continue
                
                if dim_key not in added_dimensions:
                    emitted_name = self._resolve_unique_dimension_alias(
                        semantic_name, used_dimension_aliases, attr.unique_name
                    )
                    # Lookup synonyms from the underlying column if available
                    item_synonyms = []
                    dataset_obj = dataset_by_name.get(attr.dataset)
                    if dataset_obj:
                        raw_col_names = [
                            getattr(attr, "dataset_column", None),
                            getattr(attr, "source_column", None),
                            attr.unique_name,
                        ]
                        col_obj = next(
                            (
                                dataset_obj.get_column(raw_col_name)
                                for raw_col_name in raw_col_names
                                if raw_col_name and dataset_obj.get_column(raw_col_name)
                            ),
                            None,
                        )
                        if col_obj:
                            item_synonyms = getattr(col_obj, "synonyms", [])
                    
                    syn_clause = synonyms_clause(item_synonyms)
                    dims_lines.append(
                        f'  {alias}."{emitted_name}" AS {self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}{syn_clause}'
                    )
                    added_dimensions.add(dim_key)
                    
        # 2. Add raw attributes
        for dataset in datasets:
            alias = dataset_aliases.get(dataset.unique_name)
            if not alias:
                continue
                
            for col in dataset.columns:
                if col.unique_name.startswith("RowNumber") or col.unique_name.startswith("_"):
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
                modeled_phys = modeled_phys_by_dataset.get(dataset.unique_name, set())
                if not self._column_allowed_by_schema_guard(phys_col, known_phys, modeled_phys):
                    continue

                dim_key = (alias, semantic_name, phys_col)
                if dim_key in added_dimensions:
                    continue
                
                sync_all = self.behavior.semantic_model.sync_all_attributes
                if not is_osi and getattr(col, "is_measure_candidate", False) and not sync_all:
                    continue
                
                if self._measure_key(dataset.unique_name, col.unique_name) in measure_columns:
                    if not sync_all:
                        continue
                if self._is_relationship_column(dataset.unique_name, raw_column=col.unique_name, relationship_columns=relationship_columns):
                    continue
                
                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name, used_dimension_aliases, semantic_source_name
                )
                syn_clause = synonyms_clause(getattr(col, "synonyms", []))
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS {self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}{syn_clause}'
                )
                added_dimensions.add(dim_key)

        # Fallback
        self._backfill_from_physical_schema(
            datasets=datasets,
            dataset_aliases=dataset_aliases,
            dataset_col_lookup=dataset_col_lookup,
            measure_columns=measure_columns,
            relationship_columns=relationship_columns,
            used_dimension_aliases=used_dimension_aliases,
            dims_lines=dims_lines,
            added_dimensions=added_dimensions,
            model_name=model_name,
        )

        # Last resort fallback
        if not dims_lines and datasets:
            self._apply_fallback(
                datasets[0],
                dataset_aliases,
                dataset_col_lookup,
                measure_columns,
                relationship_columns,
                used_dimension_aliases,
                dims_lines,
                model_name,
                is_osi,
            )

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
            hash_seed = f"{base_alias}|{original_dimension_name}|{idx}".encode("utf-8")
            suffix = hashlib.sha256(hash_seed).hexdigest()[:4].upper()
            candidate = self.sanitizer.sanitize_semantic_name(f"{base_alias}_{suffix}")
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
        relationship_columns: Set[Tuple[str, str]],
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
            if self._is_relationship_column(
                first_ds.unique_name, raw_column=col.unique_name, relationship_columns=relationship_columns
            ):
                continue
            
            if not is_osi and getattr(col, "is_measure_candidate", False):
                continue

            if not col.unique_name.startswith("_") and phys in known_phys:
                semantic = self.sanitizer.sanitize_semantic_name(col.unique_name)
                emitted_name = self._resolve_unique_dimension_alias(semantic, used_dimension_aliases, col.unique_name)
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS {self.sanitizer.format_physical_column_ref(alias, phys, model_name=model_name)}'
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
                f'  {alias}."{emitted_name}" AS {self.sanitizer.format_physical_column_ref(alias, phys, model_name=model_name)}'
            )

    def _column_allowed_by_schema_guard(
        self,
        phys_col: str,
        known_phys: Set[str],
        modeled_phys: Set[str],
    ) -> bool:
        """Allow only verified physical columns unless explicitly configured otherwise."""
        # If we have no live schema metadata for this dataset, allow emission
        # so models can be generated in metadata-light environments.
        if not known_phys:
            return True

        # Honor an explicit behavior flag that lets teams opt into permissive
        # emission of modeled columns even when the live schema is missing them.
        # This is intentionally opt-in because emitting non-existent physical
        # references will cause Snowflake to fail at deployment time.
        try:
            if getattr(self.behavior, "semantic_model", None) and getattr(
                getattr(self.behavior, "semantic_model"), "allow_emit_modeled_missing_columns", False
            ):
                return True
        except Exception:
            # If behavior isn't structured as expected, ignore and continue strict mode
            pass

        if phys_col in known_phys:
            return True

        logger.warning(
            "Column '%s' not found in live schema for dataset (available: %s). Skipping emission to avoid DDL errors.",
            phys_col,
            sorted(list(known_phys))[:10],
        )
        return False

    def _is_relationship_column(
        self,
        dataset_name: Optional[str],
        raw_column: Optional[str],
        relationship_columns: Set[Tuple[str, str]],
    ) -> bool:
        return self._measure_key(dataset_name, raw_column) in relationship_columns

    def _backfill_from_physical_schema(
        self,
        *,
        datasets: List[Any],
        dataset_aliases: Dict[str, str],
        dataset_col_lookup: Dict[str, Set[str]],
        measure_columns: Set[Tuple[str, str]],
        relationship_columns: Set[Tuple[str, str]],
        used_dimension_aliases: Set[str],
        dims_lines: List[str],
        added_dimensions: Set[Tuple[str, str, str]],
        model_name: str,
    ) -> None:
        """Backfill dimensions from live physical schema when modeled columns are sparse."""
        for dataset in datasets:
            alias = dataset_aliases.get(dataset.unique_name)
            if not alias:
                continue

            known_phys = dataset_col_lookup.get(dataset.unique_name, set())
            if not known_phys:
                continue

            modeled_phys = {
                self.identifier_sanitizer.sanitize_column(col.unique_name)
                for col in getattr(dataset, "columns", []) or []
                if getattr(col, "unique_name", None)
            }

            for phys_col in sorted(known_phys):
                if not phys_col or phys_col.startswith("_") or phys_col.startswith("ROWNUMBER"):
                    continue
                if self._measure_key(dataset.unique_name, phys_col) in measure_columns:
                    continue
                if self._is_relationship_column(
                    dataset.unique_name, raw_column=phys_col, relationship_columns=relationship_columns
                ):
                    continue
                if phys_col in modeled_phys:
                    continue

                semantic_name = self.sanitizer.sanitize_semantic_name(phys_col)
                dim_key = (alias, semantic_name, phys_col)
                if dim_key in added_dimensions:
                    continue

                emitted_name = self._resolve_unique_dimension_alias(
                    semantic_name, used_dimension_aliases, phys_col
                )
                dims_lines.append(
                    f'  {alias}."{emitted_name}" AS {self.sanitizer.format_physical_column_ref(alias, phys_col, model_name=model_name)}'
                )
                added_dimensions.add(dim_key)
