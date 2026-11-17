"""Encryption key resolution + lazy migration of legacy-dev-key ciphertexts."""

from __future__ import annotations

import base64
import hashlib
import stat

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from semabridge.auth import encryption as enc


def _legacy_encrypt(plain: str) -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(enc.LEGACY_DEV_SECRET.encode()).digest())
    return Fernet(key).encrypt(plain.encode()).decode()


@pytest.fixture
def isolated_key(monkeypatch, tmp_path):
    monkeypatch.delenv("SEMABRIDGE_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY_FILE", str(tmp_path / "enc.key"))
    monkeypatch.setenv("SEMABRIDGE_LAZY_REENCRYPT", "false")
    enc._get_fernet.cache_clear()
    yield tmp_path / "enc.key"
    enc._get_fernet.cache_clear()


def test_generates_and_persists_key_when_auth_disabled(monkeypatch, isolated_key):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    token = enc.encrypt_token("hello")
    assert isolated_key.exists()
    assert stat.S_IMODE(isolated_key.stat().st_mode) == 0o600
    first_key = isolated_key.read_text()
    assert first_key and first_key != enc.LEGACY_DEV_SECRET
    # A fresh process (cache cleared) reuses the persisted key.
    enc._get_fernet.cache_clear()
    assert enc.decrypt_token(token) == "hello"
    assert isolated_key.read_text() == first_key


def test_refuses_without_key_when_auth_enabled(monkeypatch, isolated_key):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(RuntimeError):
        enc.ensure_encryption_key_available()
    assert not isolated_key.exists()


def test_persisted_key_accepted_when_auth_enabled(monkeypatch, isolated_key):
    isolated_key.write_text("persisted-secret")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    enc.ensure_encryption_key_available()
    assert enc.decrypt_token(enc.encrypt_token("x")) == "x"


def test_legacy_ciphertext_still_decrypts(monkeypatch, isolated_key):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    legacy = _legacy_encrypt("old-secret")
    plain, used_legacy = enc.decrypt_token_ex(legacy)
    assert (plain, used_legacy) == ("old-secret", True)
    assert enc.decrypt_token(legacy) == "old-secret"


def test_reencrypt_legacy_ciphertexts_migrates_rows(monkeypatch, isolated_key):
    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY", "brand-new-real-key")
    enc._get_fernet.cache_clear()

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE accounts (id TEXT PRIMARY KEY, encrypted_token TEXT, refresh_token TEXT)"))
        conn.execute(text("CREATE TABLE semabridge_credentials (owner_id INT, service TEXT, key TEXT, value TEXT, "
                          "PRIMARY KEY (owner_id, service, key))"))
        conn.execute(text("CREATE TABLE user_credentials (id INT PRIMARY KEY, value TEXT)"))
        conn.execute(text("INSERT INTO accounts VALUES ('a1', :t, NULL)"), {"t": _legacy_encrypt('{"client_secret": "s1"}')})
        conn.execute(text("INSERT INTO accounts VALUES ('a2', :t, :r)"),
                     {"t": enc.encrypt_token("already-new"), "r": _legacy_encrypt("refresh-old")})
        conn.execute(text("INSERT INTO semabridge_credentials VALUES (0, 'llm_openai', 'api_key', :v)"),
                     {"v": _legacy_encrypt("sk-legacy")})
        conn.execute(text("INSERT INTO semabridge_credentials VALUES (0, 'fabric', 'tenant_id', 'plain-tenant')"))
        conn.execute(text("INSERT INTO user_credentials VALUES (1, 'plain-value')"))
    session = sessionmaker(bind=engine)()

    counts = enc.reencrypt_legacy_ciphertexts(session)
    assert counts == {
        "accounts.encrypted_token": 1,
        "accounts.refresh_token": 1,
        "semabridge_credentials.value": 1,
    }

    active = enc._get_fernet()
    with engine.connect() as conn:
        a1 = conn.execute(text("SELECT encrypted_token FROM accounts WHERE id='a1'")).scalar()
        a2 = conn.execute(text("SELECT encrypted_token, refresh_token FROM accounts WHERE id='a2'")).one()
        key = conn.execute(text("SELECT value FROM semabridge_credentials WHERE service='llm_openai'")).scalar()
        tenant = conn.execute(text("SELECT value FROM semabridge_credentials WHERE service='fabric'")).scalar()
        uc = conn.execute(text("SELECT value FROM user_credentials")).scalar()
    # Now readable with the ACTIVE key, no legacy fallback needed; nothing lost.
    assert active.decrypt(a1.encode()).decode() == '{"client_secret": "s1"}'
    assert active.decrypt(a2[0].encode()).decode() == "already-new"
    assert active.decrypt(a2[1].encode()).decode() == "refresh-old"
    assert active.decrypt(key.encode()).decode() == "sk-legacy"
    assert enc.decrypt_token_ex(key) == ("sk-legacy", False)
    # Plaintext values untouched.
    assert tenant == "plain-tenant"
    assert uc == "plain-value"
    # Idempotent.
    assert enc.reencrypt_legacy_ciphertexts(session) == {}
    session.close()


def test_reencrypt_is_compare_and_swap(monkeypatch, isolated_key):
    """A value rewritten concurrently must not be overwritten by migration."""
    monkeypatch.setenv("SEMABRIDGE_ENCRYPTION_KEY", "brand-new-real-key")
    enc._get_fernet.cache_clear()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE accounts (id TEXT PRIMARY KEY, encrypted_token TEXT, refresh_token TEXT)"))
        conn.execute(text("INSERT INTO accounts VALUES ('a1', :t, NULL)"), {"t": _legacy_encrypt("old")})
    session = sessionmaker(bind=engine)()

    original = enc._reencrypt_value

    def racing(value):
        out = original(value)
        if out is not None:
            with engine.begin() as conn:  # concurrent token refresh
                conn.execute(text("UPDATE accounts SET encrypted_token = :n"), {"n": enc.encrypt_token("fresh")})
        return out

    monkeypatch.setattr(enc, "_reencrypt_value", racing)
    counts = enc.reencrypt_legacy_ciphertexts(session)
    assert counts.get("accounts.encrypted_token", 0) == 0
    with engine.connect() as conn:
        value = conn.execute(text("SELECT encrypted_token FROM accounts")).scalar()
    assert enc.decrypt_token(value) == "fresh"
    session.close()
