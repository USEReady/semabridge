"""Builder for Snowflake semantic-view RELATIONSHIPS clause."""

from __future__ import annotations
from semabridge.utils.naming import to_alias as derive_alias

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
            dataset_col_lookup, declared_pk_by_alias, relationship_target_alias, is_osi=False, model=sml
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
            dataset_col_lookup, declared_pk_by_alias, relationship_target_alias, is_osi=True, model=osi
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
        model: Any = None,
    ) -> List[str]:
        from semabridge.utils.identifiers import (
            inactive_relationship_used_by_measures,
            inactive_role_alias_map,
            _relationship_signature,
        )

        role_aliases = inactive_role_alias_map(relationships, model)
        rel_lines = []
        rel_meta: List[Tuple[str, str, bool, Any]] = []
        for rel in relationships:
            if not getattr(rel, "is_active", True):
                if not inactive_relationship_used_by_measures(rel, model):
                    logger.info(
                        "Skipping inactive relationship '%s' -> '%s': no measure uses USERELATIONSHIP.",
                        rel.from_dataset,
                        rel.to_dataset,
                    )
                    continue
                logger.info(
                    "Including inactive Fabric relationship '%s' -> '%s' referenced by USERELATIONSHIP.",
                    rel.from_dataset,
                    rel.to_dataset,
                )

            from_alias = dataset_aliases.get(rel.from_dataset)
            to_alias = dataset_aliases.get(rel.to_dataset)

            if not getattr(rel, "is_active", True):
                role_alias = role_aliases.get(_relationship_signature(rel))
                if role_alias:
                    to_alias = role_alias

            if not from_alias or not to_alias or not rel.from_columns:
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"Unresolvable aliases (from={from_alias}, to={to_alias}) or missing from_columns."
                )
                _missing_tables = [
                    ds for ds, al in ((rel.from_dataset, from_alias), (rel.to_dataset, to_alias))
                    if not al and ds not in dataset_by_name
                ]
                if _missing_tables and rel.from_columns:
                    # The table itself is not part of the semantic view (it was
                    # excluded upstream, e.g. an unmaterialized calculated
                    # table, with its own drop record) -- the relationship
                    # cannot exist without it.
                    self.drop_ledger.record(
                        "relationship", f"{rel.from_dataset} -> {rel.to_dataset}", DropStage.DDL_EMISSION,
                        f"Related table {', '.join(map(str, _missing_tables))} is not in the semantic view "
                        "(excluded earlier -- see its table drop), so this relationship cannot be emitted.",
                        dataset=rel.from_dataset,
                        by_design=True,
                    )
                    continue
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
            if to_cols and getattr(rel, "is_active", True):
                to_phys = dataset_col_lookup.get(rel.to_dataset, set())
                mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_cols[0].upper()))
                if mapped_to_alias:
                    to_alias = mapped_to_alias
                
                to_phys_upper = {c.upper() for c in to_phys}
                if to_phys:
                    validated_to = [c for c in to_cols if c.upper() in to_phys_upper]
                    if validated_to:
                        to_cols = validated_to

            # T8: Verify target columns are declared PRIMARY KEY or UNIQUE on to_alias
            if to_cols:
                to_cols_upper = [c.upper() for c in to_cols]
                all_target_keys = declared_pk_by_alias.get(f"__keys__{to_alias}")
                if all_target_keys is None:
                    pk = declared_pk_by_alias.get(to_alias)
                    all_target_keys = [pk] if pk else []
                is_valid_target = any(
                    [k.upper() for k in key] == to_cols_upper
                    for key in all_target_keys
                )
                if not is_valid_target:
                    _card = getattr(rel, "cardinality", None)
                    _card_str = str(getattr(_card, "value", _card) or "").lower().replace("_", "-").replace(" ", "")
                    if _card_str in ("many-to-many", "manytomany", "m:n", "n:n", "*:*", "m:m"):
                        # Power BI many-to-many: neither side is unique by the
                        # model's own rule, and a semantic-view relationship must
                        # reference a PRIMARY KEY/UNIQUE key -- not expressible.
                        self.drop_ledger.record(
                            "relationship",
                            f"{rel.from_dataset} -> {rel.to_dataset}",
                            DropStage.DDL_EMISSION,
                            f"Many-to-many relationship on {to_cols}: neither side is unique in the model, and a "
                            "Snowflake semantic-view relationship must reference a PRIMARY KEY or UNIQUE key.",
                            dataset=rel.from_dataset,
                            by_design=True,
                        )
                        continue
                    logger.warning(
                        "Dropping relationship '%s' -> '%s' on %s: target columns are not a declared PRIMARY KEY or UNIQUE on '%s'.",
                        rel.from_dataset, rel.to_dataset, to_cols, to_alias,
                    )
                    self.drop_ledger.record(
                        "relationship",
                        f"{rel.from_dataset} -> {rel.to_dataset}",
                        DropStage.DDL_EMISSION,
                        f"Target column(s) {to_cols} not declared as PRIMARY KEY or UNIQUE on {to_alias} (uniqueness not proven).",
                        dataset=rel.from_dataset,
                        # Snowflake limitation: a relationship must reference a
                        # PRIMARY KEY/UNIQUE key -- expected, not a bug.
                        by_design=True,
                    )
                    continue


            quoted_to_cols = [f'"{c}"' for c in to_cols]
            ref_clause = f'{to_alias} ({", ".join(quoted_to_cols)})' if to_cols else to_alias
            rel_name = self.sanitizer.to_snowflake_relationship_name(getattr(rel, "unique_name", "") or "")
            from_ref = ", ".join([f'"{c}"' for c in from_cols])
            
            if rel_name:
                rel_lines.append(f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            else:
                rel_lines.append(f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            rel_meta.append((from_alias, to_alias, bool(getattr(rel, "is_active", True)), rel))

        # Snowflake rejects relationship cycles and multiple join paths
        # between the same pair of tables. Drop the lowest-priority edge of
        # each conflict (inactive relationships first, then later ones) with a
        # by-design reason instead of letting the whole view fail.
        from semabridge.connectors.snowflake_emitter_parts.semantic_view_items import (
            select_conflicting_relationships,
        )
        conflicts = select_conflicting_relationships(
            [(f, t) for f, t, _a, _r in rel_meta],
            # Priority: real active < real inactive < inferred (not in source),
            # so a relationship SemaBridge guessed is always dropped first.
            [
                2 if getattr(r, "is_inferred", False) else (0 if active else 1)
                for _f, _t, active, r in rel_meta
            ],
        )
        if conflicts:
            kept = []
            for i, line in enumerate(rel_lines):
                if i not in conflicts:
                    kept.append(line)
                    continue
                _f, _t, _active, rel = rel_meta[i]
                logger.warning("Dropping relationship '%s' -> '%s': %s", rel.from_dataset, rel.to_dataset, conflicts[i])
                self.drop_ledger.record(
                    "relationship", f"{rel.from_dataset} -> {rel.to_dataset}", DropStage.DDL_EMISSION,
                    conflicts[i] + ("" if _active else " (inactive relationship dropped first)"),
                    dataset=rel.from_dataset,
                    by_design=True,
                )
            rel_lines = kept
        return rel_lines