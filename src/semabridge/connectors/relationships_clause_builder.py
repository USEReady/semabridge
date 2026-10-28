"""Builder for Snowflake semantic-view RELATIONSHIPS clause."""

from __future__ import annotations
from semabridge.utils.identifiers import inactive_alias

from typing import Any, Dict, List, Optional, Set, Tuple
import re

from semabridge.utils.logger import get_logger
from semabridge.core.drop_ledger import DropLedger, DropStage

logger = get_logger(__name__)


class RelationshipsClauseBuilder:
    """Handles construction and validation of the RELATIONSHIPS clause."""

    def __init__(
        self,
        identifier_sanitizer: Any,
        schema_manager: Any,
        sanitizer: Any,
        drop_ledger: Optional[DropLedger] = None,
    ):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()

    def build_for_sml(
        self,
        sml: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        declared_pk_by_alias: Dict[str, List[str]],
        relationship_target_alias: Dict[Tuple[str, str], str]
    ) -> List[str]:
        """Build RELATIONSHIPS clause for SML model."""
        return self._build_relationships(
            sml.relationships, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, declared_pk_by_alias, relationship_target_alias, is_osi=False
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        declared_pk_by_alias: Dict[str, List[str]],
        relationship_target_alias: Dict[Tuple[str, str], str]
    ) -> List[str]:
        """Build RELATIONSHIPS clause for OSI model."""
        return self._build_relationships(
            osi.relationships, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, declared_pk_by_alias, relationship_target_alias, is_osi=True
        )

    def _build_relationships(
        self,
        relationships: List[Any],
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        declared_pk_by_alias: Dict[str, List[str]],
        relationship_target_alias: Dict[Tuple[str, str], str],
        is_osi: bool
    ) -> List[str]:
        rel_lines = []
        for rel in relationships:
            if not rel.is_active:
                logger.info(
                    "Including inactive Fabric relationship '%s' -> '%s' for Snowflake metadata parity.",
                    rel.from_dataset,
                    rel.to_dataset,
                )

            from_alias = dataset_aliases.get(rel.from_dataset)
            to_alias = dataset_aliases.get(rel.to_dataset)

            if not getattr(rel, "is_active", True):
                from_cols_raw = getattr(rel, "from_columns", None) or []
                fk_col_raw = from_cols_raw[0] if from_cols_raw else ""
                if fk_col_raw:
                    to_alias = inactive_alias(rel.to_dataset, fk_col_raw, uppercase=True)

            if not from_alias or not to_alias or not rel.from_columns:
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"Unresolvable aliases (from={from_alias}, to={to_alias}) or missing from_columns."
                )
                self.drop_ledger.record(
                    "relationship", f"{rel.from_dataset} -> {rel.to_dataset}", DropStage.DDL_EMISSION,
                    f"Unresolvable dataset aliases (from={from_alias}, to={to_alias}) or "
                    "missing from_columns.",
                    dataset=rel.from_dataset,
                )
                continue

            from_ds = dataset_by_name.get(rel.from_dataset)
            to_ds = dataset_by_name.get(rel.to_dataset)
            
            from_cols: list[str] = []
            to_cols: list[str] = []

            for c in rel.from_columns:
                resolved = self.schema_manager._resolve_physical_column_name(from_ds, c) if from_ds else self.identifier_sanitizer.sanitize_column(c)
                from_cols.append(resolved)
            for c in (rel.to_columns or []):
                resolved = self.schema_manager._resolve_physical_column_name(to_ds, c) if to_ds else self.identifier_sanitizer.sanitize_column(c)
                to_cols.append(resolved)

            if not from_cols or any(not c for c in from_cols):
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"from_col is empty after resolution."
                )
                self.drop_ledger.record(
                    "relationship", f"{rel.from_dataset} -> {rel.to_dataset}", DropStage.DDL_EMISSION,
                    "The relationship's from-column resolved to empty after physical "
                    "column resolution.",
                    dataset=rel.from_dataset,
                )
                continue

            # Validate FK columns exist
            from_phys = dataset_col_lookup.get(rel.from_dataset, set())
            if from_phys:
                validated_from = [c for c in from_cols if c in from_phys]
                if validated_from:
                    from_cols = validated_from

            # Validate PK reference
            if to_cols:
                to_phys = dataset_col_lookup.get(rel.to_dataset, set())
                mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_cols[0].upper()))
                if mapped_to_alias:
                    to_alias = mapped_to_alias
                
                to_phys_upper = {c.upper() for c in to_phys}
                if to_phys:
                    validated_to = [c for c in to_cols if c.upper() in to_phys_upper]
                    if validated_to:
                        to_cols = validated_to


            quoted_to_cols = [f'"{c}"' for c in to_cols]
            ref_clause = f'{to_alias} ({", ".join(quoted_to_cols)})' if to_cols else to_alias
            rel_name = self.sanitizer.to_snowflake_relationship_name(getattr(rel, "unique_name", "") or "")
            from_ref = ", ".join([f'"{c}"' for c in from_cols])
            
            if rel_name:
                rel_lines.append(f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            else:
                rel_lines.append(f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}')
        
        return rel_lines
