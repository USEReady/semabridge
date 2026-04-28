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
    
    for run in runs:
        # Check direct FK columns
        if run.before_src_snapshot_id:
            referenced.add(run.before_src_snapshot_id)
        if run.restore_snapshot_id:
            referenced.add(run.restore_snapshot_id)
        
        # Check JSONB array memberships
        for json_field in [run.before_tgt_snapshots, run.after_tgt_snapshots]:
            if json_field:
                try:
                    # Parse JSON array - could be string or list of dicts
                    data = json.loads(json_field)
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict):
                                # Extract snapshot_id from dict
                                if "snapshot_id" in item:
                                    referenced.add(item["snapshot_id"])
                            elif isinstance(item, str):
                                # Direct string reference
                                referenced.add(item)
                    elif isinstance(data, dict):
                        # Single dict with snapshot_id
                        if "snapshot_id" in data:
                            referenced.add(data["snapshot_id"])
                except (json.JSONDecodeError, TypeError):
                    logger.debug("Failed to parse JSON field for run %s: %s", run.run_id, json_field)
                    continue
    
    return referenced


def apply_retention_policy(
    session: Session,
    project_id: str,
    connector_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Apply retention policy for a project/connector.
    
    Args:
        session: Database session.
        project_id: Project ID to apply policy for.
        connector_id: Optional connector ID for per-connector pruning.
    
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
    
    # Filter by connector if provided
    if connector_id:
        query = query.where(SnapshotRow.connector_id == connector_id)
    elif policy.strategy == "count" and not connector_id:
        # For count strategy without connector, we need to handle per-connector
        # Get all connectors for this project
        connectors = session.execute(
            select(SnapshotRow.connector_id).where(
                SnapshotRow.project_id == project_id,
                SnapshotRow.connector_id.isnot(None)
            ).distinct()
        ).scalars().all()
        
        # Apply policy per connector
        total_pruned = 0
        for conn_id in connectors:
            result = apply_retention_policy_for_connector(
                session, project_id, conn_id, policy
            )
            total_pruned += result.get("pruned", 0)
        
        return {"pruned": total_pruned, "per_connector": True}
    
    # Filter by connector if we have one
    if connector_id:
        query = query.where(SnapshotRow.connector_id == connector_id)
    
    snapshots = session.execute(query).scalars().all()
    
    if not snapshots:
        return {"pruned": 0, "reason": "No snapshots found"}
    
    # Get all referenced snapshot IDs (CRITICAL: never prune these)
    referenced_ids = _get_all_referenced_snapshot_ids(session)
    
    # Filter out manual snapshots if policy says so
    if not policy.prune_manual_snapshots:
        snapshots = [s for s in snapshots if s.trigger != "manual"]
    
    to_prune: List[str] = []
    
    if policy.strategy == "count":
        # Keep only max_snapshots_per_connector most recent
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
    """Apply retention policy for a specific connector."""
    
    # Get snapshots for this connector
    snapshots = session.execute(
        select(SnapshotRow).where(
            SnapshotRow.project_id == project_id,
            SnapshotRow.connector_id == connector_id,
            SnapshotRow.deleted_at.is_(None),
        )
    ).scalars().all()
    
    if not snapshots:
        return {"pruned": 0, "connector_id": connector_id}
    
    # Get all referenced snapshot IDs
    referenced_ids = _get_all_referenced_snapshot_ids(session)
    
    # Filter out manual snapshots if policy says so
    if not policy.prune_manual_snapshots:
        snapshots = [s for s in snapshots if s.trigger != "manual"]
    
    to_prune: List[str] = []
    
    if policy.strategy == "count":
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
        if policy.max_age_days:
            cutoff = datetime.utcnow() - timedelta(days=policy.max_age_days)
            to_prune = [
                s.snapshot_id for s in snapshots
                if s.timestamp and s.timestamp < cutoff and s.snapshot_id not in referenced_ids
            ]
    
    if to_prune:
        stmt = update(SnapshotRow).where(
            SnapshotRow.snapshot_id.in_(to_prune)
        ).values(deleted_at=datetime.utcnow())
        session.execute(stmt)
        session.commit()
    
    return {
        "pruned": len(to_prune),
        "kept": len(snapshots) - len(to_prune),
        "connector_id": connector_id,
    }


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
        max_snapshots_per_connector: Max snapshots per connector (for 'count' strategy).
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
