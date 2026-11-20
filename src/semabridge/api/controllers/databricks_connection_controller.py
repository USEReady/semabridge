from fastapi import APIRouter

from semabridge.api.services.databricks_connection_service import (
    databricks_auth_status,
    databricks_check_permissions,
    databricks_device_code_poll,
    databricks_list_schemas,
    databricks_logout,
    databricks_native_oauth_login,
    databricks_oauth_callback,
    databricks_start_warehouse,
    databricks_test_connection,
)

router = APIRouter()
router.post('/api/connections/databricks/login')(databricks_native_oauth_login)
router.get('/api/connections/databricks/callback')(databricks_oauth_callback)
router.post('/api/connections/databricks/poll')(databricks_device_code_poll)
router.get('/api/connections/databricks/auth-status')(databricks_auth_status)
router.post('/api/connections/databricks/logout')(databricks_logout)
router.post('/api/connections/databricks/test')(databricks_test_connection)
router.post('/api/connections/databricks/warehouse/start')(databricks_start_warehouse)
router.post('/api/connections/databricks/schemas')(databricks_list_schemas)
router.post('/api/connections/databricks/permissions')(databricks_check_permissions)
