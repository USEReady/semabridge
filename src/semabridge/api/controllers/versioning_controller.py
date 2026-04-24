from fastapi import APIRouter

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
async def run_retention_policy(project_id: str, days: int = 30):
    return await version_backend.apply_retention_policy(project_id, days_to_keep=days)
