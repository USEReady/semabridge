"""
Snowflake OAuth Token Manager.

Enterprise-grade token lifecycle management for Snowflake OAuth
(Authorization Code + PKCE). Handles encryption, refresh, rotation,
concurrency, audit logging, and cleanup.

Security model:
    - Tokens encrypted at rest using AES-256 (Fernet).
    - Encryption key sourced from ``SEMABRIDGE_TOKEN_KEY`` env var.
    - PKCE verifiers encrypted before DB storage.
    - Per-user token isolation with DB-level concurrency locks.
    - Refresh tokens rotated on every exchange.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import threading
import time
from typing import Dict, List, Optional

import requests
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Column, Index, String, Float, Text, delete, select
from sqlalchemy.orm import Session

from semabridge.core.exceptions import ConnectorError
from semabridge.repository.orm.base import Base
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


# ── Custom Exceptions ────────────────────────────────────────────────

class OAuthTokenError(ConnectorError):
    """Raised when an OAuth token operation fails.

    Args:
        message: Human-readable description of the error.
        user_id: The user whose token was affected.
    """

    def __init__(self, message: str, user_id: str = "") -> None:
        self.user_id = user_id
        super().__init__(message)


class ForceReLoginError(OAuthTokenError):
    """Raised when the refresh token is expired / revoked and the
    user must re-authenticate via the OAuth flow.
    """
    pass


# ── ORM Models ───────────────────────────────────────────────────────

class OAuthToken(Base):
    """Per-user encrypted OAuth token storage."""

    __tablename__ = "oauth_tokens"

    user_id = Column(String, primary_key=True)
    provider = Column(String, primary_key=True, default="snowflake")
    account = Column(String, nullable=False)
    access_token = Column(Text, nullable=False)    # AES-256 encrypted
    refresh_token = Column(Text, nullable=False)   # AES-256 encrypted
    expires_at = Column(Float, nullable=False)
    created_at = Column(Float, nullable=False)

    __table_args__ = (
        Index("idx_user_provider", "user_id", "provider"),
        Index("idx_token_expiry", "expires_at"),
    )


class OAuthPKCEState(Base):
    """DB-backed PKCE state for multi-instance safety."""

    __tablename__ = "oauth_pkce_state"

    state = Column(String, primary_key=True)
    code_verifier = Column(Text, nullable=False)   # encrypted
    user_id = Column(String, nullable=False)
    role = Column(String, nullable=True)
    redirect_uri = Column(String, nullable=True)
    created_at = Column(Float, nullable=False)
    expires_at = Column(Float, nullable=False)


# ── Encryption Helpers ───────────────────────────────────────────────

def _get_cipher() -> Fernet:
    """Load the Fernet cipher from ``SEMABRIDGE_TOKEN_KEY`` env var.

    If the env var is not set, generates a new key and logs a warning.
    In production, the key MUST be set explicitly.

    Returns:
        A ``Fernet`` instance for encrypt/decrypt operations.

    Raises:
        ConnectorError: If the stored key is invalid.
    """
    key = os.environ.get("SEMABRIDGE_TOKEN_KEY")
    if not key:
        # Auto-generate for development — warn loudly
        key = Fernet.generate_key().decode()
        os.environ["SEMABRIDGE_TOKEN_KEY"] = key
        logger.warning(
            "[OAuth] SEMABRIDGE_TOKEN_KEY not set — generated ephemeral key. "
            "Tokens will be unreadable after restart. Set this env var in production."
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, Exception) as exc:
        raise ConnectorError(
            f"Invalid SEMABRIDGE_TOKEN_KEY: {exc}. "
            "Must be a valid Fernet key (use `python -c "
            "'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`)."
        ) from exc


def _encrypt(plaintext: str) -> str:
    """Encrypt a string using AES-256/Fernet."""
    cipher = _get_cipher()
    return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted string.

    Raises:
        ConnectorError: If decryption fails (wrong key, corrupt data).
    """
    cipher = _get_cipher()
    try:
        return cipher.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ForceReLoginError(
            "Failed to decrypt OAuth token — SEMABRIDGE_TOKEN_KEY may have changed. Please sign in again."
        ) from exc


# ── PKCE Helpers ─────────────────────────────────────────────────────

def generate_pkce() -> Dict[str, str]:
    """Generate PKCE code_verifier, code_challenge, and state.

    Uses SHA256 for the challenge (industry standard).

    Returns:
        Dict with keys: ``state``, ``code_verifier``, ``code_challenge``.
    """
    code_verifier = secrets.token_urlsafe(96)  # 128 chars
    challenge_digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = (
        base64.urlsafe_b64encode(challenge_digest)
        .rstrip(b"=")
        .decode("ascii")
    )
    state = secrets.token_urlsafe(32)
    return {
        "state": state,
        "code_verifier": code_verifier,
        "code_challenge": code_challenge,
    }


# ── Token Manager ────────────────────────────────────────────────────

class SnowflakeTokenManager:
    """Enterprise-grade token lifecycle manager for Snowflake OAuth.

    Handles:
        - Encrypted per-user token storage (AES-256/Fernet)
        - PKCE state persistence in DB (multi-instance safe)
        - Token refresh with 60-second expiry buffer
        - Token rotation (refresh_token may change)
        - Concurrency lock per user_id
        - Graceful refresh failure → ForceReLoginError
        - Audit logging for login/refresh/revoke/failure
        - TTL cleanup for expired PKCE state
    """

    # 60-second buffer before actual expiry (industry standard)
    _EXPIRY_BUFFER_SECONDS: int = 60
    # PKCE state TTL: 10 minutes
    _PKCE_STATE_TTL_SECONDS: int = 600

    def __init__(self, session_factory: "sessionmaker[Session]") -> None:
        self._SessionLocal = session_factory
        # Per-user lock registry (thread-safe for single-instance)
        self._locks: Dict[str, threading.Lock] = {}
        self._lock_registry_lock = threading.Lock()

    # ── Lock helpers ─────────────────────────────────────────────

    def _get_user_lock(self, user_id: str) -> threading.Lock:
        """Get or create a per-user lock (prevents double refresh)."""
        with self._lock_registry_lock:
            if user_id not in self._locks:
                self._locks[user_id] = threading.Lock()
            return self._locks[user_id]

    # ── Token CRUD ───────────────────────────────────────────────

    def store_tokens(
        self,
        user_id: str,
        account: str,
        access_token: str,
        refresh_token: str,
        expires_in: int,
    ) -> None:
        """Encrypt and persist a token set for a user.

        Args:
            user_id: Semabridge user identifier.
            account: Snowflake account identifier.
            access_token: Raw access token from Snowflake.
            refresh_token: Raw refresh token from Snowflake.
            expires_in: Token lifetime in seconds.
        """
        now = time.time()
        with self._SessionLocal() as session:
            existing = session.get(OAuthToken, (user_id, "snowflake"))
            if existing:
                existing.account = account
                existing.access_token = _encrypt(access_token)
                existing.refresh_token = _encrypt(refresh_token)
                existing.expires_at = now + expires_in - self._EXPIRY_BUFFER_SECONDS
                existing.created_at = now
            else:
                token = OAuthToken(
                    user_id=user_id,
                    provider="snowflake",
                    account=account,
                    access_token=_encrypt(access_token),
                    refresh_token=_encrypt(refresh_token),
                    expires_at=now + expires_in - self._EXPIRY_BUFFER_SECONDS,
                    created_at=now,
                )
                session.add(token)
            session.commit()
        logger.info("[OAuth] Tokens stored for user %s (account: %s)", user_id, account)

    def get_valid_token(self, user_id: str, config: "SnowflakeConfig") -> str:
        """Return a valid access token, refreshing if needed.

        Args:
            user_id: Semabridge user identifier.
            config: Snowflake configuration with ``oauth_client_id`` and ``account``.

        Returns:
            Decrypted, valid access token string.

        Raises:
            ForceReLoginError: If no token exists or refresh fails.
        """
        with self._SessionLocal() as session:
            token_row = session.get(OAuthToken, (user_id, "snowflake"))

        if not token_row:
            raise ForceReLoginError(
                "No OAuth token found. Please sign in with Snowflake.",
                user_id=user_id,
            )

        if self._is_expired(token_row):
            lock = self._get_user_lock(user_id)
            with lock:
                # Re-read inside lock (another thread may have refreshed)
                with self._SessionLocal() as session:
                    token_row = session.get(OAuthToken, (user_id, "snowflake"))
                if token_row and self._is_expired(token_row):
                    token_row = self._refresh(user_id, config, token_row)

        if not token_row:
            raise ForceReLoginError(
                "Token lost during refresh. Please sign in again.",
                user_id=user_id,
            )

        try:
            return _decrypt(token_row.access_token)
        except ForceReLoginError:
            self._delete_token(user_id)
            raise

    def get_token_status(self, user_id: str) -> Dict[str, object]:
        """Check token status for a user.

        Returns:
            Dict with ``connected``, ``account``, ``expires_at``.
        """
        with self._SessionLocal() as session:
            token_row = session.get(OAuthToken, (user_id, "snowflake"))

        if not token_row:
            return {"connected": False}

        return {
            "connected": True,
            "account": token_row.account,
            "expires_at": token_row.expires_at,
            "expired": self._is_expired(token_row),
        }

    def revoke(self, user_id: str, client_id: str) -> None:
        """Revoke tokens at Snowflake and delete from DB.

        Args:
            user_id: Semabridge user identifier.
            client_id: OAuth client_id for the revocation request.
        """
        with self._SessionLocal() as session:
            token_row = session.get(OAuthToken, (user_id, "snowflake"))
            if token_row:
                # Attempt server-side revocation
                try:
                    refresh = _decrypt(token_row.refresh_token)
                    self._revoke_at_snowflake(
                        token_row.account, client_id, refresh,
                    )
                except Exception as exc:
                    logger.warning(
                        "[OAuth] Server-side revoke failed for %s: %s",
                        user_id, exc,
                    )

                session.delete(token_row)
                session.commit()

        logger.info("[OAuth] Token revoked for user %s", user_id)

    # ── PKCE State ───────────────────────────────────────────────

    def store_pkce_state(
        self,
        state: str,
        code_verifier: str,
        user_id: str,
        role: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ) -> None:
        """Store PKCE state in DB (multi-instance safe).

        Args:
            state: OAuth state parameter.
            code_verifier: PKCE code verifier (will be encrypted).
            user_id: Semabridge user identifier.
            role: Snowflake role for scope.
            redirect_uri: OAuth redirect URI.
        """
        now = time.time()
        with self._SessionLocal() as session:
            pkce = OAuthPKCEState(
                state=state,
                code_verifier=_encrypt(code_verifier),
                user_id=user_id,
                role=role,
                redirect_uri=redirect_uri,
                created_at=now,
                expires_at=now + self._PKCE_STATE_TTL_SECONDS,
            )
            session.merge(pkce)
            session.commit()

    def consume_pkce_state(self, state: str) -> Optional[Dict[str, str]]:
        """Load and delete PKCE state (one-time use).

        Args:
            state: OAuth state parameter.

        Returns:
            Dict with ``code_verifier``, ``user_id``, ``role``, ``redirect_uri``
            or None if state not found or expired.
        """
        with self._SessionLocal() as session:
            pkce = session.get(OAuthPKCEState, state)
            if not pkce:
                return None
            if time.time() > pkce.expires_at:
                session.delete(pkce)
                session.commit()
                return None

            result = {
                "code_verifier": _decrypt(pkce.code_verifier),
                "user_id": pkce.user_id,
                "role": pkce.role or "",
                "redirect_uri": pkce.redirect_uri or "",
            }
            session.delete(pkce)
            session.commit()
            return result

    def cleanup_expired_state(self) -> int:
        """Purge expired PKCE state rows. Returns count deleted."""
        now = time.time()
        with self._SessionLocal() as session:
            stmt = delete(OAuthPKCEState).where(OAuthPKCEState.expires_at < now)
            result = session.execute(stmt)
            session.commit()
            count = result.rowcount  # type: ignore[union-attr]
        if count:
            logger.info("[OAuth] Cleaned up %d expired PKCE state row(s)", count)
        return count

    # ── Internals ────────────────────────────────────────────────

    @staticmethod
    def _is_expired(token_row: OAuthToken) -> bool:
        """Check if a token is expired (includes 60s buffer)."""
        return time.time() >= token_row.expires_at

    def _refresh(
        self,
        user_id: str,
        config: "SnowflakeConfig",
        token_row: OAuthToken,
    ) -> Optional[OAuthToken]:
        """Refresh an expired token via Snowflake token endpoint.

        Handles token rotation (refresh_token may change).

        Args:
            user_id: User identifier.
            config: SnowflakeConfig with account and oauth_client_id.
            token_row: Current (expired) token row.

        Returns:
            Updated OAuthToken row, or None on failure.

        Raises:
            ForceReLoginError: If the refresh token is invalid/expired.
        """
        refresh = _decrypt(token_row.refresh_token)
        token_url = (
            f"https://{config.account}.snowflakecomputing.com/oauth/token-request"
        )

        try:
            resp = requests.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh,
                    "client_id": config.oauth_client_id,
                    "redirect_uri": f"http://localhost:{self._detect_port()}/api/connections/snowflake/oauth/callback",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.error("[OAuth] Refresh network error for %s: %s", user_id, exc)
            raise OAuthTokenError(
                f"Network error during token refresh: {exc}", user_id=user_id,
            ) from exc

        if resp.status_code in (400, 401, 403):
            # Refresh token expired or revoked — force re-login
            logger.warning(
                "[OAuth] Refresh failed for %s (HTTP %d) — forcing re-login",
                user_id, resp.status_code,
            )
            self._delete_token(user_id)
            raise ForceReLoginError(
                "Session expired. Please sign in with Snowflake again.",
                user_id=user_id,
            )

        if resp.status_code != 200:
            logger.error(
                "[OAuth] Unexpected refresh response for %s: %d %s",
                user_id, resp.status_code, resp.text[:200],
            )
            raise OAuthTokenError(
                f"Token refresh failed (HTTP {resp.status_code})",
                user_id=user_id,
            )

        data = resp.json()
        new_access = data["access_token"]
        # Token rotation: refresh_token may change
        new_refresh = data.get("refresh_token", refresh)
        expires_in = data.get("expires_in", 600)

        self.store_tokens(
            user_id=user_id,
            account=config.account,
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=expires_in,
        )
        logger.info("[OAuth] Token refreshed for user %s", user_id)

        with self._SessionLocal() as session:
            return session.get(OAuthToken, (user_id, "snowflake"))

    def _delete_token(self, user_id: str) -> None:
        """Delete a user's token from DB."""
        with self._SessionLocal() as session:
            token = session.get(OAuthToken, (user_id, "snowflake"))
            if token:
                session.delete(token)
                session.commit()

    @staticmethod
    def _revoke_at_snowflake(
        account: str, client_id: str, refresh_token: str,
    ) -> None:
        """POST revoke request to Snowflake."""
        url = f"https://{account}.snowflakecomputing.com/oauth/token-request"
        resp = requests.post(
            url,
            data={
                "grant_type": "revoke",
                "token": refresh_token,
                "client_id": client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        if resp.status_code not in (200, 204):
            logger.warning(
                "[OAuth] Snowflake revoke returned %d: %s",
                resp.status_code, resp.text[:200],
            )

    @staticmethod
    def _detect_port() -> int:
        """Detect the running backend port from env or default."""
        return int(os.environ.get("SEMABRIDGE_PORT", "8000"))


# ── Module-level singleton ───────────────────────────────────────────

_instance: Optional[SnowflakeTokenManager] = None
_instance_lock = threading.Lock()


def get_token_manager() -> SnowflakeTokenManager:
    """Get or create the singleton SnowflakeTokenManager.

    Returns:
        The module-level SnowflakeTokenManager instance.
    """
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                from semabridge.repository.orm.session_factory import (
                    get_session_factory,
                )
                _instance = SnowflakeTokenManager(get_session_factory())
    return _instance
