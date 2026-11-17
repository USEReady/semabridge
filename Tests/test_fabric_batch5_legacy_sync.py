"""Batch 5 item 4: legacy /sync/jobs is deprecated, requires auth, and never uses server-wide FABRIC_* creds."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def _app(monkeypatch):
    import semabridge.api.sync_router as sr

    started = []

    class _FakeOrchestrator:
        def run(self, config, initiated_by):
            started.append(config)
            return SimpleNamespace(job_id="j1", status=SimpleNamespace(value="completed"), total_items=0,
                                   completed_items=0, failed_items=0, duration_ms=1)

    monkeypatch.setattr(sr, "_get_orchestrator", lambda: _FakeOrchestrator())
    app = FastAPI()

    @app.middleware("http")
    async def _user(request: Request, call_next):  # stands in for the auth middleware
        request.state.user_id = request.headers.get("x-test-user")
        return await call_next(request)

    app.include_router(sr.router)
    return TestClient(app), started


def test_legacy_sync_jobs_requires_auth_and_is_deprecated(monkeypatch):
    client, started = _app(monkeypatch)
    body = {"direction": "fabric_to_snowflake", "fabric_workspace_id": "ws-1", "fabric_identity_id": "acct-1"}

    anon = client.post("/sync/jobs", json=body)
    assert anon.status_code == 401 and "deprecated" in anon.json()["detail"] and started == []
    assert client.get("/sync/jobs").status_code == 401

    ok = client.post("/sync/jobs", json=body, headers={"x-test-user": "u-1"})
    assert ok.status_code == 200 and ok.headers.get("Deprecation") == "true"
    assert started[0].fabric_identity_id == "acct-1" and started[0].fabric_workspace_id == "ws-1"
    schema = client.app.openapi()["paths"]
    assert schema["/sync/jobs"]["post"].get("deprecated") is True
    assert not schema["/sync/mappings"]["get"].get("deprecated")  # non-legacy /sync routes untouched


def test_legacy_fabric_credentials_never_fall_back_to_server_env(monkeypatch):
    import semabridge.auth.token_resolver as tr
    import semabridge.sources.fabric.credentials as cr
    from semabridge.sources.base import SourceAuthError, SourceConfigError
    from semabridge.sources.fabric.legacy_sync import fabric_extractor_for_sync, fabric_publisher_for_sync

    monkeypatch.setenv("FABRIC_CLIENT_SECRET", "server-wide-secret")
    monkeypatch.setenv("FABRIC_WORKSPACE_ID", "server-ws")
    monkeypatch.setattr(tr, "resolve_fabric_access_token", lambda h, i=None: f"token-for-{i}")

    ext = fabric_extractor_for_sync("job-ws", "acct-1")
    assert ext._access_token == "token-for-acct-1" and ext.config.workspace_id == "job-ws"
    assert ext.config.client_secret is None  # the env secret is not picked up
    pub = fabric_publisher_for_sync("job-ws", "acct-1")
    assert pub._access_token == "token-for-acct-1" and pub.config.client_secret is None

    with pytest.raises(SourceConfigError, match="fabric_workspace_id is required"):
        fabric_extractor_for_sync(None, "acct-1")  # no server default workspace
    store = SimpleNamespace(get_fabric_auth_method=lambda: "service_principal", has_valid_token=lambda: False,
                            get_msal_token=lambda: None)
    monkeypatch.setattr("semabridge.repository.credential_manager.CredentialManager", lambda: store)
    with pytest.raises(SourceAuthError, match="a Fabric identity is required"):
        fabric_extractor_for_sync("job-ws", None)  # would have been the server-wide service principal

    # the orchestrator's Fabric discovery goes through the per-job helper
    import semabridge.sync.orchestrator as so
    from semabridge.sync.models import SyncConfig, SyncDirection

    seen = {}
    monkeypatch.setattr("semabridge.sources.fabric.legacy_sync.fabric_extractor_for_sync",
                        lambda ws, ident: seen.update(ws=ws, ident=ident) or SimpleNamespace(list_semantic_models=lambda: []))
    so.SyncOrchestrator.__new__(so.SyncOrchestrator)._discover_fabric_items(
        SyncConfig(direction=SyncDirection.FABRIC_TO_SNOWFLAKE, fabric_workspace_id="job-ws", fabric_identity_id="acct-9"), "job-1")
    assert seen == {"ws": "job-ws", "ident": "acct-9"}
