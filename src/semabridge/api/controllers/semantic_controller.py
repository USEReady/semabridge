from fastapi import APIRouter

from semabridge.api.services.semantic_service import semantic_refresh, semantic_sync, sync_models

router = APIRouter()
router.post('/api/semantic/sync', response_model=None)(semantic_sync)
router.post('/api/semantic/refresh', response_model=None)(semantic_refresh)
router.post('/api/sync')(sync_models)
