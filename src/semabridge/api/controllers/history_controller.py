from fastapi import APIRouter

from semabridge.api.services.core_domain_service import get_history
from semabridge.api.services.project_domain_service import compare_versions, rollback_version

router = APIRouter()
router.get('/api/history')(get_history)
router.get('/api/history/compare')(compare_versions)
router.post('/api/history/rollback')(rollback_version)
