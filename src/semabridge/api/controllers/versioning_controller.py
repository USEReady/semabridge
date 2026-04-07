from fastapi import APIRouter

from semabridge.api.services.versioning_service import (
    compare_model_versions,
    delete_model_versions,
    get_version_snapshot,
    list_model_versions,
    rollback_model_version,
)

router = APIRouter()
router.get('/api/model-versions')(list_model_versions)
router.get('/api/model-versions/compare')(compare_model_versions)
router.delete('/api/model-versions')(delete_model_versions)
router.get('/api/model-versions/snapshot')(get_version_snapshot)
router.post('/api/model-versions/rollback')(rollback_model_version)
