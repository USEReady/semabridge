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

            if is_osi:
                from_cols = [self.identifier_sanitizer.sanitize_column(c) for c in rel.from_columns]
                to_cols = [self.identifier_sanitizer.sanitize_column(c) for c in (rel.to_columns or [])]
            else:
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
                if not validated_from:
                    fallback_fk = sorted(from_phys)[0]
                    logger.warning(
                        f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                        f"FK columns {from_cols} not physical in '{rel.from_dataset}'. "
                        f"Using '{fallback_fk}'."
                    )
                    from_cols = [fallback_fk]
                else:
                    from_cols = validated_from

            # Validate PK reference
            if to_cols:
                to_phys = dataset_col_lookup.get(rel.to_dataset, set())
                mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_cols[0].upper()))
                if mapped_to_alias:
                    to_alias = mapped_to_alias
                
                declared_pk_cols = declared_pk_by_alias.get(to_alias, [])
                to_phys_upper = {c.upper() for c in to_phys}
                if to_phys:
                    validated_to = [c for c in to_cols if c.upper() in to_phys_upper]
                    if not validated_to:
                        fallback_to = declared_pk_cols[0] if declared_pk_cols else sorted(to_phys)[0]
                        logger.warning(
                            f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                            f"referenced columns {to_cols} not physical in '{rel.to_dataset}'. "
                            f"Using '{fallback_to}'."
                        )
                        to_cols = [fallback_to]
                    else:
                        to_cols = validated_to

                if declared_pk_cols:
                    declared_upper = {c.upper() for c in declared_pk_cols}
                    if not all(c.upper() in declared_upper for c in to_cols):
                        fallback_to = declared_pk_cols[0]
                        logger.warning(
                            f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                            f"referenced columns {to_cols} are not declared PK {declared_pk_cols} "
                            f"for '{rel.to_dataset}'. Using '{fallback_to}'."
                        )
                        to_cols = [fallback_to]
                        mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_cols[0].upper()))
                        if mapped_to_alias:
                            to_alias = mapped_to_alias

            quoted_to_cols = [f'"{c}"' for c in to_cols]
            ref_clause = f'{to_alias} ({", ".join(quoted_to_cols)})' if to_cols else to_alias
            rel_name = self.sanitizer.to_snowflake_relationship_name(getattr(rel, "unique_name", "") or "")
            from_ref = ", ".join([f'"{c}"' for c in from_cols])
            
            if rel_name:
                rel_lines.append(f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            else:
                rel_lines.append(f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}')

            # Snowflake's semantic-view RELATIONSHIPS grammar has no
            # cardinality/cross-filter-direction keyword -- it's always
            # emitted as a plain REFERENCES join above regardless of what
            # rel.cardinality/rel.cross_filter say, so a many-to-many
            # relationship (or a bidirectional one, which behaves like
            # many-to-many for join fan-out purposes since either side can
            # match multiple rows on the other) can silently multiply rows
            # for any metric that ends up joined across it, with no signal
            # anywhere today that this risk exists. Log-only, advisory:
            # nothing here changes the emitted DDL or drops anything, since
            # there's no way to represent the risk IN the DDL itself.
            # .value first: Cardinality/CrossFilterDirection are `str, Enum`
            # mixins, and plain str(enum_member) renders as "Cardinality.
            # MANY_TO_MANY" (the ClassName.MEMBER form), not the actual
            # "many-to-many" value, on this codebase's Python version --
            # str()-ing the enum directly would silently never match below.
            # getattr(..., "value", ...) also degrades safely for a plain
            # string or None (neither has .value, so the default is used).
            cardinality_raw = getattr(rel, "cardinality", None)
            cardinality = str(getattr(cardinality_raw, "value", cardinality_raw) or "").lower()
            cross_filter_raw = getattr(rel, "cross_filter", None)
            cross_filter = str(getattr(cross_filter_raw, "value", cross_filter_raw) or "").lower()
            if "many-to-many" in cardinality or "both" in cross_filter:
                logger.warning(
                    "Relationship '%s' -> '%s' is %s (cross_filter=%s). Snowflake semantic "
                    "views have no cardinality-aware join semantics, so metrics that get "
                    "joined across this relationship at query time may double-count rows "
                    "due to fan-out. No automatic fix exists for this -- consider a bridge/"
                    "junction table or a precomputed aggregate if this shows up in results.",
                    rel.from_dataset, rel.to_dataset, cardinality or "many-to-many", cross_filter,
                )

        return rel_lines
