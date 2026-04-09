from fastapi import APIRouter

from semabridge.api.services.databricks_connection_service import (
    databricks_auth_status,
    databricks_device_code_poll,
    databricks_logout,
    databricks_native_oauth_login,
    databricks_oauth_callback,
)

router = APIRouter()
router.post('/api/connections/databricks/login')(databricks_native_oauth_login)
router.get('/api/connections/databricks/callback')(databricks_oauth_callback)
router.post('/api/connections/databricks/poll')(databricks_device_code_poll)
router.get('/api/connections/databricks/auth-status')(databricks_auth_status)
router.post('/api/connections/databricks/logout')(databricks_logout)
