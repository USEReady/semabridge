import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from semabridge.repository.orm.session_factory import db_manager
from semabridge.repository.orm.models import SnapshotRow, Run
from sqlalchemy import select, delete, update, func, or_

logger = logging.getLogger("semabridge.version_control")

class VersionControlBackend:
    """
    Advanced version control backend logic, isolated from core sync pipelines.
    Handles retention policies, snapshot pruning, and history analysis.
    """
    
    @staticmethod
    async def apply_retention_policy(project_id: str, days_to_keep: int = 30, min_versions_to_keep: int = 5):
        """
        Prune old snapshots for a project while keeping a minimum number of recent versions.
        Soft-deletes snapshots by setting deleted_at.
        """
        session = db_manager._session()
        try:
            # 1. Get all snapshots for project, ordered by timestamp
            stmt = (
                select(SnapshotRow)
                .where(SnapshotRow.project_id == project_id)
                .where(SnapshotRow.deleted_at == None)
                .order_by(SnapshotRow.timestamp.desc())
            )
            snapshots = session.execute(stmt).scalars().all()
            
            if len(snapshots) <= min_versions_to_keep:
                return {"pruned": 0, "kept": len(snapshots), "reason": "Below minimum count"}
            
            cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
            to_prune_ids = []
            
            # Keep the first N versions regardless of age
            for idx, snap in enumerate(snapshots):
                if idx < min_versions_to_keep:
                    continue
                
                # Check if older than cutoff
                if snap.timestamp < cutoff_date:
                    # Double check it's not a "pinned" version or referenced by a recent run
                    # (Simplified for now: just prune)
                    to_prune_ids.append(snap.snapshot_id)
            
            if to_prune_ids:
                stmt = (
                    update(SnapshotRow)
                    .where(SnapshotRow.snapshot_id.in_(to_prune_ids))
                    .values(deleted_at=datetime.utcnow().isoformat())
                )
                session.execute(stmt)
                session.commit()
                
            return {
                "pruned": len(to_prune_ids),
                "kept": len(snapshots) - len(to_prune_ids),
                "status": "success"
            }
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
            stmt = (
                select(
                    func.count(SnapshotRow.snapshot_id),
                    func.sum(func.length(func.cast(SnapshotRow.sml_blob, SnapshotRow.sml_blob.type))) # Rough estimate
                )
                .where(SnapshotRow.project_id == project_id)
                .where(SnapshotRow.deleted_at == None)
            )
            count, size_bytes = session.execute(stmt).first() or (0, 0)
            return {
                "snapshot_count": count or 0,
                "estimated_size_kb": round((size_bytes or 0) / 1024, 2),
                "project_id": project_id
            }
        finally:
            session.close()

# Singleton instance
version_backend = VersionControlBackend()
