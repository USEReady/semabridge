from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from semabridge.repository.orm.models import RelationshipRow, SnapshotRow
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def purge_old_relationships(session: Session, *, days: int = 90, project_id: Optional[str] = None) -> int:
    """Purge RelationshipRow entries whose associated snapshot is older than `days`.

    Args:
        session: SQLAlchemy Session (caller-managed transaction)
        days: retention window in days (rows older than this will be deleted)
        project_id: optional project_id to limit purge to a single project

    Returns:
        Number of relationship rows deleted.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Build subquery to select old snapshot ids
    sq = select(SnapshotRow.snapshot_id).where(SnapshotRow.timestamp < cutoff)
    if project_id:
        sq = sq.where(SnapshotRow.project_id == project_id)

    # Delete relationship rows referencing those snapshots
    stmt = delete(RelationshipRow).where(RelationshipRow.snapshot_id.in_(sq))
    result = session.execute(stmt)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise

    deleted = result.rowcount if hasattr(result, "rowcount") else 0
    logger.info("Purged %s relationship rows older than %s days%s", deleted, days, f" for project {project_id}" if project_id else "")
    return int(deleted or 0)
