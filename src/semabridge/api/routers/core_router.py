from fastapi import APIRouter

from semabridge.api.services.core_api_service import (
    discover_fabric_models,
    discover_fabric_models_by_workspace,
    discover_multi_workspace,
    discover_repository,
    discover_semantic,
    discover_snowflake,
    generate_config,
    get_config,
    get_global_config,
    get_history,
    get_model,
    health_check,
    save_global_config,
    save_model,
    semantic_refresh,
    semantic_sync,
    sync_models,
    validate_config,
    validate_live,
)

router = APIRouter()
router.get('/api/health')(health_check)
router.get('/api/discovery/fabric')(discover_fabric_models)
router.get('/api/discovery/fabric/workspaces/{workspace_id}/models')(discover_fabric_models_by_workspace)
router.get('/api/discovery/snowflake')(discover_snowflake)
router.get('/api/discovery/repository')(discover_repository)
router.get('/api/discovery/semantic', response_model=None)(discover_semantic)
router.post('/api/semantic/sync', response_model=None)(semantic_sync)
router.post('/api/semantic/refresh', response_model=None)(semantic_refresh)
router.get('/api/models/{model_id}')(get_model)
router.put('/api/models/{model_id}')(save_model)
router.get('/api/config')(get_config)
router.get('/api/global-config')(get_global_config)
router.put('/api/global-config')(save_global_config)
router.post('/api/config/generate')(generate_config)
router.post('/api/config/validate')(validate_config)
router.get('/api/history')(get_history)
router.post('/api/sync')(sync_models)
router.post('/api/config/validate-live')(validate_live)
router.post('/api/multi-workspace/discover')(discover_multi_workspace)
