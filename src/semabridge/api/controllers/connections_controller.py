"""Connections controller — user-scoped credential management endpoints.

Each endpoint resolves the authenticated user and passes ``user_id`` to the
service layer.  When no authenticated user is found (dev mode or CLI), the
request falls back to ``user_id=0`` (the global/system sentinel), preserving
full backward compatibility.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from semabridge.api.services.connections_service import (
    delete_connection,
    get_connections_status,
    list_workspaces,
    save_connection,
    snowflake_oauth_test,
    test_connection,
)

router = APIRouter()


def _resolve_db(request: Request) -> Optional[Session]:
    """Return the SQLAlchemy session attached to the request, if any."""
    return getattr(request.state, "db", None)


def _resolve_user_id(request: Request) -> int:
    """Resolve the authenticated user's ID from the request.

    Returns the user's integer ID when authenticated, or ``0`` (the
    global/system sentinel) when running in dev mode or without a token.
    ``0`` means credentials are read from / written to the global scope,
    which is backward-compatible with the old single-tenant behaviour.
    """
    try:
        from semabridge.api.deps import get_current_user_optional
        db = _resolve_db(request)
        if db is None:
            # No DB session attached — open one via the session factory.
            from semabridge.repository.orm.session_factory import db_manager
            with db_manager.get_session() as session:
                user = get_current_user_optional(request, session)
                return user.id if user else 0
        user = get_current_user_optional(request, db)
        return user.id if user else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Workspaces (does not need user scoping — workspace list is global)
# ---------------------------------------------------------------------------

router.get('/api/workspaces')(list_workspaces)


# ---------------------------------------------------------------------------
# Connection status — per-user view of stored credentials
# ---------------------------------------------------------------------------

@router.get('/api/connections/status')
async def _get_connections_status(request: Request):
    user_id = _resolve_user_id(request)
    return await get_connections_status(user_id=user_id)


# ---------------------------------------------------------------------------
# Save connection — stores credentials scoped to the authenticated user
# ---------------------------------------------------------------------------

@router.post('/api/connections/{service}')
async def _save_connection(service: str, request: Request):
    user_id = _resolve_user_id(request)
    body = await request.json()
    return await save_connection(service, body, user_id=user_id)


# ---------------------------------------------------------------------------
# Delete connection — removes only the authenticated user's credentials
# ---------------------------------------------------------------------------

@router.delete('/api/connections/{service}')
async def _delete_connection(service: str, request: Request):
    user_id = _resolve_user_id(request)
    return await delete_connection(service, user_id=user_id)


# ---------------------------------------------------------------------------
# Test connection — uses the authenticated user's credentials for the test
# ---------------------------------------------------------------------------

@router.post('/api/connections/{service}/test')
async def _test_connection(service: str, request: Request):
    user_id = _resolve_user_id(request)
    return await test_connection(service, user_id=user_id)


# ---------------------------------------------------------------------------
# Snowflake OAuth — uses ephemeral credentials from the request body, no
# user scoping needed (the token is provided inline, not from the DB).
# ---------------------------------------------------------------------------

@router.post('/api/connections/snowflake/oauth-test')
async def _snowflake_oauth_test(request: Request):
    body = await request.json()
    return await snowflake_oauth_test(body)
