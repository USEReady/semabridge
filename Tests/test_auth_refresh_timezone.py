from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from semabridge.api.auth_router import router as auth_router
from semabridge.api.deps import get_db
from semabridge.auth.passwords import hash_password
from semabridge.auth.tokens import hash_refresh_token
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import PasswordResetToken, RefreshToken, User


def _make_client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def test_refresh_handles_sqlite_naive_expires_at():
    client, SessionLocal = _make_client()
    db = SessionLocal()
    try:
        user = User(
            username="alice_test",
            email="alice@example.com",
            password_hash=hash_password("Secret123!"),
            role="admin",
            is_active=True,
        )
        db.add(user)
        db.flush()

        # Store offset-naive datetimes just as SQLite DateTime columns return them
        active_raw = "valid-refresh-token"
        expired_raw = "expired-refresh-token"
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(active_raw),
                expires_at=now_naive + timedelta(hours=1),
                is_revoked=False,
            )
        )
        db.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_refresh_token(expired_raw),
                expires_at=now_naive - timedelta(minutes=5),
                is_revoked=False,
            )
        )
        db.commit()
    finally:
        db.close()

    ok = client.post("/auth/refresh", cookies={"semabridge_refresh_token": "valid-refresh-token"})
    assert ok.status_code == 200
    assert ok.json()["access_token"]

    exp = client.post("/auth/refresh", cookies={"semabridge_refresh_token": "expired-refresh-token"})
    assert exp.status_code == 401
    assert exp.json()["detail"] == "Refresh token expired"


def test_reset_password_handles_sqlite_naive_expires_at():
    client, SessionLocal = _make_client()
    db = SessionLocal()
    try:
        user = User(
            username="bob_test",
            email="bob@example.com",
            password_hash=hash_password("OldPass123!"),
            role="user",
            is_active=True,
        )
        db.add(user)
        db.flush()

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_refresh_token("reset-ok"),
                expires_at=now_naive + timedelta(minutes=15),
                used=False,
            )
        )
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_refresh_token("reset-exp"),
                expires_at=now_naive - timedelta(minutes=1),
                used=False,
            )
        )
        db.commit()
    finally:
        db.close()

    bad = client.post("/auth/reset-password", json={"token": "reset-exp", "new_password": "NewPass123!"})
    assert bad.status_code == 400

    good = client.post("/auth/reset-password", json={"token": "reset-ok", "new_password": "NewPass123!"})
    assert good.status_code == 200

