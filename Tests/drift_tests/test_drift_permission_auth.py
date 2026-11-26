"""Tests verifying drift router permission enforcement with AUTH_ENABLED=true.

Specifically verifies:
1. When AUTH_ENABLED=true, a viewer role calling approve/ignore/accept gets 403 Forbidden.
2. When AUTH_ENABLED=true, a user belonging to one project acting on another project's event gets 403 Forbidden.
3. A user owning the target project succeeds (200 OK).
4. An unauthenticated request gets 401 Unauthorized.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.api.routers import drift_router
from semabridge.api.services import project_ownership_service as pos
from semabridge.auth.tokens import create_access_token
from semabridge.auth.middleware import AuthMiddleware


@pytest.fixture
def auth_drift_client(monkeypatch):
    """Build a TestClient with AuthMiddleware and AUTH_ENABLED=true."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-at-least-32-chars-long-for-hs256")
    monkeypatch.setattr(pos, "auth_is_enabled", lambda: True)

    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(drift_router.router)
    return TestClient(app)


def test_viewer_calling_approve_gets_403(auth_drift_client, monkeypatch):
    """A viewer role attempting to approve a drift event must get 403 Forbidden."""
    test_event = {
        "id": "drift-ev-viewer-test",
        "project_id": "proj-alpha",
        "status": "pending",
        "mock": True,
    }
    monkeypatch.setattr(drift_router, "_find_drift_event", lambda eid: test_event)
    monkeypatch.setattr(
        pos,
        "is_project_owned_by_user",
        lambda pid, uid, **kwargs: True,  # User owns project, but has role 'viewer'
    )

    viewer_token = create_access_token({"sub": "user-viewer", "role": "viewer"})
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp = auth_drift_client.post(
        "/api/drift/events/drift-ev-viewer-test/approve",
        headers=headers,
        json={"reason": "Viewer attempting approval"},
    )

    assert resp.status_code == 403
    assert "caller permissions do not allow drift mutations" in resp.json()["detail"].lower()


def test_viewer_calling_ignore_and_accept_gets_403(auth_drift_client, monkeypatch):
    """A viewer role attempting to ignore or accept a drift event must also get 403 Forbidden."""
    test_event = {
        "id": "drift-ev-viewer-test-2",
        "project_id": "proj-alpha",
        "status": "pending",
        "mock": True,
    }
    monkeypatch.setattr(drift_router, "_find_drift_event", lambda eid: test_event)
    monkeypatch.setattr(pos, "is_project_owned_by_user", lambda pid, uid, **kwargs: True)

    viewer_token = create_access_token({"sub": "user-viewer", "role": "viewer"})
    headers = {"Authorization": f"Bearer {viewer_token}"}

    resp_ignore = auth_drift_client.post(
        "/api/drift/events/drift-ev-viewer-test-2/ignore",
        headers=headers,
        json={"reason": "Viewer attempting ignore"},
    )
    assert resp_ignore.status_code == 403
    assert "caller permissions do not allow drift mutations" in resp_ignore.json()["detail"].lower()

    resp_accept = auth_drift_client.post(
        "/api/drift/events/drift-ev-viewer-test-2/accept",
        headers=headers,
        json={"reason": "Viewer attempting accept"},
    )
    assert resp_accept.status_code == 403
    assert "caller permissions do not allow drift mutations" in resp_accept.json()["detail"].lower()


def test_user_of_one_project_acting_on_another_project_event_gets_403(auth_drift_client, monkeypatch):
    """User of proj-alpha attempting to approve an event belonging to proj-beta gets 403 Forbidden."""
    event_beta = {
        "id": "drift-ev-beta-123",
        "project_id": "proj-beta",
        "status": "pending",
        "mock": True,
    }
    monkeypatch.setattr(drift_router, "_find_drift_event", lambda eid: event_beta)
    # User-alpha owns proj-alpha only, not proj-beta
    monkeypatch.setattr(
        pos,
        "is_project_owned_by_user",
        lambda pid, uid, **kwargs: pid == "proj-alpha" and uid == "user-alpha",
    )

    alpha_token = create_access_token({"sub": "user-alpha", "role": "editor"})
    headers = {"Authorization": f"Bearer {alpha_token}"}

    resp = auth_drift_client.post(
        "/api/drift/events/drift-ev-beta-123/approve",
        headers=headers,
        json={"reason": "Cross-project actor trying to approve"},
    )

    assert resp.status_code == 403
    assert "forbidden: project access denied" in resp.json()["detail"].lower()


def test_owner_user_acting_on_own_project_event_succeeds(auth_drift_client, monkeypatch):
    """User owning proj-alpha approving an event on proj-alpha succeeds (200 OK)."""
    event_alpha = {
        "id": "drift-ev-alpha-123",
        "project_id": "proj-alpha",
        "status": "pending",
        "mock": True,
    }
    monkeypatch.setattr(drift_router, "_find_drift_event", lambda eid: event_alpha)
    monkeypatch.setattr(
        pos,
        "is_project_owned_by_user",
        lambda pid, uid, **kwargs: pid == "proj-alpha" and uid == "user-alpha",
    )
    monkeypatch.setattr(
        drift_router,
        "resolve_drift_event",
        lambda eid, action, reason, project_id=None: {**event_alpha, "status": "approved", "mock": True},
    )

    alpha_token = create_access_token({"sub": "user-alpha", "role": "editor"})
    headers = {"Authorization": f"Bearer {alpha_token}"}

    resp = auth_drift_client.post(
        "/api/drift/events/drift-ev-alpha-123/approve",
        headers=headers,
        json={"reason": "Valid owner approval"},
    )

    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["event"]["status"] == "approved"


def test_unauthenticated_request_gets_401(auth_drift_client):
    """Calling drift mutation without authorization token returns 401 Unauthorized."""
    resp = auth_drift_client.post(
        "/api/drift/events/drift-ev-any/approve",
        json={"reason": "No token"},
    )
    assert resp.status_code == 401
    assert "missing or invalid authorization header" in resp.json()["detail"].lower()
