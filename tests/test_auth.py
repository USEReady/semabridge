"""
Tests for the SemaBridge authentication system.

Covers:
  - Password hashing / verification
  - JWT token creation / decoding
  - Auth router endpoints (register, login, me, credentials)
  - Auth middleware (public vs protected path enforcement)
"""

from __future__ import annotations

import os
import time
from datetime import timedelta
from unittest.mock import patch

import pytest
from jose import jwt as jose_jwt

# ---------------------------------------------------------------------------
# Password tests
# ---------------------------------------------------------------------------

from semabridge.auth.passwords import hash_password, verify_password


class TestPasswords:
    """Password hashing and verification."""

    def test_hash_returns_string(self) -> None:
        hashed = hash_password("secret123")
        assert isinstance(hashed, str)
        assert hashed.startswith("$2b$")  # bcrypt prefix

    def test_hash_is_unique(self) -> None:
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # different salts

    def test_verify_correct_password(self) -> None:
        hashed = hash_password("mypassword")
        assert verify_password("mypassword", hashed) is True

    def test_verify_wrong_password(self) -> None:
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False


# ---------------------------------------------------------------------------
# Token tests
# ---------------------------------------------------------------------------

from semabridge.auth.tokens import create_access_token, decode_access_token


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure JWT_SECRET_KEY is present for every test in this module."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-for-unit-tests")


class TestTokens:
    """JWT creation and validation."""

    def test_create_and_decode(self) -> None:
        token = create_access_token({"sub": 42, "role": "admin"})
        payload = decode_access_token(token)
        assert payload["sub"] == "42"  # sub is coerced to str per RFC 7519
        assert payload["role"] == "admin"
        assert "exp" in payload

    def test_expired_token_raises(self) -> None:
        token = create_access_token(
            {"sub": 1},
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(Exception):  # jose.ExpiredSignatureError
            decode_access_token(token)

    def test_tampered_token_raises(self) -> None:
        token = create_access_token({"sub": 1})
        tampered = token[:-4] + "XXXX"
        with pytest.raises(Exception):
            decode_access_token(tampered)

    def test_missing_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
            create_access_token({"sub": 1})


# ---------------------------------------------------------------------------
# ORM model tests
# ---------------------------------------------------------------------------

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import User, UserCredential


@pytest.fixture()
def db_session() -> Session:
    """In-memory SQLite session with all ORM tables created.

    Uses ``StaticPool`` so the same in-memory DB is shared across
    connections (required for SQLite :memory:) and
    ``check_same_thread=False`` because FastAPI's TestClient runs
    endpoint handlers in a worker thread.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()
    engine.dispose()


class TestUserModel:
    """ORM User model fields and constraints."""

    def test_create_user(self, db_session: Session) -> None:
        user = User(
            username="alice",
            email="alice@example.com",
            password_hash=hash_password("pw"),
            role="admin",
        )
        db_session.add(user)
        db_session.commit()

        loaded = db_session.execute(
            select(User).where(User.username == "alice")
        ).scalar_one()
        assert loaded.email == "alice@example.com"
        assert loaded.role == "admin"
        assert loaded.is_active is True

    def test_duplicate_username_fails(self, db_session: Session) -> None:
        db_session.add(User(username="bob", email="a@b.com", password_hash="x"))
        db_session.commit()
        db_session.add(User(username="bob", email="c@d.com", password_hash="y"))
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_duplicate_email_fails(self, db_session: Session) -> None:
        db_session.add(User(username="u1", email="same@e.com", password_hash="x"))
        db_session.commit()
        db_session.add(User(username="u2", email="same@e.com", password_hash="y"))
        with pytest.raises(Exception):
            db_session.commit()


class TestUserCredentialModel:
    """ORM UserCredential model."""

    def test_create_credential(self, db_session: Session) -> None:
        user = User(username="cred_user", email="c@u.com", password_hash="h")
        db_session.add(user)
        db_session.commit()

        cred = UserCredential(
            user_id=user.id,
            service="fabric",
            key="tenant_id",
            value="abc-123",
        )
        db_session.add(cred)
        db_session.commit()

        loaded = db_session.execute(
            select(UserCredential).where(UserCredential.user_id == user.id)
        ).scalar_one()
        assert loaded.service == "fabric"
        assert loaded.key == "tenant_id"
        assert loaded.value == "abc-123"

    def test_cascade_delete(self, db_session: Session) -> None:
        user = User(username="del_user", email="d@u.com", password_hash="h")
        db_session.add(user)
        db_session.commit()
        db_session.add(
            UserCredential(user_id=user.id, service="snowflake", key="account", value="x")
        )
        db_session.commit()

        db_session.delete(user)
        db_session.commit()

        remaining = db_session.execute(select(UserCredential)).scalars().all()
        assert len(remaining) == 0


# ---------------------------------------------------------------------------
# Auth Router integration tests (using FastAPI TestClient)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """FastAPI TestClient wired to the in-memory DB via dependency_overrides."""
    from semabridge.api.auth_router import router
    from semabridge.auth.deps import _get_db
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_db():
        yield db_session

    test_app.dependency_overrides[_get_db] = override_get_db

    with TestClient(test_app) as c:
        yield c


class TestAuthRouter:
    """Integration tests for /auth/* endpoints."""

    def test_register_first_user_is_admin(self, client: TestClient) -> None:
        resp = client.post("/auth/register", json={
            "username": "firstuser",
            "email": "first@example.com",
            "password": "securepassword123",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "firstuser"
        assert body["role"] == "admin"

    def test_register_second_user_is_viewer(self, client: TestClient) -> None:
        # First user → admin
        client.post("/auth/register", json={
            "username": "admin1",
            "email": "admin@ex.com",
            "password": "password1234",
        })
        # Second user → viewer
        resp = client.post("/auth/register", json={
            "username": "viewer1",
            "email": "viewer@ex.com",
            "password": "password1234",
        })
        assert resp.status_code == 201
        assert resp.json()["role"] == "viewer"

    def test_register_duplicate_username_409(self, client: TestClient) -> None:
        client.post("/auth/register", json={
            "username": "dup", "email": "a@b.com", "password": "12345678",
        })
        resp = client.post("/auth/register", json={
            "username": "dup", "email": "c@d.com", "password": "12345678",
        })
        assert resp.status_code == 409

    def test_login_success(self, client: TestClient) -> None:
        client.post("/auth/register", json={
            "username": "loginuser",
            "email": "login@ex.com",
            "password": "mypassword1",
        })
        resp = client.post("/auth/login", json={
            "username": "loginuser",
            "password": "mypassword1",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    def test_login_wrong_password_401(self, client: TestClient) -> None:
        client.post("/auth/register", json={
            "username": "wrongpw",
            "email": "wp@ex.com",
            "password": "correctpw1",
        })
        resp = client.post("/auth/login", json={
            "username": "wrongpw",
            "password": "wrongpw123",
        })
        assert resp.status_code == 401

    def test_me_returns_profile(self, client: TestClient) -> None:
        client.post("/auth/register", json={
            "username": "meuser",
            "email": "me@ex.com",
            "password": "password99",
        })
        login = client.post("/auth/login", json={
            "username": "meuser",
            "password": "password99",
        })
        token = login.json()["access_token"]
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "meuser"

    def test_me_no_token_401(self, client: TestClient) -> None:
        resp = client.get("/auth/me")
        assert resp.status_code in (401, 422)  # 401 from dep, 422 from missing header

    def test_credential_save_and_list(self, client: TestClient) -> None:
        client.post("/auth/register", json={
            "username": "creduser",
            "email": "cred@ex.com",
            "password": "password00",
        })
        token = client.post("/auth/login", json={
            "username": "creduser",
            "password": "password00",
        }).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Save a credential
        resp = client.post(
            "/auth/credentials/fabric",
            json={"key": "tenant_id", "value": "abc-123"},
            headers=headers,
        )
        assert resp.status_code == 201

        # List credentials
        resp = client.get("/auth/credentials", headers=headers)
        assert resp.status_code == 200
        items = resp.json()
        assert any(c["key"] == "tenant_id" for c in items)

    def test_credential_delete(self, client: TestClient) -> None:
        client.post("/auth/register", json={
            "username": "delcred",
            "email": "dc@ex.com",
            "password": "password00",
        })
        token = client.post("/auth/login", json={
            "username": "delcred",
            "password": "password00",
        }).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        client.post(
            "/auth/credentials/snowflake",
            json={"key": "account", "value": "my-acct"},
            headers=headers,
        )
        resp = client.delete("/auth/credentials/snowflake/account", headers=headers)
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------

class TestAuthMiddleware:
    """Tests for the JWT auth middleware gate."""

    def _make_app(self):
        """Build a minimal app with the auth middleware enabled."""
        from fastapi import FastAPI
        from semabridge.auth.middleware import AuthMiddleware

        test_app = FastAPI()
        test_app.add_middleware(AuthMiddleware)

        @test_app.get("/api/health")
        def health():
            return {"status": "ok"}

        @test_app.get("/api/protected")
        def protected():
            return {"data": "secret"}

        return test_app

    def test_public_paths_always_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        with TestClient(self._make_app()) as client:
            resp = client.get("/api/health")
        assert resp.status_code == 200

    def test_protected_path_requires_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        with TestClient(self._make_app()) as client:
            resp = client.get("/api/protected")
        assert resp.status_code == 401

    def test_protected_path_with_valid_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "true")
        # Confirm the secret key is consistent
        secret = os.environ.get("JWT_SECRET_KEY", "")
        assert secret, "JWT_SECRET_KEY must be set by autouse fixture"
        token = create_access_token({"sub": 1, "role": "admin"})
        with TestClient(self._make_app()) as client:
            resp = client.get(
                "/api/protected",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_auth_disabled_skips_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_ENABLED", "false")
        with TestClient(self._make_app()) as client:
            resp = client.get("/api/protected")
        assert resp.status_code == 200  # No token needed
