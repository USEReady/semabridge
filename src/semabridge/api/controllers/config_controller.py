from fastapi import APIRouter

from semabridge.api.services.config_service import (
    generate_config,
    get_config,
    get_global_config,
    save_global_config,
    validate_config,
    validate_live,
)

router = APIRouter()
router.get('/api/config')(get_config)
router.get('/api/global-config')(get_global_config)
router.put('/api/global-config')(save_global_config)
router.post('/api/config/generate')(generate_config)
router.post('/api/config/validate')(validate_config)
router.post('/api/config/validate-live')(validate_live)
