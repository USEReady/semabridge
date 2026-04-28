import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from semabridge.repository.orm.session_factory import db_manager
from semabridge.api.services.retention_service import (
    apply_retention_policy as apply_retention_policy_impl,
    set_retention_policy as set_retention_policy_impl,
    get_retention_policy as get_retention_policy_impl,
)

logger = logging.getLogger("semabridge.version_control")


class VersionControlBackend:
    """
    Advanced version control backend logic, isolated from core sync pipelines.
    Handles retention policies, snapshot pruning, and history analysis.
    """

    @staticmethod
    async def apply_retention_policy(
        project_id: str,
        days_to_keep: int = 30,
        min_versions_to_keep: int = 5,
        strategy: Optional[str] = None,
        max_snapshots: Optional[int] = None,
        prune_manual: bool = False,
    ):
        """
        Prune old snapshots for a project while respecting retention policy.
        
        Args:
            project_id: Project ID.
            days_to_keep: Days to keep (for 'days' strategy).
            min_versions_to_keep: Minimum versions to keep (for 'count' strategy).
            strategy: Optional strategy override ('count', 'days', 'unlimited').
            max_snapshots: Max snapshots per connector (for 'count' strategy).
            prune_manual: Whether to prune manual snapshots.
        """
        session = db_manager._session()
        try:
            # If strategy is provided, update the policy first
            if strategy:
                from semabridge.api.services.retention_service import set_retention_policy
                set_retention_policy(
                    session, project_id,
                    strategy=strategy,
                    max_snapshots_per_connector=max_snapshots,
                    max_age_days=days_to_keep if strategy == "days" else None,
                    prune_manual_snapshots=prune_manual,
                )
            
            # Apply the policy
            result = apply_retention_policy_impl(session, project_id)
            return result
        except Exception as exc:
            logger.error("Failed to apply retention policy for %s: %s", project_id, exc)
            return {"error": str(exc), "status": "failed"}
        finally:
            session.close()

    @staticmethod
    async def get_storage_stats(project_id: str):
        """Calculate storage impact of snapshots for a project."""
        session = db_manager._session()
        try:
            from sqlalchemy import func, select
            from semabridge.repository.orm.models import SnapshotRow
            
            stmt = select(
                func.count(SnapshotRow.snapshot_id),
                func.sum(func.length(func.cast(SnapshotRow.sml_blob, SnapshotRow.sml_blob.type)))
            ).where(
                SnapshotRow.project_id == project_id,
                SnapshotRow.deleted_at == None
            )
            count, size_bytes = session.execute(stmt).first() or (0, 0)
            return {
                "snapshot_count": count or 0,
                "estimated_size_kb": round((size_bytes or 0) / 1024, 2),
                "project_id": project_id
            }
        finally:
            session.close()

    @staticmethod
    async def set_retention_policy(
        project_id: str,
        strategy: str = "unlimited",
        max_snapshots: Optional[int] = None,
        max_age_days: Optional[int] = None,
        prune_manual: bool = False,
    ):
        """Set retention policy for a project."""
        session = db_manager._session()
        try:
            policy = set_retention_policy_impl(
                session, project_id,
                strategy=strategy,
                max_snapshots_per_connector=max_snapshots,
                max_age_days=max_age_days,
                prune_manual_snapshots=prune_manual,
            )
            return {
                "status": "success",
                "policy": {
                    "project_id": policy.project_id,
                    "strategy": policy.strategy,
                    "max_snapshots_per_connector": policy.max_snapshots_per_connector,
                    "max_age_days": policy.max_age_days,
                    "prune_manual_snapshots": policy.prune_manual_snapshots,
                }
            }
        except Exception as exc:
            logger.error("Failed to set retention policy for %s: %s", project_id, exc)
            return {"error": str(exc), "status": "failed"}
        finally:
            session.close()

    @staticmethod
    async def get_retention_policy(project_id: str):
        """Get retention policy for a project."""
        session = db_manager._session()
        try:
            policy = get_retention_policy_impl(session, project_id)
            if not policy:
                return {"status": "not_found", "project_id": project_id}
            
            return {
                "status": "success",
                "policy": {
                    "project_id": policy.project_id,
                    "strategy": policy.strategy,
                    "max_snapshots_per_connector": policy.max_snapshots_per_connector,
                    "max_age_days": policy.max_age_days,
                    "prune_manual_snapshots": policy.prune_manual_snapshots,
                }
            }
        finally:
            session.close()


# Singleton instance
version_backend = VersionControlBackend()
