"""Helper module for normalizing relationships and handling role-playing aliases during Snowflake emission."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.utils.logger import get_logger
from semabridge.core.drop_ledger import DropLedger, DropStage

logger = get_logger(__name__)


@dataclass
class EmissionRelationship:
    """A relationship prepared and normalized for Snowflake DDL emission."""
    original_rel: Any
    from_dataset: str
    from_columns: List[str]
    to_dataset: str
    to_columns: List[str]
    cardinality: str  # "many-to-one", "one-to-one", "many-to-many"
    is_active: bool
    is_role_playing: bool = False
    role_alias: Optional[str] = None
    is_skipped: bool = False
    skip_reason: Optional[str] = None
    note: Optional[str] = None


@dataclass
class RolePlayingTable:
    """A logical table alias representing a role of a dimension table."""
    role_alias: str
    base_dataset: str
    pk_columns: List[str]
    from_fact: str
    fact_column: str


class RelationshipEmissionPlanner:
    """Plans relationship emission: swaps 1:M to M:1, identifies 1:1,
    skips M:M with reason, detects role-playing relationships and assigns role aliases,
    and skips non-USERELATIONSHIP inactive relationships.
    """

    def __init__(
        self,
        identifier_sanitizer: Any,
        drop_ledger: Optional[DropLedger] = None,
    ) -> None:
        self.identifier_sanitizer = identifier_sanitizer
        self.drop_ledger: DropLedger = drop_ledger if drop_ledger is not None else DropLedger()
        self.m2m_datasets: Set[str] = set()

    def plan(
        self,
        datasets: List[Any],
        relationships: List[Any],
        metrics: Optional[List[Any]] = None,
    ) -> Tuple[List[EmissionRelationship], List[RolePlayingTable], Dict[str, List[str]], Dict[str, str]]:
        """Plan and normalize relationships for emission.

        Returns:
            - emission_relationships: List of EmissionRelationship objects
            - role_tables: List of RolePlayingTable objects
            - relationship_pk_map: dict mapping table_alias/dataset -> list of PK column names
            - userelationship_measure_map: dict mapping metric_name -> role_alias to rewrite
        """
        metrics = metrics or []

        # 1. Extract USERELATIONSHIP references from metrics
        # Matches: USERELATIONSHIP(Fact[Col1], Dim[Col2]) or USERELATIONSHIP('Fact'[Col1], 'Dim'[Col2])
        userel_pairs: Set[Tuple[str, str, str, str]] = set()
        userel_pattern = re.compile(
            r"USERELATIONSHIP\s*\(\s*'?([a-zA-Z0-9_ ]+)'?\[([a-zA-Z0-9_ ]+)\]\s*,\s*'?([a-zA-Z0-9_ ]+)'?\[([a-zA-Z0-9_ ]+)\]\s*\)",
            re.IGNORECASE,
        )
        for m in metrics:
            expr = getattr(m, "expression", "") or ""
            sql_expr = getattr(m, "sql_expression", "") or ""
            combined = f"{expr} {sql_expr}"
            for t1, c1, t2, c2 in userel_pattern.findall(combined):
                userel_pairs.add((t1.strip().upper(), c1.strip().upper(), t2.strip().upper(), c2.strip().upper()))
                userel_pairs.add((t2.strip().upper(), c2.strip().upper(), t1.strip().upper(), c1.strip().upper()))

        # Filter out synthetic date tables
        valid_rels = [
            r for r in relationships
            if not (
                str(getattr(r, "from_dataset", "") or "").startswith(("LocalDateTable_", "DateTableTemplate_"))
                or str(getattr(r, "to_dataset", "") or "").startswith(("LocalDateTable_", "DateTableTemplate_"))
            )
        ]

        normalized: List[EmissionRelationship] = []

        for rel in valid_rels:
            from_ds = getattr(rel, "from_dataset", "") or ""
            to_ds = getattr(rel, "to_dataset", "") or ""
            from_cols = list(getattr(rel, "from_columns", []) or [])
            to_cols = list(getattr(rel, "to_columns", []) or [])
            is_active = bool(getattr(rel, "is_active", True))

            card_obj = getattr(rel, "cardinality", "")
            card_raw = str(getattr(card_obj, "value", card_obj) or "").strip().lower()
            card_clean = card_raw.split(".")[-1].replace("_", "-")

            cf_obj = (
                getattr(rel, "cross_filter_direction", None)
                or getattr(rel, "cross_filtering_behavior", None)
                or getattr(rel, "cross_filter", None)
                or ""
            )
            cf_raw = str(getattr(cf_obj, "value", cf_obj) or "").strip().lower()
            cf_clean = cf_raw.split(".")[-1].replace("_", "").replace("-", "")

            # Direction normalization: 1:M becomes M:1 (swap sides)
            is_1_to_m = card_clean in ("one-to-many", "1:m", "onetomany", "1m")
            if is_1_to_m:
                norm_from_ds, norm_to_ds = to_ds, from_ds
                norm_from_cols, norm_to_cols = to_cols, from_cols
                norm_card = "many-to-one"
            elif card_clean in ("one-to-one", "1:1", "onetoone", "11"):
                norm_from_ds, norm_to_ds = from_ds, to_ds
                norm_from_cols, norm_to_cols = from_cols, to_cols
                norm_card = "one-to-one"
            elif card_clean in ("many-to-many", "m:m", "manytomany", "mm"):
                norm_from_ds, norm_to_ds = from_ds, to_ds
                norm_from_cols, norm_to_cols = from_cols, to_cols
                norm_card = "many-to-many"
            else:
                norm_from_ds, norm_to_ds = from_ds, to_ds
                norm_from_cols, norm_to_cols = from_cols, to_cols
                norm_card = "many-to-one"

            # Cross-filter note
            note = None
            if cf_clean in ("both", "bothdirections", "automatic"):
                note = "both-direction filtering isn't carried over to Snowflake semantic views (emitted as many-to-one)"
                self.drop_ledger.record(
                    "relationship",
                    f"{from_ds} -> {to_ds}",
                    DropStage.DDL_EMISSION,
                    note,
                    dataset=from_ds,
                )

            # Many-to-Many handling: skip with visible dry-run reason
            if norm_card == "many-to-many":
                self.m2m_datasets.add(from_ds)
                self.m2m_datasets.add(to_ds)
                self.m2m_datasets.add(norm_from_ds)
                self.m2m_datasets.add(norm_to_ds)
                skip_reason = "many-to-many not supported in Snowflake semantic views; use a bridge table"
                self.drop_ledger.record(
                    "relationship",
                    f"{from_ds} -> {to_ds}",
                    DropStage.DDL_EMISSION,
                    skip_reason,
                    dataset=from_ds,
                )
                normalized.append(
                    EmissionRelationship(
                        original_rel=rel,
                        from_dataset=norm_from_ds,
                        from_columns=norm_from_cols,
                        to_dataset=norm_to_ds,
                        to_columns=norm_to_cols,
                        cardinality=norm_card,
                        is_active=is_active,
                        is_skipped=True,
                        skip_reason=skip_reason,
                        note=note,
                    )
                )
                continue

            normalized.append(
                EmissionRelationship(
                    original_rel=rel,
                    from_dataset=norm_from_ds,
                    from_columns=norm_from_cols,
                    to_dataset=norm_to_ds,
                    to_columns=norm_to_cols,
                    cardinality=norm_card,
                    is_active=is_active,
                    note=note,
                )
            )

        # 2. Group relationships by (from_dataset, to_dataset) to detect role-playing
        active_and_candidates = [r for r in normalized if not r.is_skipped]
        groups: Dict[Tuple[str, str], List[EmissionRelationship]] = {}
        for r in active_and_candidates:
            key = (r.from_dataset.upper(), r.to_dataset.upper())
            groups.setdefault(key, []).append(r)

        role_tables: List[RolePlayingTable] = []
        userel_measure_map: Dict[str, str] = {}

        for (from_key, to_key), rels_in_group in groups.items():
            if len(rels_in_group) == 1:
                rel_item = rels_in_group[0]
                if rel_item.is_active:
                    # Normal active relationship
                    continue
                else:
                    # Inactive relationship alone: check if used by USERELATIONSHIP
                    from_c = rel_item.from_columns[0].upper() if rel_item.from_columns else ""
                    to_c = rel_item.to_columns[0].upper() if rel_item.to_columns else ""
                    is_used = (from_key, from_c, to_key, to_c) in userel_pairs

                    if is_used:
                        # Use via role alias
                        fact_col_str = rel_item.from_columns[0] if rel_item.from_columns else "ROLE"
                        safe_to = self.identifier_sanitizer.sanitize_alias(rel_item.to_dataset)
                        safe_col = self.identifier_sanitizer.sanitize_alias(fact_col_str)
                        role_alias = f"{safe_to}_{safe_col}"

                        rel_item.is_role_playing = True
                        rel_item.role_alias = role_alias
                        rel_item.is_active = True  # Emitted actively to role alias
                        role_tables.append(
                            RolePlayingTable(
                                role_alias=role_alias,
                                base_dataset=rel_item.to_dataset,
                                pk_columns=rel_item.to_columns,
                                from_fact=rel_item.from_dataset,
                                fact_column=fact_col_str,
                            )
                        )
                        # Map metrics calling USERELATIONSHIP for this relationship to the role alias
                        for m in metrics:
                            m_expr = f"{getattr(m, 'expression', '')} {getattr(m, 'sql_expression', '')}".upper()
                            if f"{from_key}[{from_c}]" in m_expr and f"{to_key}[{to_c}]" in m_expr:
                                userel_measure_map[m.unique_name] = role_alias
                    else:
                        # Inactive relationship without USERELATIONSHIP: skip with note
                        rel_item.is_skipped = True
                        rel_item.skip_reason = "Inactive relationship without USERELATIONSHIP measure skipped"
                        self.drop_ledger.record(
                            "relationship",
                            f"{rel_item.from_dataset} -> {rel_item.to_dataset}",
                            DropStage.DDL_EMISSION,
                            rel_item.skip_reason,
                            dataset=rel_item.from_dataset,
                        )
            else:
                # Multiple relationships between same fact and same target table -> Role-Playing!
                # Pick the primary relationship (prefer active; otherwise first)
                active_idx = 0
                for i, r in enumerate(rels_in_group):
                    if r.is_active:
                        active_idx = i
                        break

                for i, rel_item in enumerate(rels_in_group):
                    if i == active_idx:
                        # Main relationship targets base dataset
                        rel_item.is_active = True
                        continue

                    # Role-playing relationship gets a dedicated alias
                    fact_col_str = rel_item.from_columns[0] if rel_item.from_columns else f"ROLE_{i+1}"
                    safe_to = self.identifier_sanitizer.sanitize_alias(rel_item.to_dataset)
                    safe_col = self.identifier_sanitizer.sanitize_alias(fact_col_str)
                    role_alias = f"{safe_to}_{safe_col}"

                    rel_item.is_role_playing = True
                    rel_item.role_alias = role_alias
                    rel_item.is_active = True
                    role_tables.append(
                        RolePlayingTable(
                            role_alias=role_alias,
                            base_dataset=rel_item.to_dataset,
                            pk_columns=rel_item.to_columns,
                            from_fact=rel_item.from_dataset,
                            fact_column=fact_col_str,
                        )
                    )

                    from_c = rel_item.from_columns[0].upper() if rel_item.from_columns else ""
                    to_c = rel_item.to_columns[0].upper() if rel_item.to_columns else ""
                    for m in metrics:
                        m_expr = f"{getattr(m, 'expression', '')} {getattr(m, 'sql_expression', '')}".upper()
                        if f"{from_key}[{from_c}]" in m_expr and f"{to_key}[{to_c}]" in m_expr:
                            userel_measure_map[m.unique_name] = role_alias

        # 3. Build relationship_pk_map
        relationship_pk_map: Dict[str, List[str]] = {}
        for r in normalized:
            if r.is_skipped:
                continue

            target_name = r.role_alias if r.is_role_playing and r.role_alias else r.to_dataset
            if r.to_columns:
                pk_list = relationship_pk_map.setdefault(target_name, [])
                for c in r.to_columns:
                    if c not in pk_list:
                        pk_list.append(c)

            if r.cardinality == "one-to-one" and r.from_columns:
                from_pk_list = relationship_pk_map.setdefault(r.from_dataset, [])
                for c in r.from_columns:
                    if c not in from_pk_list:
                        from_pk_list.append(c)

        return normalized, role_tables, relationship_pk_map, userel_measure_map
