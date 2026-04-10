from __future__ import annotations

import re
from typing import Any, Callable, Optional

from semabridge.core.behavior import DatabricksBehavior
from semabridge.sml.models import SMLDataset


class DatabricksSourceRelationBuilder:
    """Build source relations and inline source queries for metric artifacts."""

    def __init__(
        self,
        *,
        behavior: DatabricksBehavior,
        sanitize_identifier: Callable[[str], str],
        resolve_physical_source_column: Callable[[SMLDataset, str, str], str],
        get_source_table_columns: Callable[[str], set[str]],
    ) -> None:
        self._behavior = behavior
        self._sanitize_identifier = sanitize_identifier
        self._resolve_physical_source_column = resolve_physical_source_column
        self._get_source_table_columns = get_source_table_columns

    def resolve_dataset_dedup_rule(self, dataset: SMLDataset) -> dict[str, Any]:
        """Resolve an optional dedup rule for a dataset from behavior config."""
        rules = getattr(self._behavior, "source_dedup_rules", {}) or {}
        if not isinstance(rules, dict):
            return {}

        dataset_name = str(dataset.unique_name or "").strip()
        source_name = str(dataset.source_table or "").strip()
        candidates = {
            dataset_name,
            source_name,
            self._sanitize_identifier(dataset_name),
            self._sanitize_identifier(source_name),
            dataset_name.lower(),
            source_name.lower(),
            self._sanitize_identifier(dataset_name).lower(),
            self._sanitize_identifier(source_name).lower(),
        }

        for key, rule in rules.items():
            rule_key = str(key or "").strip()
            if not rule_key:
                continue
            if rule_key in candidates or rule_key.lower() in candidates:
                return rule if isinstance(rule, dict) else {}

        return {}

    def build_source_relation_with_optional_dedup(
        self,
        dataset: SMLDataset,
        source_fq: str,
    ) -> str:
        """Optionally wrap the source relation with QUALIFY ROW_NUMBER dedup logic."""
        rule = self.resolve_dataset_dedup_rule(dataset)
        if not rule:
            return source_fq

        partition_by_raw = rule.get("partition_by")
        order_by_raw = rule.get("order_by")
        if not isinstance(partition_by_raw, list) or not partition_by_raw:
            return source_fq

        partition_cols: list[str] = []
        for col in partition_by_raw:
            col_name = str(col or "").strip()
            if not col_name:
                continue
            resolved = self._resolve_physical_source_column(dataset, col_name, source_fq)
            safe_col = self._sanitize_identifier(resolved or col_name)
            if safe_col:
                partition_cols.append(f"`{safe_col}`")

        if not partition_cols:
            return source_fq

        order_parts: list[str] = []
        if isinstance(order_by_raw, list):
            for raw in order_by_raw:
                part = str(raw or "").strip()
                if not part:
                    continue
                match = re.match(r"^(.+?)\s+(ASC|DESC)$", part, flags=re.IGNORECASE)
                if match:
                    raw_col = str(match.group(1)).strip()
                    direction = str(match.group(2)).upper()
                else:
                    raw_col = part
                    direction = "DESC"
                resolved = self._resolve_physical_source_column(dataset, raw_col, source_fq)
                safe_col = self._sanitize_identifier(resolved or raw_col)
                if safe_col:
                    order_parts.append(f"`{safe_col}` {direction}")

        if not order_parts:
            # Stable fallback when no explicit ordering is configured.
            order_parts = [f"{partition_cols[0]} DESC"]

        partition_clause = ", ".join(partition_cols)
        order_clause = ", ".join(order_parts)
        return (
            "(\n"
            "SELECT *\n"
            f"FROM {source_fq}\n"
            "QUALIFY ROW_NUMBER() OVER (\n"
            f"  PARTITION BY {partition_clause}\n"
            f"  ORDER BY {order_clause}\n"
            ") = 1\n"
            ") AS `semabridge_dedup`"
        )

    def build_metric_view_source_query(
        self,
        bindings: list[Any],
        source_fq: str,
        dataset: Optional[SMLDataset] = None,
    ) -> str:
        """Build an inline source query that aliases physical columns to stable names."""
        physical_cols = self._get_source_table_columns(source_fq)
        select_parts: list[str] = []

        for binding in bindings:
            source_column = str(getattr(binding, "source_column", "") or "")
            projected_name = str(getattr(binding, "projected_name", "") or "")
            if not source_column or not projected_name:
                continue
            if not physical_cols or source_column.lower() in physical_cols:
                select_parts.append(f"  `{source_column}` AS `{projected_name}`")
            else:
                select_parts.append(f"  NULL AS `{projected_name}`")

        if not select_parts:
            return source_fq.replace("`", "")

        select_list = ",\n".join(select_parts)
        source_relation = source_fq
        if dataset is not None:
            source_relation = self.build_source_relation_with_optional_dedup(dataset, source_fq)
        return (
            "SELECT\n"
            f"{select_list}\n"
            f"FROM {source_relation}"
        )
