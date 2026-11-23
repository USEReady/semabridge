"""Connections controller — user-scoped credential management endpoints.

Each endpoint resolves the authenticated user and passes ``user_id`` to the
service layer.

- ``AUTH_ENABLED=false`` (single-user dev mode): when no user can be resolved
  the request falls back to ``user_id=0`` (the global/system sentinel),
  preserving full backward compatibility.
- ``AUTH_ENABLED=true``: an authenticated user is required (401 otherwise)
  and credentials are only ever read from / written to that user's scope --
  never the global ``0`` scope.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from semabridge.auth.flags import auth_enabled
from semabridge.api.services.connections_service import (
    audit_databricks_permissions,
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


def _lookup_user_id(request: Request) -> int:
    """Return the resolved user's id, or 0 when none can be resolved."""
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


def _resolve_user_id(request: Request) -> int:
    """Resolve the authenticated user's ID from the request.

    Auth disabled: returns the user's id, or ``0`` (the global/system
    sentinel) when no user is found -- the old single-tenant behaviour.

    Auth enabled: returns the authenticated user's id; raises 401 when no
    user can be resolved, so nothing ever falls through to the global scope.
    """
    user_id = _lookup_user_id(request)
    if auth_enabled() and not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


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
# Databricks permission audit (CS-10) -- read-only; remediation SQL as text.
# Body (optional): {"catalog": "...", "schema": "..."}; defaults to the
# stored connection's catalog/schema.
# ---------------------------------------------------------------------------

@router.post('/api/connections/databricks/permissions')
async def _databricks_permissions(request: Request):
    user_id = _resolve_user_id(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    return await audit_databricks_permissions(
        user_id=user_id,
        catalog=(payload.get("catalog") or None),
        schema=(payload.get("schema") or payload.get("schema_name") or None),
    )


# ---------------------------------------------------------------------------
# Snowflake OAuth — uses ephemeral credentials from the request body, no
# user scoping needed (the token is provided inline, not from the DB).
# ---------------------------------------------------------------------------

@router.post('/api/connections/snowflake/oauth-test')
async def _snowflake_oauth_test(request: Request):
    # Requires a logged-in user when auth is enabled (raises 401 otherwise).
    _resolve_user_id(request)
    body = await request.json()
    return await snowflake_oauth_test(body)
