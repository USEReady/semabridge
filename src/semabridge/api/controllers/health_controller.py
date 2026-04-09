from fastapi import APIRouter

from semabridge.api.services.health_service import health_check

router = APIRouter()
router.get('/api/health')(health_check)
