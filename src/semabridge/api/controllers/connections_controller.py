from fastapi import APIRouter, Request

from semabridge.api.services.connections_service import (
    delete_connection,
    get_connections_status,
    list_workspaces,
    save_connection,
    snowflake_oauth_test,
    test_connection,
)

router = APIRouter()
router.get('/api/workspaces')(list_workspaces)
router.get('/api/connections/status')(get_connections_status)
router.post('/api/connections/{service}')(save_connection)
router.delete('/api/connections/{service}')(delete_connection)
router.post('/api/connections/{service}/test')(test_connection)


@router.post('/api/connections/snowflake/oauth-test')
async def _snowflake_oauth_test(request: Request):
    body = await request.json()
    return await snowflake_oauth_test(body)
