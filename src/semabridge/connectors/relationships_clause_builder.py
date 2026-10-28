"""Builder for Snowflake semantic-view RELATIONSHIPS clause."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

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
        relationship_target_alias: Dict[Tuple[str, str], str],
        emission_plan: Optional[Tuple[Any, Any, Any, Any]] = None,
    ) -> List[str]:
        """Build RELATIONSHIPS clause for SML model."""
        return self._build_relationships(
            sml.relationships, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, declared_pk_by_alias, relationship_target_alias, is_osi=False,
            emission_plan=emission_plan, model=sml
        )

    def build_for_osi(
        self,
        osi: Any,
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        declared_pk_by_alias: Dict[str, List[str]],
        relationship_target_alias: Dict[Tuple[str, str], str],
        emission_plan: Optional[Tuple[Any, Any, Any, Any]] = None,
    ) -> List[str]:
        """Build RELATIONSHIPS clause for OSI model."""
        return self._build_relationships(
            osi.relationships, dataset_aliases, dataset_by_name, 
            dataset_col_lookup, declared_pk_by_alias, relationship_target_alias, is_osi=True,
            emission_plan=emission_plan, model=osi
        )

    def _build_relationships(
        self,
        relationships: List[Any],
        dataset_aliases: Dict[str, str],
        dataset_by_name: Dict[str, Any],
        dataset_col_lookup: Dict[str, Set[str]],
        declared_pk_by_alias: Dict[str, List[str]],
        relationship_target_alias: Dict[Tuple[str, str], str],
        is_osi: bool,
        emission_plan: Optional[Tuple[Any, Any, Any, Any]] = None,
        model: Any = None,
    ) -> List[str]:
        from semabridge.connectors.relationship_emission_helper import RelationshipEmissionPlanner

        if emission_plan is not None:
            norm_rels = emission_plan[0]
        else:
            planner = RelationshipEmissionPlanner(self.identifier_sanitizer, drop_ledger=self.drop_ledger)
            norm_rels, _, _, _ = planner.plan(
                list(dataset_by_name.values()), relationships, metrics=getattr(model, "metrics", []) if model else []
            )

        rel_lines = []
        for rel_item in norm_rels:
            if rel_item.is_skipped:
                continue

            from_ds_name = rel_item.from_dataset
            to_ds_name = rel_item.to_dataset

            from_alias = dataset_aliases.get(from_ds_name)
            if rel_item.is_role_playing and rel_item.role_alias:
                to_alias = rel_item.role_alias
            else:
                to_alias = dataset_aliases.get(to_ds_name)

            if not from_alias or not to_alias or not rel_item.from_columns:
                logger.warning(
                    f"Skipping relationship '{from_ds_name}' -> '{to_ds_name}': "
                    f"Unresolvable aliases (from={from_alias}, to={to_alias}) or missing from_columns."
                )
                self.drop_ledger.record(
                    "relationship", f"{from_ds_name} -> {to_ds_name}", DropStage.DDL_EMISSION,
                    f"Unresolvable dataset aliases (from={from_alias}, to={to_alias}) or "
                    "missing from_columns.",
                    dataset=from_ds_name,
                )
                continue

            from_ds = dataset_by_name.get(from_ds_name)
            to_ds = dataset_by_name.get(to_ds_name)
            
            from_cols: list[str] = []
            to_cols: list[str] = []

            for c in rel_item.from_columns:
                resolved = self.schema_manager._resolve_physical_column_name(from_ds, c) if from_ds else self.identifier_sanitizer.sanitize_column(c)
                from_cols.append(resolved)
            for c in (rel_item.to_columns or []):
                resolved = self.schema_manager._resolve_physical_column_name(to_ds, c) if to_ds else self.identifier_sanitizer.sanitize_column(c)
                to_cols.append(resolved)

            if not from_cols or any(not c for c in from_cols):
                logger.warning(
                    f"Skipping relationship '{from_ds_name}' -> '{to_ds_name}': "
                    f"from_col is empty after resolution."
                )
                self.drop_ledger.record(
                    "relationship", f"{from_ds_name} -> {to_ds_name}", DropStage.DDL_EMISSION,
                    "The relationship's from-column resolved to empty after physical "
                    "column resolution.",
                    dataset=from_ds_name,
                )
                continue

            # Validate FK columns exist (case-insensitive lookup)
            from_phys = dataset_col_lookup.get(from_ds_name, set())
            from_phys_map = {c.upper(): c for c in from_phys}
            if from_phys:
                from_cols = [from_phys_map.get(c.upper(), c) for c in from_cols]

            # Validate PK reference (case-insensitive lookup)
            if to_cols:
                to_phys = dataset_col_lookup.get(to_ds_name, set())
                to_phys_map = {c.upper(): c for c in to_phys}
                if to_phys:
                    to_cols = [to_phys_map.get(c.upper(), c) for c in to_cols]

                if not rel_item.is_role_playing:
                    mapped_to_alias = relationship_target_alias.get((to_ds_name, to_cols[0].upper()))
                    if mapped_to_alias:
                        to_alias = mapped_to_alias

            quoted_to_cols = [f'"{c}"' for c in to_cols]
            ref_clause = f'{to_alias} ({", ".join(quoted_to_cols)})' if to_cols else to_alias
            from_ref = ", ".join([f'"{c}"' for c in from_cols])

            # Generate stable Snowflake relationship name
            rel_name_candidate = getattr(rel_item.original_rel, "unique_name", "") or ""
            if rel_item.is_role_playing or not rel_name_candidate or rel_item.cardinality == "many-to-one" and getattr(rel_item.original_rel, "cardinality", None) and "one" in str(getattr(rel_item.original_rel, "cardinality", "")).lower().split("to")[0]:
                rel_name = self.sanitizer.to_snowflake_relationship_name(f"{from_alias}_{from_cols[0]}_{to_alias}_{to_cols[0]}")
            else:
                rel_name = self.sanitizer.to_snowflake_relationship_name(rel_name_candidate)
            
            if rel_name:
                rel_lines.append(f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            else:
                rel_lines.append(f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}')
        
        return rel_lines
