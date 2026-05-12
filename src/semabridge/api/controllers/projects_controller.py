from fastapi import APIRouter
from typing import List

from semabridge.api.services.projects_service import (
    create_project_compat,
    delete_project_compat,
    get_project_compat,
    get_project_config_compat,
    get_project_dependency_graph_compat,
    get_project_dependency_impact_compat,
    get_project_dependency_resolution_compat,
    premerge_validate_projects_compat,
    preflight_project_contract_change_compat,
    list_projects_compat,
    list_project_discovery_compat,
    patch_project_compat,
    save_project_config_compat,
)
from semabridge.api.services.project_runs_service import (
    capture_manual_snapshots_compat,
    compare_project_snapshots_compat,
    list_project_snapshots_compat,
    list_snapshot_groups_compat,
    restore_project_version_compat,
    get_project_runs_compat,
    delete_project_snapshots_compat,
    apply_project_retention_policy,
    get_project_storage_stats,
)

router = APIRouter()
router.get('/api/projects')(list_projects_compat)
router.get('/api/projects/discovery')(list_project_discovery_compat)
router.get('/api/projects/dependencies/graph')(get_project_dependency_graph_compat)
router.get('/api/projects/{project_id}/dependencies/impact')(get_project_dependency_impact_compat)
router.get('/api/projects/{project_id}/dependencies/resolution')(get_project_dependency_resolution_compat)
router.post('/api/projects/{project_id}/contracts/preflight')(preflight_project_contract_change_compat)
router.post('/api/projects/contracts/premerge-validate')(premerge_validate_projects_compat)
router.post('/api/projects')(create_project_compat)
router.get('/api/projects/{project_id}')(get_project_compat)
router.patch('/api/projects/{project_id}')(patch_project_compat)
router.delete('/api/projects/{project_id}')(delete_project_compat)
router.get('/api/projects/{project_id}/config')(get_project_config_compat)
router.put('/api/projects/{project_id}/config')(save_project_config_compat)
router.get('/api/projects/{project_id}/snapshots')(list_project_snapshots_compat)
@router.delete('/api/projects/{project_id}/snapshots')
async def delete_snapshots(project_id: str, snapshot_ids: List[str]):
    return await delete_project_snapshots_compat(project_id, snapshot_ids)
router.get('/api/projects/{project_id}/snapshot-groups')(list_snapshot_groups_compat)
router.post('/api/projects/{project_id}/snapshots/capture')(capture_manual_snapshots_compat)
router.get('/api/projects/{project_id}/snapshots/compare')(compare_project_snapshots_compat)
router.post('/api/projects/{project_id}/restore-version')(restore_project_version_compat)
router.get('/api/projects/{project_id}/runs')(get_project_runs_compat)

@router.get('/api/projects/{project_id}/vc/stats')
async def get_project_vc_stats(project_id: str):
    return await get_project_storage_stats(project_id)

@router.post('/api/projects/{project_id}/vc/retention')
async def run_project_retention(project_id: str, days: int = 30):
    return await apply_project_retention_policy(project_id, days)
