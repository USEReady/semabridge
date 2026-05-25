"""
Retention Policy Service - Safe snapshot pruning with reference tracking.

Implements user-configurable retention policies per project with safe-delete logic
that never removes snapshots still referenced by any run.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Set, Dict, Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from semabridge.repository.orm.models import (
    RetentionPolicy,
    SnapshotRow,
    Run,
)
from semabridge.repository.orm.session_factory import db_manager

logger = logging.getLogger(__name__)


def _get_all_referenced_snapshot_ids(session: Session) -> Set[str]:
    """
    Collect every snapshot ID referenced anywhere in the runs table.
    
    Checks both direct FK columns and JSONB array memberships:
    - before_src_snapshot_id
    - restore_snapshot_id  
    - before_tgt_snapshots (JSON array)
    - after_tgt_snapshots (JSON array)
    """
    referenced: Set[str] = set()

    runs = session.execute(select(Run)).scalars().all()

    def _extract_snapshot_ids_from_field(field_value) -> Set[str]:
        ids: Set[str] = set()
        if not field_value:
            return ids

        # If it's already a Python structure, normalise it to a list
        candidates = None
        if isinstance(field_value, (list, tuple)):
            candidates = list(field_value)
        elif isinstance(field_value, dict):
            candidates = [field_value]
        elif isinstance(field_value, str):
            # Could be a raw id or a JSON-encoded list/dict
            try:
                parsed = json.loads(field_value)
                if isinstance(parsed, (list, tuple)):
                    candidates = list(parsed)
                elif isinstance(parsed, dict):
                    candidates = [parsed]
                elif isinstance(parsed, str):
                    candidates = [parsed]
                else:
                    return ids
            except (json.JSONDecodeError, TypeError):
                # Treat the string as a plain snapshot id
                candidates = [field_value]
        else:
            return ids

        for item in candidates:
            if isinstance(item, dict):
                # Common key names that may carry an id
                sid = item.get("snapshot_id") or item.get("id") or item.get("snapshot")
                if isinstance(sid, str):
                    ids.add(sid)
            elif isinstance(item, str):
                ids.add(item)

        return ids

    for run in runs:
        # Check direct FK columns (model uses these canonical names)
        if getattr(run, "before_src_snapshot_id", None):
            referenced.add(run.before_src_snapshot_id)
        if getattr(run, "restored_from_snapshot_id", None):
            referenced.add(run.restored_from_snapshot_id)

        # Check JSON/text array fields (canonical names)
        for json_field in (
            getattr(run, "before_target_snapshot_ids", None),
            getattr(run, "after_target_snapshot_ids", None),
        ):
            try:
                ids = _extract_snapshot_ids_from_field(json_field)
                referenced.update(ids)
            except Exception:
                logger.debug("Failed to parse JSON field for run %s: %r", run.run_id, json_field)
                continue

    return referenced


def apply_retention_policy(
    session: Session,
    project_id: str,
    connector_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Apply retention policy for a project.
    
    Args:
        session: Database session.
        project_id: Project ID to apply policy for.
        connector_id: Deprecated and ignored. Retention is project-wide.
    
    Returns:
        Dictionary with pruning statistics.
    """
    # Fetch policy
    policy = session.execute(
        select(RetentionPolicy).where(RetentionPolicy.project_id == project_id)
    ).scalar_one_or_none()
    
    # No policy or unlimited strategy = no pruning
    if not policy or policy.strategy == "unlimited":
        logger.info("No retention policy or unlimited strategy for project %s", project_id)
        return {"pruned": 0, "reason": "No policy or unlimited strategy"}
    
    # Build base query for snapshots
    query = select(SnapshotRow).where(
        SnapshotRow.project_id == project_id,
        SnapshotRow.deleted_at.is_(None),  # Only active snapshots
    )
    
    snapshots = session.execute(query).scalars().all()
    
    if not snapshots:
        return {"pruned": 0, "reason": "No snapshots found"}
    
    # Get all referenced snapshot IDs (CRITICAL: never prune these)
    referenced_ids = _get_all_referenced_snapshot_ids(session)
    
    to_prune: List[str] = []
    
    if policy.strategy == "count":
        # Keep only max_snapshots_per_connector most recent across the project
        all_snaps = sorted(
            snapshots, 
            key=lambda s: s.timestamp or datetime.min, 
            reverse=True
        )
        max_count = policy.max_snapshots_per_connector or 0
        to_keep = {s.snapshot_id for s in all_snaps[:max_count]}
        to_prune = [
            s.snapshot_id for s in all_snaps 
            if s.snapshot_id not in to_keep and s.snapshot_id not in referenced_ids
        ]
    
    elif policy.strategy == "days":
        # Prune snapshots older than max_age_days
        if policy.max_age_days:
            cutoff = datetime.utcnow() - timedelta(days=policy.max_age_days)
            to_prune = [
                s.snapshot_id for s in snapshots 
                if s.timestamp and s.timestamp < cutoff and s.snapshot_id not in referenced_ids
            ]
    
    # Soft-delete: set deleted_at, do NOT hard-delete
    if to_prune:
        stmt = update(SnapshotRow).where(
            SnapshotRow.snapshot_id.in_(to_prune)
        ).values(deleted_at=datetime.utcnow())
        session.execute(stmt)
        session.commit()
        logger.info("Pruned %d snapshots for project %s", len(to_prune), project_id)
    
    return {
        "pruned": len(to_prune),
        "kept": len(snapshots) - len(to_prune),
        "referenced": len([s for s in snapshots if s.snapshot_id in referenced_ids]),
        "status": "success"
    }


def apply_retention_policy_for_connector(
    session: Session,
    project_id: str,
    connector_id: str,
    policy: RetentionPolicy,
) -> Dict[str, Any]:
    """Backward-compatible wrapper for legacy connector-scoped callers."""
    logger.debug(
        "Connector-scoped retention is no longer supported; applying project-wide policy for %s",
        project_id,
    )
    return apply_retention_policy(session, project_id)


def set_retention_policy(
    session: Session,
    project_id: str,
    strategy: str = "unlimited",
    max_snapshots_per_connector: Optional[int] = None,
    max_age_days: Optional[int] = None,
    prune_manual_snapshots: bool = False,
) -> RetentionPolicy:
    """
    Set or update retention policy for a project.
    
    Args:
        session: Database session.
        project_id: Project ID.
        strategy: One of 'count', 'days', 'unlimited'.
        max_snapshots_per_connector: Max snapshots to keep for the project when using 'count'.
        max_age_days: Max age in days (for 'days' strategy).
        prune_manual_snapshots: Whether to prune manual snapshots.
    
    Returns:
        Created or updated RetentionPolicy.
    """
    if strategy not in ("count", "days", "unlimited"):
        raise ValueError(f"Invalid strategy: {strategy}. Must be 'count', 'days', or 'unlimited'")
    
    policy = session.execute(
        select(RetentionPolicy).where(RetentionPolicy.project_id == project_id)
    ).scalar_one_or_none()
    
    if policy:
        # Update existing
        policy.strategy = strategy
        policy.max_snapshots_per_connector = max_snapshots_per_connector
        policy.max_age_days = max_age_days
        policy.prune_manual_snapshots = prune_manual_snapshots
        policy.updated_at = datetime.utcnow()
    else:
        # Create new
        policy = RetentionPolicy(
            id=str(uuid.uuid4()),
            project_id=project_id,
            strategy=strategy,
            max_snapshots_per_connector=max_snapshots_per_connector,
            max_age_days=max_age_days,
            prune_manual_snapshots=prune_manual_snapshots,
        )
        session.add(policy)
    
    session.commit()
    return policy


def get_retention_policy(
    session: Session,
    project_id: str,
) -> Optional[RetentionPolicy]:
    """Get retention policy for a project."""
    return session.execute(
        select(RetentionPolicy).where(RetentionPolicy.project_id == project_id)
    ).scalar_one_or_none()
