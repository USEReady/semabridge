"""Builder for Snowflake semantic-view RELATIONSHIPS clause."""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Set, Tuple

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class BidirectionalFilterWarning(UserWarning):
    """
    Raised when a relationship uses bi-directional cross-filtering.

    Snowflake Semantic Views do not enforce bi-directional filter propagation
    at the DDL level.  If a Power BI model relies on this for a calculation,
    query results against the Snowflake view may differ unless the consuming
    BI tool handles the reverse join explicitly.
    """


class RelationshipsClauseBuilder:
    """Handles construction and validation of the RELATIONSHIPS clause."""

    def __init__(self, identifier_sanitizer: Any, schema_manager: Any, sanitizer: Any):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.sanitizer = sanitizer

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
        # Track (from_dataset, to_dataset) pairs already emitted so we never
        # declare two relationships between the same table pair — Snowflake
        # would either reject the DDL or produce an ambiguous join path.
        emitted_pairs: Set[Tuple[str, str]] = set()

        for rel in relationships:
            # Inactive relationships (e.g. USERELATIONSHIP() alternates in Power BI)
            # have no equivalent in Snowflake Semantic Views.  Emitting them would
            # create duplicate or ambiguous join paths, so we skip them entirely.
            if not rel.is_active:
                logger.info(
                    "Skipping inactive relationship '%s' -> '%s': "
                    "Snowflake Semantic Views do not support inactive/alternate relationships. "
                    "Use USERELATIONSHIP() logic in DAX measures instead.",
                    rel.from_dataset,
                    rel.to_dataset,
                )
                continue

            if rel.from_dataset not in dataset_by_name or rel.to_dataset not in dataset_by_name:
                logger.info(
                    "Skipping relationship '%s' -> '%s': endpoint outside current dataset scope.",
                    rel.from_dataset,
                    rel.to_dataset,
                )
                continue

            # Bi-directional cross-filtering: Snowflake Semantic Views do not
            # enforce reverse filter propagation at the DDL level.  The DDL is
            # emitted unchanged, but we surface a warning so developers know
            # that calculations relying on bi-directional filtering may produce
            # different numbers when queried directly from Snowflake.
            from semabridge.sml.models import CrossFilterDirection  # local import avoids circular deps
            if getattr(rel, "cross_filter", None) == CrossFilterDirection.BOTH:
                msg = (
                    f"Relationship '{rel.from_dataset}' -> '{rel.to_dataset}' uses "
                    "bi-directional cross-filtering. Snowflake Semantic Views do not "
                    "enforce reverse filter propagation natively; measures that depend "
                    "on this behaviour may return different results when queried directly "
                    "from Snowflake unless the consuming BI tool handles the reverse join."
                )
                warnings.warn(msg, BidirectionalFilterWarning, stacklevel=2)
                logger.warning(msg)

            # Many-to-many (bridge table) guard: Snowflake expects standard
            # dimensional modelling (Fact -> Dimension).  Bridge tables are
            # accepted as long as we don't accidentally declare a PK on a
            # column that is not unique in that table.  We detect M:M
            # cardinality and log a notice so the caller can verify uniqueness.
            from semabridge.sml.models import Cardinality  # local import avoids circular deps
            if getattr(rel, "cardinality", None) == Cardinality.MANY_TO_MANY:
                logger.info(
                    "Many-to-many relationship detected: '%s' -> '%s'. "
                    "Ensure the join column on '%s' is unique (i.e. it is a true bridge/dimension key) "
                    "before declaring it as a PRIMARY KEY in the Snowflake Semantic View. "
                    "Snowflake will accept the relationship DDL, but an incorrect PK declaration "
                    "on a non-unique column will produce incorrect query results.",
                    rel.from_dataset,
                    rel.to_dataset,
                    rel.to_dataset,
                )

            # Guard against duplicate active relationships between the same table pair.
            # This can happen when a model has multiple active paths (e.g. role-playing
            # dimensions) that were not fully de-activated in the source.
            pair = (rel.from_dataset, rel.to_dataset)
            if pair in emitted_pairs:
                logger.warning(
                    "Skipping duplicate active relationship '%s' -> '%s': "
                    "a relationship between these tables has already been emitted. "
                    "Snowflake does not support multiple join paths between the same table pair.",
                    rel.from_dataset,
                    rel.to_dataset,
                )
                continue

            from_alias = dataset_aliases.get(rel.from_dataset)
            to_alias = dataset_aliases.get(rel.to_dataset)

            if not from_alias or not to_alias or not rel.from_columns:
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"Unresolvable aliases (from={from_alias}, to={to_alias}) or missing from_columns."
                )
                continue

            from_ds = dataset_by_name.get(rel.from_dataset)
            to_ds = dataset_by_name.get(rel.to_dataset)
            
            if is_osi:
                from_col = self.identifier_sanitizer.sanitize_column(rel.from_columns[0])
                to_col = self.identifier_sanitizer.sanitize_column(rel.to_columns[0]) if rel.to_columns else ""
            else:
                from_col = (
                    self.schema_manager._resolve_physical_column_name(from_ds, rel.from_columns[0])
                    if from_ds else self.identifier_sanitizer.sanitize_column(rel.from_columns[0])
                )
                to_col = (
                    self.schema_manager._resolve_physical_column_name(to_ds, rel.to_columns[0])
                    if (to_ds and rel.to_columns)
                    else (self.identifier_sanitizer.sanitize_column(rel.to_columns[0]) if rel.to_columns else "")
                )

            if not from_col:
                logger.warning(
                    f"Skipping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"from_col is empty after resolution."
                )
                continue

            # Validate FK column exists
            from_phys = dataset_col_lookup.get(rel.from_dataset, set())
            if from_phys and from_col not in from_phys:
                fallback_fk = sorted(from_phys)[0]
                logger.warning(
                    f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                    f"FK column '{from_col}' is not physical in '{rel.from_dataset}'. "
                    f"Using '{fallback_fk}'."
                )
                from_col = fallback_fk

            # Validate PK reference
            if to_col:
                to_phys = dataset_col_lookup.get(rel.to_dataset, set())
                mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_col.upper()))
                if mapped_to_alias:
                    to_alias = mapped_to_alias
                
                declared_pk_cols = declared_pk_by_alias.get(to_alias, [])
                to_phys_upper = {c.upper() for c in to_phys}
                
                if to_phys and to_col.upper() not in to_phys_upper:
                    fallback_to = declared_pk_cols[0] if declared_pk_cols else sorted(to_phys)[0]
                    logger.warning(
                        f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                        f"referenced column '{to_col}' is not physical in '{rel.to_dataset}'. "
                        f"Using '{fallback_to}'."
                    )
                    to_col = fallback_to
                    mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_col.upper()))
                    if mapped_to_alias:
                        to_alias = mapped_to_alias
                    declared_pk_cols = declared_pk_by_alias.get(to_alias, declared_pk_cols)

                if declared_pk_cols and to_col.upper() not in {c.upper() for c in declared_pk_cols}:
                    fallback_to = declared_pk_cols[0]
                    logger.warning(
                        f"Remapping relationship '{rel.from_dataset}' -> '{rel.to_dataset}': "
                        f"referenced column '{to_col}' is not the declared PK {declared_pk_cols} "
                        f"for '{rel.to_dataset}'. Using '{fallback_to}'."
                    )
                    to_col = fallback_to
                    mapped_to_alias = relationship_target_alias.get((rel.to_dataset, to_col.upper()))
                    if mapped_to_alias:
                        to_alias = mapped_to_alias

            ref_clause = f'{to_alias} ("{to_col}")' if to_col else to_alias
            rel_name = self.sanitizer.to_snowflake_relationship_name(getattr(rel, "unique_name", "") or "")
            from_ref = f'"{from_col}"'
            
            if rel_name:
                rel_lines.append(f'  {rel_name} AS {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            else:
                rel_lines.append(f'  {from_alias} ({from_ref}) REFERENCES {ref_clause}')
            emitted_pairs.add(pair)
        
        return rel_lines
