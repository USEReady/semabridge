from typing import Optional
from fastapi import APIRouter, Query, Body

from semabridge.api.services.versioning_service import (
    compare_model_versions,
    delete_model_versions,
    get_version_snapshot,
    list_model_versions,
    rollback_model_version,
)
from semabridge.api.services.version_control_impl import version_backend

router = APIRouter()
router.get('/api/model-versions')(list_model_versions)
router.get('/api/model-versions/compare')(compare_model_versions)
router.delete('/api/model-versions')(delete_model_versions)
router.get('/api/model-versions/snapshot')(get_version_snapshot)
router.post('/api/model-versions/rollback')(rollback_model_version)

@router.get('/api/version-control/stats')
async def get_vc_stats(project_id: str):
    return await version_backend.get_storage_stats(project_id)

@router.post('/api/version-control/retention')
async def run_retention_policy(
    project_id: str,
    days_to_keep: int = Query(default=30, ge=1),
    min_versions_to_keep: int = Query(default=5, ge=1),
    strategy: str = Query(default="days"),
    max_snapshots: Optional[int] = Query(default=None, ge=1),
    prune_manual: bool = Query(default=False),
):
    """
    Apply retention policy to prune old snapshots.
    
    Args:
        project_id: Project ID.
        days_to_keep: Days to keep (for 'days' strategy).
        min_versions_to_keep: Minimum versions to keep (for 'count' strategy).
        strategy: Policy strategy ('count', 'days', 'unlimited').
        max_snapshots: Max snapshots per connector.
        prune_manual: Whether to prune manual snapshots.
    """
    return await version_backend.apply_retention_policy(
        project_id,
        days_to_keep=days_to_keep,
        min_versions_to_keep=min_versions_to_keep,
        strategy=strategy,
        max_snapshots=max_snapshots,
        prune_manual=prune_manual,
    )

@router.put('/api/version-control/retention-policy')
async def set_retention_policy(
    project_id: str,
    strategy: str = Query(default="unlimited"),
    max_snapshots: Optional[int] = Query(default=None, ge=1),
    max_age_days: Optional[int] = Query(default=None, ge=1),
    prune_manual: bool = Query(default=False),
):
    """
    Set or update retention policy for a project.
    
    Args:
        project_id: Project ID.
        strategy: Policy strategy ('count', 'days', 'unlimited').
        max_snapshots: Max snapshots per connector (for 'count' strategy).
        max_age_days: Max age in days (for 'days' strategy).
        prune_manual: Whether to prune manual snapshots.
    """
    return await version_backend.set_retention_policy(
        project_id,
        strategy=strategy,
        max_snapshots=max_snapshots,
        max_age_days=max_age_days,
        prune_manual=prune_manual,
    )

@router.get('/api/version-control/retention-policy')
async def get_retention_policy(project_id: str):
    """Get retention policy for a project."""
    return await version_backend.get_retention_policy(project_id)
