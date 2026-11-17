"""Security boundaries: AUTH_ENABLED parsing, auto-login gating, public
paths, connection scoping, project ownership, /api/sync sanitisation."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from semabridge.api.auth_router import router as auth_router
from semabridge.api.deps import get_db
from semabridge.auth import flags
from semabridge.auth.middleware import AuthMiddleware
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import User


# ---------------------------------------------------------------------------
# AUTH_ENABLED parsing (single helper, fails closed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [(None, True), ("", True), ("true", True), ("TRUE", True), ("1", True), ("garbage", True),
     ("false", False), ("False", False), ("0", False), ("no", False), ("off", False)],
)
def test_auth_enabled_fails_closed(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("AUTH_ENABLED", raising=False)
    else:
        monkeypatch.setenv("AUTH_ENABLED", raw)
    assert flags.auth_enabled() is expected


def test_ownership_service_uses_shared_flag(monkeypatch):
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    assert pos.auth_is_enabled() is True  # used to be False (inconsistent with middleware)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert pos.auth_is_enabled() is False


# ---------------------------------------------------------------------------
# /auth/auto-login
# ---------------------------------------------------------------------------

def _auth_client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    app = FastAPI()
    app.include_router(auth_router)

    def _override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app), SessionLocal


@pytest.fixture
def jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-security-tests-0123456789")


def test_auto_login_auth_disabled_still_grants_admin(monkeypatch, jwt_secret):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("SEMABRIDGE_DEV_AUTO_LOGIN", raising=False)
    client, _ = _auth_client()
    res = client.post("/auth/auto-login")
    assert res.status_code == 200
    assert res.json()["access_token"]
    assert res.json()["user"]["role"] == "admin"


def test_auto_login_disabled_when_auth_enabled(monkeypatch, jwt_secret):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("SEMABRIDGE_DEV_AUTO_LOGIN", raising=False)
    client, SessionLocal = _auth_client()
    res = client.post("/auth/auto-login")
    assert res.status_code == 404
    with SessionLocal() as db:
        assert db.execute(select(User)).first() is None


def test_auto_login_with_flag_never_grants_admin_when_auth_enabled(monkeypatch, jwt_secret):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("SEMABRIDGE_DEV_AUTO_LOGIN", "true")
    client, SessionLocal = _auth_client()
    # Pre-existing admin dev user (e.g. created earlier in auth-disabled mode)
    with SessionLocal() as db:
        db.add(User(username="dev", email="dev-x@semabridge.local", password_hash="x", role="admin"))
        db.commit()
    res = client.post("/auth/auto-login")
    assert res.status_code == 200
    assert res.json()["user"]["role"] != "admin"
    with SessionLocal() as db:
        assert db.execute(select(User).where(User.username == "dev")).scalar_one().role != "admin"


# ---------------------------------------------------------------------------
# Middleware public paths: /api/connections no longer a public prefix
# ---------------------------------------------------------------------------

def _middleware_client():
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/connections/status")
    def _status():
        return {"ok": True}

    @app.post("/api/connections/{service}")
    def _save(service: str):
        return {"saved": service}

    @app.get("/api/connections/databricks/callback")
    def _cb():
        return {"cb": True}

    return TestClient(app)


def test_connections_require_auth_when_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    client = _middleware_client()
    assert client.get("/api/connections/status").status_code == 401
    assert client.post("/api/connections/snowflake").status_code == 401
    # OAuth redirect target stays public
    assert client.get("/api/connections/databricks/callback").status_code == 200


def test_connections_open_when_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    client = _middleware_client()
    assert client.get("/api/connections/status").status_code == 200
    assert client.post("/api/connections/snowflake").status_code == 200


def _bare_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [], "state": {}})


def test_connections_controller_never_uses_global_scope_when_auth_enabled(monkeypatch):
    from semabridge.api.controllers import connections_controller as cc

    monkeypatch.setattr(cc, "_lookup_user_id", lambda request: 0)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc:
        cc._resolve_user_id(_bare_request())
    assert exc.value.status_code == 401

    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert cc._resolve_user_id(_bare_request()) == 0  # dev mode unchanged


# ---------------------------------------------------------------------------
# Project ownership: orphaned projects are admin-only, never reassigned
# ---------------------------------------------------------------------------

def test_orphaned_project_not_reassigned(monkeypatch):
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.setenv("AUTH_ENABLED", "true")
    persisted = []
    project = {"id": "p"}
    monkeypatch.setattr(pos, "ensure_project_owner", lambda pid: {
        "project": project, "owner_user_id": "99", "ownership_source": "project_payload", "recovered": False,
    })
    monkeypatch.setattr(pos, "_user_exists", lambda uid: False)
    monkeypatch.setattr(pos, "_persist_project_owner", lambda *a, **k: persisted.append(a))

    monkeypatch.setattr(pos, "_is_admin_user", lambda uid: False)
    assert pos.is_project_owned_by_user("p", "5", log_denied=False) is False
    assert persisted == []

    monkeypatch.setattr(pos, "_is_admin_user", lambda uid: True)
    assert pos.is_project_owned_by_user("p", "1") is True
    assert persisted == []


def test_sentinel_owner_project_not_claimed(monkeypatch):
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(pos, "ensure_project_owner", lambda pid: {
        "project": {"id": pid}, "owner_user_id": "1", "ownership_source": "project_payload", "recovered": False,
    })
    monkeypatch.setattr(pos, "_is_admin_user", lambda uid: False)
    monkeypatch.setattr(pos, "_persist_project_owner", lambda *a, **k: pytest.fail("must not persist"))
    assert pos.is_project_owned_by_user("p", "5") is False


def test_auth_disabled_project_access_unchanged(monkeypatch):
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert pos.is_project_owned_by_user("anything", None) is True
    pos.assert_project_access("anything", None)  # no raise


def test_shared_assert_project_access(monkeypatch):
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(pos, "is_project_owned_by_user", lambda pid, uid, **kw: uid == "7")
    pos.assert_project_access("p", "7")
    with pytest.raises(HTTPException) as exc:
        pos.assert_project_access("p", "8")
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# /api/sync sanitisation (auth enabled)
# ---------------------------------------------------------------------------

@pytest.fixture
def sync_ctrl(monkeypatch, tmp_path):
    from semabridge.api.controllers import semantic_controller as sc

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(sc, "assert_project_access", _fake_assert_access)
    monkeypatch.setattr(sc, "validate_project_connector_accounts_belong_to_user", lambda uid, cfg: None)
    import semabridge.auth.file_access as fa
    monkeypatch.setattr(fa, "allowed_source_roots", lambda: [tmp_path.resolve()])
    return sc


def _fake_assert_access(project_id, user_id, **kw):
    if project_id != "mine":
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


def test_sync_drops_executor_and_forces_user_id(sync_ctrl):
    out = sync_ctrl._sanitize_sync_payload_for_user(
        {"project_id": "mine", "user_id": "999", "executor": "process", "process_max_workers": 64,
         "binding_project_id": "theirs"},
        "7",
    )
    assert out["user_id"] == "7"
    for key in ("executor", "process_max_workers", "binding_project_id"):
        assert key not in out


def test_sync_rejects_unknown_options(sync_ctrl):
    from semabridge.domain.exceptions import ValidationError

    with pytest.raises(ValidationError):
        sync_ctrl._sanitize_sync_payload_for_user({"project_id": "mine", "evil": 1}, "7")


def test_sync_requires_project_ownership(sync_ctrl):
    with pytest.raises(HTTPException):
        sync_ctrl._sanitize_sync_payload_for_user({"project_id": "theirs"}, "7")
    with pytest.raises(HTTPException):
        sync_ctrl._sanitize_sync_payload_for_user({"content": "project_id: theirs\n"}, "7")


def test_sync_content_paths_must_be_allowed(sync_ctrl, tmp_path):
    from semabridge.domain.exceptions import PermissionError as DomainPermissionError

    inside = tmp_path / "m.pbix"
    inside.write_bytes(b"x")
    ok = sync_ctrl._sanitize_sync_payload_for_user(
        {"content": f"source:\n  type: pbix\n  pbix_path: {inside}\n"}, "7"
    )
    assert ok["user_id"] == "7"
    with pytest.raises(DomainPermissionError):
        sync_ctrl._sanitize_sync_payload_for_user(
            {"content": "source:\n  type: pbix\n  pbix_path: /etc/passwd\n"}, "7"
        )


def test_sync_route_passthrough_when_auth_disabled(monkeypatch):
    from semabridge.api.controllers import semantic_controller as sc

    monkeypatch.setenv("AUTH_ENABLED", "false")
    captured = {}

    async def fake_sync(payload):
        captured.update(payload)
        return {"status": "success"}

    monkeypatch.setattr(sc, "sync_models", fake_sync)
    app = FastAPI()
    app.include_router(sc.router)
    res = TestClient(app).post("/api/sync", json={"content": "a: 1\n", "executor": "thread"})
    assert res.status_code == 200
    assert captured["executor"] == "thread"  # dev mode: unchanged
