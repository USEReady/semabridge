"""
Module: encryption
Purpose: Encrypt and decrypt sensitive token values used by Semabridge.
Responsibilities:
- Resolve the active secret: ``SEMABRIDGE_ENCRYPTION_KEY`` env var, else a
  random key generated once and persisted to ``~/.semabridge/encryption.key``
  (mode 0600; path overridable with ``SEMABRIDGE_ENCRYPTION_KEY_FILE``).
- When auth is enabled and neither is available, refuse to operate (and
  :func:`ensure_encryption_key_available` lets startup fail fast).
- Derive a Fernet key from that secret and provide encrypt/decrypt helpers.
- Legacy migration: values written before this change were encrypted with a
  fixed, publicly known dev secret. :func:`decrypt_token` transparently falls
  back to that legacy key and schedules a one-shot background re-encryption
  of every legacy ciphertext with the active key
  (:func:`reencrypt_legacy_ciphertexts`), using compare-and-swap updates so a
  concurrent write is never overwritten.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken

from semabridge.auth.flags import auth_enabled

_encryption_logger = logging.getLogger(__name__)

# The fixed secret every install used when SEMABRIDGE_ENCRYPTION_KEY was unset.
# It is public (it was in the source), so it is only ever used to READ old
# ciphertexts during migration -- never to encrypt.
LEGACY_DEV_SECRET = "dev-only-insecure-key-do-not-use-in-production"


def _derive_fernet(secret: str) -> Fernet:
    # Fernet requires a 32-byte url-safe-base64 key; sha256 normalises length/format.
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encryption_key_file() -> Path:
    configured = os.environ.get("SEMABRIDGE_ENCRYPTION_KEY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".semabridge" / "encryption.key"


def _read_key_file(path: Path) -> Optional[str]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def _create_key_file(path: Path) -> str:
    """Create the key file atomically (O_EXCL) with 0600 perms; if another
    process won the race, return the key it wrote."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_secret = secrets.token_urlsafe(32)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        existing = _read_key_file(path)
        if existing:
            return existing
        raise RuntimeError(f"Encryption key file {path} exists but is empty")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(new_secret)
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass
    _encryption_logger.warning(
        "SEMABRIDGE_ENCRYPTION_KEY is not set: generated a new random encryption key "
        "and saved it to %s (mode 0600). Back this file up -- stored credentials "
        "cannot be decrypted without it.",
        path,
    )
    return new_secret


def _resolve_secret() -> str:
    secret = os.environ.get("SEMABRIDGE_ENCRYPTION_KEY", "").strip()
    if secret:
        return secret
    key_path = encryption_key_file()
    persisted = _read_key_file(key_path)
    if persisted:
        return persisted
    if auth_enabled():
        raise RuntimeError(
            "No encryption key configured. With AUTH_ENABLED=true you must set "
            "SEMABRIDGE_ENCRYPTION_KEY (generate one with: python -c \"import secrets; "
            f"print(secrets.token_urlsafe(32))\") or provide a key file at {key_path}."
        )
    return _create_key_file(key_path)


@lru_cache()
def _get_fernet() -> Fernet:
    """Fernet for the active key. Cached for the process lifetime;
    lru_cache does not cache raised exceptions, so a missing key keeps
    raising on every call."""
    return _derive_fernet(_resolve_secret())


@lru_cache()
def _legacy_fernet() -> Fernet:
    return _derive_fernet(LEGACY_DEV_SECRET)


def _active_is_legacy() -> bool:
    return _resolve_secret() == LEGACY_DEV_SECRET


def ensure_encryption_key_available() -> None:
    """Fail fast at startup when no key can be resolved (auth enabled and
    neither SEMABRIDGE_ENCRYPTION_KEY nor a persisted key file exists)."""
    _get_fernet()


def encrypt_token(plain_token: str) -> str:
    """Encrypts a string (e.g. auth token or password) with the active key."""
    if not plain_token:
        return ""
    f = _get_fernet()
    return f.encrypt(plain_token.encode("utf-8")).decode("utf-8")


def decrypt_token_ex(encrypted_token: str) -> Tuple[str, bool]:
    """Decrypt and report whether the legacy dev key was needed.

    Returns ``(plaintext, used_legacy_key)``; plaintext is ``""`` when the
    value cannot be decrypted with either key.
    """
    if not encrypted_token:
        return "", False
    raw = encrypted_token.encode("utf-8")
    try:
        return _get_fernet().decrypt(raw).decode("utf-8"), False
    except InvalidToken:
        pass
    except RuntimeError:
        raise
    except Exception as exc:  # malformed input
        _encryption_logger.error("Failed to decrypt token: %s", exc)
        return "", False
    if not _active_is_legacy():
        try:
            return _legacy_fernet().decrypt(raw).decode("utf-8"), True
        except Exception:
            pass
    _encryption_logger.error("Failed to decrypt token: invalid key or corrupted value")
    return "", False


def decrypt_token(encrypted_token: str) -> str:
    """Decrypts a previously encrypted string.

    Falls back to the legacy dev key for values written before a real key
    was configured, and schedules a one-shot background re-encryption of
    all such values with the active key.
    """
    plain, used_legacy = decrypt_token_ex(encrypted_token)
    if used_legacy:
        _schedule_legacy_reencryption()
    return plain


# ---------------------------------------------------------------------------
# Legacy ciphertext migration
# ---------------------------------------------------------------------------

_MIGRATION_LOCK = threading.Lock()
_MIGRATION_SCHEDULED = False


def _schedule_legacy_reencryption() -> None:
    """Start the re-encryption once per process, on a daemon thread so it
    never runs inside the caller's DB transaction."""
    global _MIGRATION_SCHEDULED
    if os.environ.get("SEMABRIDGE_LAZY_REENCRYPT", "true").strip().lower() in {"false", "0", "no", "off"}:
        return
    with _MIGRATION_LOCK:
        if _MIGRATION_SCHEDULED:
            return
        _MIGRATION_SCHEDULED = True
    _encryption_logger.warning(
        "Found credentials encrypted with the legacy dev key; re-encrypting them with the active key."
    )

    def _run() -> None:
        try:
            reencrypt_legacy_ciphertexts()
        except Exception as exc:  # noqa: BLE001
            _encryption_logger.warning("Legacy credential re-encryption failed: %s", exc)

    threading.Thread(target=_run, name="semabridge-reencrypt", daemon=True).start()


def _reencrypt_value(value: Optional[str]) -> Optional[str]:
    """Return the value re-encrypted with the active key if (and only if) it
    is a ciphertext readable solely with the legacy key; else None."""
    if not value or not isinstance(value, str) or not value.startswith("gAAAAA"):
        return None
    raw = value.encode("utf-8")
    try:
        _get_fernet().decrypt(raw)
        return None  # already on the active key
    except InvalidToken:
        pass
    try:
        plain = _legacy_fernet().decrypt(raw).decode("utf-8")
    except Exception:
        return None
    return encrypt_token(plain)


# (table, primary-key columns, encrypted value columns)
_ENCRYPTED_COLUMNS = (
    ("accounts", ("id",), ("encrypted_token", "refresh_token")),
    ("semabridge_credentials", ("owner_id", "service", "key"), ("value",)),
    ("user_credentials", ("id",), ("value",)),
)


def reencrypt_legacy_ciphertexts(session: Optional[Any] = None) -> Dict[str, int]:
    """Re-encrypt every stored value that only the legacy dev key can read.

    Each update is a compare-and-swap (``WHERE col = <old ciphertext>``), so
    a value rewritten concurrently (e.g. a token refresh) is left untouched.
    Safe to call repeatedly; returns ``{"table.column": rows_migrated}``.
    """
    if _active_is_legacy():
        return {}
    from sqlalchemy import text

    owns_session = session is None
    if owns_session:
        from semabridge.repository.orm.session_factory import db_manager

        session = db_manager.get_session_factory()()
    counts: Dict[str, int] = {}
    try:
        for table, pk_cols, value_cols in _ENCRYPTED_COLUMNS:
            for column in value_cols:
                select_cols = ", ".join((*pk_cols, column))
                try:
                    rows = session.execute(text(f"SELECT {select_cols} FROM {table}")).all()
                except Exception as exc:  # table/column missing on this schema
                    session.rollback()
                    _encryption_logger.debug("Skipping %s.%s: %s", table, column, exc)
                    continue
                migrated = 0
                for row in rows:
                    old_value = row[-1]
                    new_value = _reencrypt_value(old_value)
                    if new_value is None:
                        continue
                    where = " AND ".join(f"{pk} = :pk_{i}" for i, pk in enumerate(pk_cols))
                    params = {f"pk_{i}": row[i] for i in range(len(pk_cols))}
                    params.update({"new_value": new_value, "old_value": old_value})
                    result = session.execute(
                        text(f"UPDATE {table} SET {column} = :new_value WHERE {where} AND {column} = :old_value"),
                        params,
                    )
                    migrated += int(getattr(result, "rowcount", 0) or 0)
                session.commit()
                if migrated:
                    counts[f"{table}.{column}"] = migrated
        if counts:
            _encryption_logger.warning("Re-encrypted legacy credentials with the active key: %s", counts)
        return counts
    finally:
        if owns_session:
            session.close()
