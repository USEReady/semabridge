from fastapi import APIRouter

from semabridge.api.services.connection_api_service import (
    databricks_auth_status,
    databricks_device_code_poll,
    databricks_logout,
    databricks_native_oauth_login,
    databricks_oauth_callback,
    debug_token_header,
    delete_connection,
    fabric_auth_status,
    fabric_device_code_login,
    fabric_device_code_poll,
    fabric_get_default_workspace,
    fabric_list_workspaces,
    fabric_logout,
    fabric_select_workspace,
    get_connections_status,
    list_workspaces,
    save_connection,
    snowflake_sso_login,
    test_connection,
)

router = APIRouter()
router.get('/api/workspaces')(list_workspaces)
router.get('/api/connections/status')(get_connections_status)
router.post('/api/connections/{service}')(save_connection)
router.delete('/api/connections/{service}')(delete_connection)
router.post('/api/connections/{service}/test')(test_connection)
router.post('/api/connections/snowflake/sso-login')(snowflake_sso_login)
router.post('/api/connections/fabric/login')(fabric_device_code_login)
router.post('/api/connections/fabric/poll')(fabric_device_code_poll)
router.get('/api/connections/fabric/auth-status')(fabric_auth_status)
router.post('/api/connections/fabric/logout')(fabric_logout)
router.post('/api/connections/databricks/login')(databricks_native_oauth_login)
router.get('/api/connections/databricks/callback')(databricks_oauth_callback)
router.post('/api/connections/databricks/poll')(databricks_device_code_poll)
router.get('/api/connections/databricks/auth-status')(databricks_auth_status)
router.post('/api/connections/databricks/logout')(databricks_logout)
router.get('/api/connections/fabric/workspaces')(fabric_list_workspaces)
router.get('/api/debug/token')(debug_token_header)
router.get('/api/connections/fabric/default-workspace')(fabric_get_default_workspace)
router.post('/api/connections/fabric/select-workspace')(fabric_select_workspace)
