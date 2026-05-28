import sys
import asyncio

# Windows: force SelectorEventLoop BEFORE any imports that touch asyncio.
# Must be set here — uvicorn creates the event loop during its own startup
# and will use ProactorEventLoop by default on Windows unless the policy is
# already set. ProactorEventLoop causes WinError 10054 (ConnectionResetError)
# and request hangs of 140+ seconds on long-running endpoints.
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os

from fastapi import FastAPI

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
from semabridge.api.routers.synonyms_router import router as synonyms_router

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
    synonyms_router,
]:
    app.include_router(router)

if auth_router is not None:
    app.include_router(auth_router)

app.include_router(ui_router, prefix='/api')


if __name__ == '__main__':
    import uvicorn

    port = int(os.environ.get('API_PORT', '8001'))
    uvicorn.run(
        # Pass as import string so uvicorn respects the event loop policy
        # already set above. Passing the app object directly causes uvicorn
        # to spin up its own ProactorEventLoop on Windows, overriding our fix.
        'semabridge.api.main:app',
        host='0.0.0.0',
        port=port,
        reload=False,
        # Force asyncio (SelectorEventLoop) — prevents WinError 10054 and
        # the 140-second request hangs caused by ProactorEventLoop on Windows.
        loop='asyncio',
    )
