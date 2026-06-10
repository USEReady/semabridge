from fastapi import APIRouter

from semabridge.api.services.core_domain_service import get_model, save_model

router = APIRouter()
router.get('/api/models/{model_id}')(get_model)
router.put('/api/models/{model_id}')(save_model)
