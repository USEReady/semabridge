import os

from fastapi import FastAPI, Request

from semabridge.api.account_router import router as account_router
from semabridge.api.app_setup import configure_app, lifespan
from semabridge.api.browse import router as browse_router
from semabridge.api.discovery_api import router as discovery_router
from semabridge.api.repo_router import router as repo_router
from semabridge.api.routers.connection_router import router as connection_router
from semabridge.api.routers.core_router import router as core_router
from semabridge.api.routers.project_router import router as project_router
from semabridge.api.settings_api import router as settings_router
from semabridge.api.sync_router import router as sync_router
from semabridge.api.ui import router as ui_router
from semabridge.api.websocket_alerts import alert_router
from semabridge.api.routers.diagnostics_router import router as diagnostics_router
from semabridge.api.routers.mapping_router import router as mapping_router
from semabridge.notifications.api.notification_routes import router as notification_router

try:
    from semabridge.api.auth_router import router as auth_router
except ImportError:
    auth_router = None

# Keep the entrypoint intentionally thin: startup hooks live in app_setup,
# and feature routes are composed from the modular router packages.
app = FastAPI(
    title='SemaBridge API',
    version='2.0.0',
    docs_url='/docs',
    redoc_url='/redoc',
    lifespan=lifespan,
)
configure_app(app)

# Mount legacy top-level routers plus the newer domain routers on one app
# so the refactor can stay backward compatible while modules are cleaned up.
for router in [
    repo_router,
    sync_router,
    account_router,
    settings_router,
    discovery_router,
    browse_router,
    alert_router,
    core_router,
    project_router,
    connection_router,
    diagnostics_router,
    mapping_router,
    notification_router,
]:
    app.include_router(router)

if auth_router is not None:
    app.include_router(auth_router)

app.include_router(ui_router, prefix='/api')


@app.get("/api/debug/auth-test")
def debug_auth_test(request: Request):
    from semabridge.auth.tokens import decode_access_token
    import os
    
    auth_header = request.headers.get("Authorization", "")
    if not auth_header:
        auth_header = next((v for k, v in request.headers.items() if k.lower() == "authorization"), "")
        
    token = ""
    prefix_valid = False
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
        prefix_valid = True
        
    secret_key = os.environ.get("JWT_SECRET_KEY", "")
    secret_key_len = len(secret_key)
    secret_key_preview = f"{secret_key[:4]}...{secret_key[-4:]}" if secret_key_len > 8 else "too_short"
    
    decode_payload = None
    decode_error = None
    if token:
        try:
            decode_payload = decode_access_token(token)
        except Exception as e:
            decode_error = f"{e.__class__.__name__}: {str(e)}"
            
    return {
        "status": "success" if decode_payload else "failed",
        "auth_header_present": bool(auth_header),
        "auth_header_value_prefix": auth_header[:25] + "..." if len(auth_header) > 25 else auth_header,
        "prefix_valid": prefix_valid,
        "token_extracted_length": len(token),
        "jwt_secret_loaded": bool(secret_key),
        "jwt_secret_length": secret_key_len,
        "jwt_secret_preview": secret_key_preview,
        "decoded_payload": decode_payload,
        "decode_error": decode_error,
    }


if __name__ == '__main__':
    import uvicorn

    port = int(os.environ.get('API_PORT', '8001'))
    uvicorn.run(app, host='0.0.0.0', port=port, reload=False)
