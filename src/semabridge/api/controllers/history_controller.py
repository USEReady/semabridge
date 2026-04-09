from fastapi import APIRouter

from semabridge.api.services.history_service import compare_versions, get_history, rollback_version

router = APIRouter()
router.get('/api/history')(get_history)
router.get('/api/history/compare')(compare_versions)
router.post('/api/history/rollback')(rollback_version)
