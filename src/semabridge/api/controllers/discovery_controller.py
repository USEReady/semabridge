from fastapi import APIRouter

from semabridge.api.services.discovery_service import (
    discover_fabric_models,
    discover_fabric_models_by_workspace,
    discover_multi_workspace,
    discover_repository,
    discover_semantic,
    discover_snowflake,
    discover_snowflake_databases,
    discover_snowflake_schemas,
    discover_snowflake_warehouses,
)

router = APIRouter()
router.get('/api/discovery/fabric')(discover_fabric_models)
router.get('/api/discovery/fabric/workspaces/{workspace_id}/models')(discover_fabric_models_by_workspace)
router.get('/api/discovery/snowflake')(discover_snowflake)
router.get('/api/discovery/snowflake/warehouses')(discover_snowflake_warehouses)
router.get('/api/discovery/snowflake/databases')(discover_snowflake_databases)
router.get('/api/discovery/snowflake/databases/{database}/schemas')(discover_snowflake_schemas)
router.get('/api/discovery/repository')(discover_repository)
router.get('/api/discovery/semantic', response_model=None)(discover_semantic)
router.post('/api/multi-workspace/discover')(discover_multi_workspace)
