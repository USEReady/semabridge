from fastapi import APIRouter

from semabridge.api.services.fabric_connection_service import (
    debug_token_header,
    fabric_auth_status,
    fabric_device_code_login,
    fabric_device_code_poll,
    fabric_get_default_workspace,
    fabric_list_workspaces,
    fabric_logout,
    fabric_select_workspace,
)

router = APIRouter()
router.post('/api/connections/fabric/login')(fabric_device_code_login)
router.post('/api/connections/fabric/poll')(fabric_device_code_poll)
router.get('/api/connections/fabric/auth-status')(fabric_auth_status)
router.post('/api/connections/fabric/logout')(fabric_logout)
router.get('/api/connections/fabric/workspaces')(fabric_list_workspaces)
router.get('/api/debug/token')(debug_token_header)
router.get('/api/connections/fabric/default-workspace')(fabric_get_default_workspace)
router.post('/api/connections/fabric/select-workspace')(fabric_select_workspace)
