"""Orchestration for history-dataset latest-row snapshot views."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Set

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class HistorySnapshotOrchestrator:
    """Manages generation of helper views for history tables to avoid join fan-out."""

    def __init__(self, identifier_sanitizer: Any, schema_manager: Any, config: Any):
        self.identifier_sanitizer = identifier_sanitizer
        self.schema_manager = schema_manager
        self.config = config

    def build_for_sml(self, sml: Any) -> Tuple[List[str], Dict[str, str]]:
        """Build latest-row views for history datasets in an SML model."""
        return self._build_history_snapshot_ddls(sml.datasets, is_osi=False)

    def build_for_osi(self, osi: Any) -> Tuple[List[str], Dict[str, str]]:
        """Build latest-row views for history datasets in an OSI model."""
        return self._build_history_snapshot_ddls(osi.datasets, is_osi=True)

    def _build_history_snapshot_ddls(
        self,
        datasets: List[Any],
        is_osi: bool = False
    ) -> Tuple[List[str], Dict[str, str]]:
        """Internal implementation for history snapshot generation."""
        history_view_ddls: List[str] = []
        dataset_source_overrides: Dict[str, str] = {}
        emitted_views: Set[str] = set()

        collect_method = (
            self.schema_manager._collect_physical_source_columns_osi
            if is_osi
            else self.schema_manager._collect_physical_source_columns
        )

        for dataset in datasets:
            source_name = getattr(dataset, "source_table", None) or dataset.unique_name
            if not self.is_history_table_name(source_name):
                continue

            physical_columns = list(collect_method(dataset).keys())
            parent_col, timestamp_col = self.resolve_history_snapshot_keys(physical_columns)
            if not parent_col or not timestamp_col:
                logger.info(
                    "History snapshot bypassed for %s dataset '%s': missing parent/timestamp columns",
                    "OSI" if is_osi else "SML",
                    dataset.unique_name,
                )
                continue

            safe_source = self.identifier_sanitizer.sanitize_table_name(source_name)
            safe_latest_view = self.identifier_sanitizer.sanitize_table_name(f"{safe_source}_LATEST")
            if safe_latest_view in emitted_views:
                dataset_source_overrides[dataset.unique_name] = safe_latest_view
                continue

            full_source = f'"{self.config.database}"."{self.config.schema_name}"."{safe_source}"'
            full_latest = f'"{self.config.database}"."{self.config.schema_name}"."{safe_latest_view}"'
            select_cols = ", ".join(f'"{col}"' for col in physical_columns)
            ddl = (
                f"CREATE OR REPLACE VIEW {full_latest} AS\n"
                f"SELECT {select_cols}\n"
                f"FROM {full_source}\n"
                f'QUALIFY ROW_NUMBER() OVER (PARTITION BY "{parent_col}" '
                f'ORDER BY "{timestamp_col}" DESC NULLS LAST) = 1'
            )

            history_view_ddls.append(ddl)
            emitted_views.add(safe_latest_view)
            dataset_source_overrides[dataset.unique_name] = safe_latest_view
            logger.info(
                "%s history dataset '%s' mapped to latest-snapshot view '%s' using (%s, %s)",
                "OSI" if is_osi else "SML",
                dataset.unique_name,
                safe_latest_view,
                parent_col,
                timestamp_col,
            )

        return history_view_ddls, dataset_source_overrides

    @staticmethod
    def is_history_table_name(name: str) -> bool:
        """Return True for Salesforce-like change-history tables."""
        upper = str(name or "").upper()
        return (
            upper.endswith("_HISTORY")
            or upper.endswith("_HIST")
            or upper.endswith("__C_HISTORY")
            or "__HISTORY" in upper
        )

    @staticmethod
    def resolve_history_snapshot_keys(physical_columns: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """Choose parent/timestamp columns for latest-row snapshoting."""
        if not physical_columns:
            return None, None

        by_upper = {str(col).upper(): col for col in physical_columns}

        parent_candidates = (
            "PARENT_ID", "PARENTID", "QUOTE_ID", "RECORD_ID", "ENTITY_ID", "ID",
        )
        timestamp_candidates = (
            "CREATED_DATE", "CREATEDDATE", "LAST_MODIFIED_DATE", "LASTMODIFIEDDATE",
            "SYSTEMMODSTAMP", "MODSTAMP", "TIMESTAMP", "AUDIT_TIMESTAMP", "UPDATE_TIME",
        )

        parent_col = None
        for cand in parent_candidates:
            if cand in by_upper:
                parent_col = by_upper[cand]
                break

        timestamp_col = None
        for cand in timestamp_candidates:
            if cand in by_upper:
                timestamp_col = by_upper[cand]
                break

        return parent_col, timestamp_col
