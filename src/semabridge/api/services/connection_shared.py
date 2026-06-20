"""Shared connection-domain bootstrap, singletons, and helpers.

Module-level setup (``.env`` loading, Windows asyncio policy, websocket alert
handler), the shared singletons (``db_manager``, ``engine``, ``settings``,
``scheduler_service``, ``version_control_service``, ``logger``), Fabric public-client
constants, and the ``_extract_bearer_token`` FastAPI dependency. Imported by the
``connection_*_impl`` modules and re-exported through ``connection_domain_service``.
This module contains no FastAPI app or routing.
"""
# Load .env FIRST so SEMABRIDGE_DATABASE_URL, FABRIC_* and all other
# env-vars are resolved before any module-level code reads os.environ.
from pathlib import Path as _Path
from typing import Optional
import asyncio
import os
import logging
from fastapi import Header

_dotenv_path = _Path(__file__).resolve().parents[3] / ".env"
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=_dotenv_path, override=False)
except ImportError:
    pass  # python-dotenv optional — env vars already set by the OS are used as-is

# Windows-specific asyncio stability:
# use the selector loop instead of Proactor to avoid intermittent
# `_ProactorBaseWritePipeTransport._loop_writing` assertion failures
# during heavy logging / websocket / pipe writes.
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# Core imports
from semabridge.core.settings import get_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.api.websocket_alerts import install_websocket_alert_handler
from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.api.services.version_control_service import VersionControlService
from semabridge.domain.exceptions import AuthenticationError

# -------------------------------------------------------
# Initialize Core Services (module-level)
# -------------------------------------------------------

# Install WebSocket alert handler so warnings/errors auto-dispatch to UI
install_websocket_alert_handler()

logger = logging.getLogger("semabridge.api")

db_manager = ModelRepository()
engine = ExecutionEngine(db_manager=db_manager)

settings = get_settings()
scheduler_service = SchedulerService()
version_control_service: Optional[VersionControlService] = None

# Content hash tracker -- avoids duplicate versions for unchanged files
_last_snapshot_hash: dict[str, str] = {}


# Fabric public-client constants (shared by auth + token resolution).
_FABRIC_PUBLIC_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
# We use the native Fabric API scope. This token will be accepted by Fabric endpoints (list, create, update models).
# To talk to Power BI endpoints (like executeQueries), the system will use the refresh_token to acquire a secondary token.
_FABRIC_SCOPES = ["https://api.fabric.microsoft.com/.default"]


# -------------------------------------------------------
# Bearer Token Extraction (needed by early Fabric routes)
# -------------------------------------------------------
def _extract_bearer_token(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> Optional[str]:
    """Extract a bearer token from Authorization header.

    Accepts header format: ``Authorization: Bearer <token>``.
    Returns ``None`` when no header is provided so existing auth flows can
    continue to use stored credentials or env-token fallback.
    """
    if not authorization:
        logger.info("Fabric request received without Authorization header")
        return None

    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        logger.warning("Invalid Authorization header format for Fabric request")
        raise AuthenticationError("Invalid Authorization header format. Expected: Bearer <token>")
    logger.info("Fabric bearer token received in Authorization header")
    return token
