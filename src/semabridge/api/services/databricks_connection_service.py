"""Databricks connection management, REST/Unity Catalog client, and discovery service.

Provides:
- Encrypted credential helpers (Fernet via ``semabridge.auth.encryption``)
- Multi-user, multi-process safe OAuth (U2M refresh_token) and M2M (client_credentials)
  token refresh with real expiry tracking persisted in the DB
- Resilient ``DatabricksRestClient`` using ``semabridge.utils.tls.requests_verify(...)``
  with timeouts, 429/5xx exponential backoff retries, and clear 401/403 classification
- Structured connection test (auth -> warehouse state -> catalogs/schemas -> target check)
- Explicit warehouse start endpoint
- Unity Catalog effective-permissions check with least-privilege warnings & GRANT SQL
- Paginated ``/api/discovery/databricks/sources`` endpoint
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from semabridge.api.services.connection_session_store import (
    _POLL_SESSION_TTL,
    _cleanup_stale_poll_sessions,
    _get_client_ip,
    _last_poll_time,
    _poll_sessions,
    _poll_sessions_lock,
    _time,
    _uuid,
)
from semabridge.auth.encryption import decrypt_token, encrypt_token
from semabridge.auth.flags import auth_enabled
from semabridge.core.settings import reload_settings
from semabridge.domain.exceptions import InternalError, RateLimitError, ValidationError
from semabridge.utils.tls import requests_verify

logger = logging.getLogger("semabridge.api")

# Secret credential keys for Databricks — always encrypted at rest and masked in responses.
DATABRICKS_SECRET_KEYS = frozenset({"token", "client_secret", "access_token", "refresh_token"})
_MASK_SENTINELS = frozenset({"••••••••", "********", "••••", "***"})
_TOKEN_EXPIRY_BUFFER_SECONDS = 60

# HTTP resilience defaults
DEFAULT_HTTP_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Unity Catalog privilege sets for least-privilege auditing
_EXCESSIVE_CATALOG_PRIVILEGES = frozenset({
    "ALL PRIVILEGES",
    "MANAGE",
    "MODIFY",
    "CREATE CATALOG",
    "CREATE SCHEMA",
    "CREATE TABLE",
})
_EXCESSIVE_SCHEMA_PRIVILEGES = frozenset({
    "ALL PRIVILEGES",
    "MANAGE",
    "MODIFY",
    "CREATE TABLE",
})
_EXCESSIVE_TABLE_PRIVILEGES = frozenset({
    "ALL PRIVILEGES",
    "MANAGE",
    "MODIFY",
})


# ---------------------------------------------------------------------------
# Credential encryption, masking, and normalization helpers
# ---------------------------------------------------------------------------

def normalize_databricks_host(host: Optional[str]) -> str:
    """Strip scheme and trailing slashes from a Databricks workspace host."""
    val = str(host or "").strip().rstrip("/")
    if val.startswith("https://"):
        val = val[len("https://"):]
    elif val.startswith("http://"):
        val = val[len("http://"):]
    return val.rstrip("/")


def is_masked_placeholder(value: Any) -> bool:
    """Return True if ``value`` is a UI mask sentinel rather than a real secret."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return stripped in _MASK_SENTINELS or (len(stripped) >= 4 and set(stripped) <= {"•", "*"})


def decrypt_databricks_secret(value: Optional[str]) -> str:
    """Decrypt a Fernet-encrypted Databricks secret; pass through legacy plaintext."""
    if not value:
        return ""
    raw = str(value).strip()
    if not raw or is_masked_placeholder(raw):
        return ""
    if raw.startswith("gAAAAA"):
        return decrypt_token(raw)
    return raw


def encrypt_databricks_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Prepare a Databricks credential payload for encrypted persistence.

    - Normalizes ``host`` and aliases ``schema`` -> ``schema_name``.
    - Strips masked placeholder values so partial edits never overwrite real secrets.
    - Encrypts every secret key (``token``, ``client_secret``, ``access_token``,
      ``refresh_token``) with Fernet (``encrypt_token``).
    """
    out: Dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if value is None:
            continue
        norm_key = "schema_name" if key == "schema" else key
        if norm_key == "host":
            cleaned = normalize_databricks_host(str(value))
            if cleaned:
                out["host"] = cleaned
            continue
        if norm_key in DATABRICKS_SECRET_KEYS:
            raw = str(value).strip()
            if not raw or is_masked_placeholder(raw):
                continue
            out[norm_key] = raw if raw.startswith("gAAAAA") else encrypt_token(raw)
        else:
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    out[norm_key] = cleaned
            else:
                out[norm_key] = value
    return out


def mask_databricks_credentials(creds: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of ``creds`` with every secret key replaced by a mask."""
    masked: Dict[str, Any] = {}
    for key, value in (creds or {}).items():
        if key in DATABRICKS_SECRET_KEYS and value:
            masked[key] = "••••••••"
        else:
            masked[key] = value
    return masked


def _redact_secrets_from_text(text: str, secrets: Sequence[str] = ()) -> str:
    """Ensure no known secret token value ever leaks into error messages."""
    safe = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 6 and secret in safe:
            safe = safe.replace(secret, "[REDACTED]")
    return safe


# ---------------------------------------------------------------------------
# Request user & ownership resolution
# ---------------------------------------------------------------------------

def _resolve_request_user_id(request: Optional[Request]) -> int:
    """Resolve authenticated user ID; raise 401 when auth is enabled and missing."""
    if request is None:
        if auth_enabled():
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return 0

    user_id = 0
    try:
        from semabridge.api.deps import get_current_user_optional

        db = getattr(request.state, "db", None)
        if db is None:
            from semabridge.repository.orm.session_factory import db_manager

            with db_manager.get_session() as session:
                user = get_current_user_optional(request, session)
                user_id = user.id if user else 0
        else:
            user = get_current_user_optional(request, db)
            user_id = user.id if user else 0
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("Could not resolve request user_id: %s", exc)
        user_id = 0

    if auth_enabled() and not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


# ---------------------------------------------------------------------------
# Structured Databricks API Error & Resilient HTTP Client
# ---------------------------------------------------------------------------

class DatabricksAPIError(Exception):
    """Structured error raised by ``DatabricksRestClient`` (never leaks secrets)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        category: str = "api_error",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.category = category


def _extract_databricks_error_detail(resp: requests.Response) -> str:
    """Extract a concise, safe error message from a Databricks HTTP error response."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            msg = data.get("message") or data.get("error_description") or data.get("error") or ""
            code = data.get("error_code") or ""
            if code and msg:
                return f"{code}: {str(msg)[:240]}"
            if msg:
                return str(msg)[:240]
    except Exception as exc:
        logger.debug("Non-JSON Databricks error body (%s): %s", resp.status_code, exc)
    text = (getattr(resp, "text", "") or "").strip()
    return text[:240] if text else f"HTTP {resp.status_code}"


def _parse_retry_after(resp: requests.Response, default_delay: float) -> float:
    """Parse ``Retry-After`` header (capped at 10s), falling back to ``default_delay``."""
    raw = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
    if raw:
        try:
            val = float(raw)
            if val >= 0:
                return min(val, 10.0)
        except ValueError:
            logger.debug("Ignoring non-numeric Retry-After header: %s", raw)
    return default_delay


class DatabricksRestClient:
    """Thin, resilient REST client for Databricks SQL Warehouses and Unity Catalog.

    - Every call passes ``verify=requests_verify(...)``.
    - Retries with exponential backoff on HTTP 429 and 5xx (plus transient
      timeouts/connection errors for GET requests).
    - Produces clear, user-facing errors distinguishing 401 (bad/expired credentials)
      from 403 (insufficient permissions) without leaking tokens or stack traces.
    """

    def __init__(
        self,
        host: str,
        bearer_token: str,
        *,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
        session: Optional[requests.Session] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        cleaned_host = normalize_databricks_host(host)
        if not cleaned_host:
            raise DatabricksAPIError(
                "Databricks workspace host is required.",
                status_code=400,
                category="validation",
            )
        if not bearer_token:
            raise DatabricksAPIError(
                "Authentication failed: no Databricks access token or PAT available.",
                status_code=401,
                category="auth",
            )
        self.host = cleaned_host
        self.base_url = f"https://{cleaned_host}"
        self._token = bearer_token
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.backoff_base = max(0.0, float(backoff_base))
        self._session = session
        self._sleep = sleep_fn

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        purpose: str = "Databricks REST API",
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        verify = requests_verify(purpose)
        requester = self._session.request if self._session is not None else requests.request
        can_retry_transport = method.upper() == "GET"
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                resp = requester(
                    method.upper(),
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                    verify=verify,
                )
            except requests.exceptions.SSLError as exc:
                raise DatabricksAPIError(
                    f"TLS verification failed connecting to {self.host}. "
                    "If behind a corporate proxy, set SEMABRIDGE_TLS_CA_BUNDLE to your CA bundle.",
                    category="tls",
                ) from exc
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                if can_retry_transport and attempt < self.max_retries - 1:
                    self._sleep(self.backoff_base * (2 ** attempt))
                    continue
                raise DatabricksAPIError(
                    f"Could not reach Databricks workspace '{self.host}': request timed out or connection failed.",
                    category="network",
                ) from exc
            except requests.exceptions.RequestException as exc:
                raise DatabricksAPIError(
                    f"Network error communicating with Databricks workspace '{self.host}'.",
                    category="network",
                ) from exc

            status = resp.status_code
            if 200 <= status < 300:
                if not getattr(resp, "content", b"") and not getattr(resp, "text", ""):
                    return {}
                try:
                    data = resp.json()
                    return data if isinstance(data, dict) else {"items": data}
                except ValueError:
                    return {}

            if status in _RETRYABLE_STATUS_CODES and attempt < self.max_retries - 1:
                delay = _parse_retry_after(resp, self.backoff_base * (2 ** attempt))
                logger.warning(
                    "Databricks %s %s returned %d (attempt %d/%d); retrying in %.2fs",
                    method.upper(),
                    path,
                    status,
                    attempt + 1,
                    self.max_retries,
                    delay,
                )
                self._sleep(delay)
                continue

            detail = _redact_secrets_from_text(_extract_databricks_error_detail(resp), (self._token,))
            if status == 401:
                raise DatabricksAPIError(
                    "Authentication failed (401): invalid, expired, or revoked Databricks token/credentials. "
                    f"Verify your credentials and try again. ({detail})",
                    status_code=401,
                    category="auth",
                )
            if status == 403:
                raise DatabricksAPIError(
                    "Permission denied (403): your Databricks identity is authenticated but lacks "
                    f"the required privilege for this operation. ({detail})",
                    status_code=403,
                    category="permission",
                )
            if status == 404:
                raise DatabricksAPIError(
                    f"Databricks resource not found (404): {detail}",
                    status_code=404,
                    category="not_found",
                )
            if status == 429:
                raise DatabricksAPIError(
                    f"Databricks rate limit exceeded (429) after {self.max_retries} attempts: {detail}",
                    status_code=429,
                    category="rate_limit",
                )
            if status >= 500:
                raise DatabricksAPIError(
                    f"Databricks service error ({status}) after {self.max_retries} attempts: {detail}",
                    status_code=status,
                    category="server_error",
                )
            raise DatabricksAPIError(
                f"Databricks API request failed ({status}): {detail}",
                status_code=status,
                category="api_error",
            )

        raise DatabricksAPIError(
            f"Databricks request to {path} failed after {self.max_retries} attempts.",
            category="network",
        ) from last_exc

    # --- Identity ---

    def get_current_user(self) -> Dict[str, Any]:
        """Fetch current authenticated principal via SCIM Me endpoint."""
        return self._request("GET", "/api/2.0/preview/scim/v2/Me", purpose="Databricks SCIM Me")

    # --- SQL Warehouses ---

    def get_warehouse(self, warehouse_id: str) -> Dict[str, Any]:
        """Fetch SQL warehouse metadata and runtime state."""
        encoded = urllib.parse.quote(warehouse_id.strip(), safe="")
        return self._request(
            "GET",
            f"/api/2.0/sql/warehouses/{encoded}",
            purpose="Databricks SQL Warehouse status",
        )

    def start_warehouse(self, warehouse_id: str) -> Dict[str, Any]:
        """Explicitly trigger starting a stopped SQL warehouse."""
        encoded = urllib.parse.quote(warehouse_id.strip(), safe="")
        return self._request(
            "POST",
            f"/api/2.0/sql/warehouses/{encoded}/start",
            json_body={},
            purpose="Databricks SQL Warehouse start",
        )

    # --- Unity Catalog Metadata ---

    def list_catalogs(
        self,
        *,
        page_token: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {}
        if page_token:
            params["page_token"] = page_token
        if max_results:
            params["max_results"] = max_results
        data = self._request(
            "GET",
            "/api/2.1/unity-catalog/catalogs",
            params=params or None,
            purpose="Databricks Unity Catalog list catalogs",
        )
        items = data.get("catalogs") if isinstance(data.get("catalogs"), list) else []
        return items, data.get("next_page_token") or None

    def list_all_catalogs(self, max_pages: int = 20) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        token: Optional[str] = None
        for _ in range(max_pages):
            batch, token = self.list_catalogs(page_token=token)
            results.extend(batch)
            if not token:
                break
        return results

    def list_schemas(
        self,
        catalog_name: str,
        *,
        page_token: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {"catalog_name": catalog_name}
        if page_token:
            params["page_token"] = page_token
        if max_results:
            params["max_results"] = max_results
        data = self._request(
            "GET",
            "/api/2.1/unity-catalog/schemas",
            params=params,
            purpose="Databricks Unity Catalog list schemas",
        )
        items = data.get("schemas") if isinstance(data.get("schemas"), list) else []
        return items, data.get("next_page_token") or None

    def list_all_schemas(self, catalog_name: str, max_pages: int = 20) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        token: Optional[str] = None
        for _ in range(max_pages):
            batch, token = self.list_schemas(catalog_name, page_token=token)
            results.extend(batch)
            if not token:
                break
        return results

    def list_tables(
        self,
        catalog_name: str,
        schema_name: str,
        *,
        page_token: Optional[str] = None,
        max_results: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        params: Dict[str, Any] = {
            "catalog_name": catalog_name,
            "schema_name": schema_name,
        }
        if page_token:
            params["page_token"] = page_token
        if max_results:
            params["max_results"] = max_results
        data = self._request(
            "GET",
            "/api/2.1/unity-catalog/tables",
            params=params,
            purpose="Databricks Unity Catalog list tables",
        )
        items = data.get("tables") if isinstance(data.get("tables"), list) else []
        return items, data.get("next_page_token") or None

    def get_effective_permissions(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: Optional[str] = None,
    ) -> Dict[str, Any]:
        encoded_type = urllib.parse.quote(securable_type.strip().lower(), safe="")
        encoded_name = urllib.parse.quote(full_name.strip(), safe="")
        params = {"principal": principal} if principal else None
        return self._request(
            "GET",
            f"/api/2.1/unity-catalog/effective-permissions/{encoded_type}/{encoded_name}",
            params=params,
            purpose="Databricks Unity Catalog effective permissions",
        )


# ---------------------------------------------------------------------------
# OAuth / M2M token exchange & refresh (with TLS + retries + DB persistence)
# ---------------------------------------------------------------------------

def _post_oidc_token(
    host: str,
    form_data: Dict[str, str],
    *,
    purpose: str = "Databricks OIDC token",
    timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    """POST to ``https://{host}/oidc/v1/token`` with TLS verification and retries."""
    cleaned_host = normalize_databricks_host(host)
    if not cleaned_host:
        raise DatabricksAPIError("Databricks workspace host is required.", status_code=400, category="validation")

    token_url = f"https://{cleaned_host}/oidc/v1/token"
    verify = requests_verify(purpose)
    secrets_to_redact = tuple(
        v for k, v in form_data.items() if k in {"client_secret", "refresh_token", "code_verifier"} and v
    )

    for attempt in range(max(1, int(max_retries))):
        try:
            resp = requests.post(
                token_url,
                data=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout,
                verify=verify,
            )
        except requests.exceptions.SSLError as exc:
            raise DatabricksAPIError(
                f"TLS verification failed contacting {cleaned_host}/oidc/v1/token. "
                "Set SEMABRIDGE_TLS_CA_BUNDLE if behind a TLS-intercepting proxy.",
                category="tls",
            ) from exc
        except requests.exceptions.RequestException as exc:
            if attempt < max_retries - 1:
                sleep_fn(backoff_base * (2 ** attempt))
                continue
            raise DatabricksAPIError(
                f"Could not reach Databricks OIDC endpoint on '{cleaned_host}'.",
                category="network",
            ) from exc

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise DatabricksAPIError(
                    "Databricks OIDC endpoint returned an invalid JSON response.",
                    status_code=502,
                    category="server_error",
                ) from exc

        if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries - 1:
            delay = _parse_retry_after(resp, backoff_base * (2 ** attempt))
            sleep_fn(delay)
            continue

        detail = _redact_secrets_from_text(_extract_databricks_error_detail(resp), secrets_to_redact)
        if resp.status_code in (400, 401):
            raise DatabricksAPIError(
                f"Authentication failed ({resp.status_code}): Databricks rejected the OAuth/Service Principal credentials. ({detail})",
                status_code=401,
                category="auth",
            )
        if resp.status_code == 403:
            raise DatabricksAPIError(
                f"Permission denied (403) during Databricks token exchange. ({detail})",
                status_code=403,
                category="permission",
            )
        raise DatabricksAPIError(
            f"Databricks token endpoint failed ({resp.status_code}): {detail}",
            status_code=resp.status_code,
            category="server_error" if resp.status_code >= 500 else "api_error",
        )

    raise DatabricksAPIError(
        f"Databricks token endpoint on '{cleaned_host}' failed after retries.",
        category="network",
    )


def _persist_refreshed_tokens(
    *,
    access_token: str,
    expires_at: float,
    refresh_token: Optional[str] = None,
    user_id: int = 0,
    account_id: Optional[str] = None,
    db: Optional[Session] = None,
    persist_to_credential_manager: bool = False,
) -> None:
    """Persist refreshed encrypted tokens back to DB (Account and/or CredentialManager)."""
    if account_id:
        try:
            from semabridge.repository.orm.models import Account
            from semabridge.repository.orm.session_factory import db_manager

            owns_session = db is None
            session = db_manager.get_session_factory()() if owns_session else db
            try:
                account = session.execute(
                    select(Account).where(Account.id == account_id)
                ).scalar_one_or_none()
                if account is not None:
                    bundle: Dict[str, Any] = {}
                    if account.encrypted_token:
                        decrypted = decrypt_token(account.encrypted_token)
                        if decrypted:
                            try:
                                parsed = json.loads(decrypted)
                                if isinstance(parsed, dict):
                                    bundle.update(parsed)
                            except json.JSONDecodeError:
                                logger.debug("Account %s encrypted_token is not JSON; replacing with refreshed token", account_id)
                    bundle["access_token"] = access_token
                    bundle["expires_at"] = str(int(expires_at))
                    if refresh_token:
                        bundle["refresh_token"] = refresh_token
                        account.refresh_token = encrypt_token(refresh_token)
                    account.encrypted_token = encrypt_token(json.dumps(bundle))
                    account.token_expires_at = datetime.fromtimestamp(expires_at, tz=timezone.utc)
                    session.commit()
            finally:
                if owns_session:
                    session.close()
        except Exception as exc:
            logger.warning("Failed to persist refreshed Databricks token to Account %s: %s", account_id, exc)

    if persist_to_credential_manager:
        try:
            from semabridge.repository.credential_manager import CredentialManager

            cm = CredentialManager()
            update_fields: Dict[str, Any] = {
                "access_token": encrypt_token(access_token),
                "expires_at": str(int(expires_at)),
            }
            if refresh_token:
                update_fields["refresh_token"] = encrypt_token(refresh_token)
            cm.save_credentials("databricks", update_fields, user_id=user_id)
        except Exception as exc:
            logger.warning("Failed to persist refreshed Databricks token to CredentialManager (owner=%d): %s", user_id, exc)


def resolve_databricks_bearer_token(
    creds: Dict[str, Any],
    *,
    user_id: int = 0,
    account_id: Optional[str] = None,
    db: Optional[Session] = None,
    force_refresh: bool = False,
    persist: bool = True,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Tuple[str, Optional[float]]:
    """Resolve a valid Databricks Bearer token for PAT, Service Principal (M2M), or OAuth (U2M).

    Handles real expiry checking (with a 60-second safety buffer) and persists
    refreshed tokens back to DB so multi-user and multi-process workers stay in sync.
    """
    auth_type = str(creds.get("auth_type") or "pat").strip().lower()
    host = normalize_databricks_host(creds.get("host"))
    now = now_fn()

    if auth_type == "pat":
        token = decrypt_databricks_secret(creds.get("token"))
        if not token:
            raise DatabricksAPIError(
                "Authentication failed: Personal Access Token (PAT) is missing.",
                status_code=401,
                category="auth",
            )
        return token, None

    if auth_type in ("service_principal", "m2m", "client_credentials"):
        cached_token = decrypt_databricks_secret(creds.get("access_token"))
        expires_at = _parse_expires_at(creds.get("expires_at"))
        if (
            not force_refresh
            and cached_token
            and expires_at is not None
            and now < (expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS)
        ):
            return cached_token, expires_at

        client_id = str(creds.get("client_id") or "").strip()
        client_secret = decrypt_databricks_secret(creds.get("client_secret"))
        if not host or not client_id or not client_secret:
            raise DatabricksAPIError(
                "Authentication failed: Service Principal auth requires host, client_id, and client_secret.",
                status_code=401,
                category="auth",
            )

        token_data = _post_oidc_token(
            host,
            {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "all-apis",
            },
            purpose="Databricks M2M OAuth token",
            sleep_fn=sleep_fn,
        )
        access_token = str(token_data.get("access_token") or "").strip()
        if not access_token:
            raise DatabricksAPIError(
                "Authentication failed: Databricks OIDC response did not include an access_token.",
                status_code=401,
                category="auth",
            )
        expires_in = int(token_data.get("expires_in") or 3600)
        new_expires_at = now + expires_in
        if persist:
            _persist_refreshed_tokens(
                access_token=access_token,
                expires_at=new_expires_at,
                user_id=user_id,
                account_id=account_id,
                db=db,
                persist_to_credential_manager=bool(creds.get("_from_credential_manager")),
            )
        return access_token, new_expires_at

    if auth_type in ("interactive", "oauth", "u2m"):
        access_token = decrypt_databricks_secret(creds.get("access_token"))
        refresh_token = decrypt_databricks_secret(creds.get("refresh_token"))
        expires_at = _parse_expires_at(creds.get("expires_at"))

        is_expired = (
            force_refresh
            or not access_token
            or (expires_at is not None and now >= (expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS))
        )
        if not is_expired and access_token:
            return access_token, expires_at

        client_id = str(creds.get("client_id") or "").strip()
        if not refresh_token or not host or not client_id:
            raise DatabricksAPIError(
                "Databricks OAuth session has expired and cannot be refreshed automatically. Please sign in again.",
                status_code=401,
                category="auth",
            )

        token_data = _post_oidc_token(
            host,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            purpose="Databricks OAuth token refresh",
            sleep_fn=sleep_fn,
        )
        new_access_token = str(token_data.get("access_token") or "").strip()
        if not new_access_token:
            raise DatabricksAPIError(
                "Databricks OAuth token refresh did not return an access_token. Please sign in again.",
                status_code=401,
                category="auth",
            )
        new_refresh_token = str(token_data.get("refresh_token") or refresh_token).strip()
        expires_in = int(token_data.get("expires_in") or 3600)
        new_expires_at = now + expires_in
        if persist:
            _persist_refreshed_tokens(
                access_token=new_access_token,
                expires_at=new_expires_at,
                refresh_token=new_refresh_token,
                user_id=user_id,
                account_id=account_id,
                db=db,
                persist_to_credential_manager=bool(creds.get("_from_credential_manager")),
            )
        return new_access_token, new_expires_at

    raise DatabricksAPIError(
        f"Unsupported Databricks auth_type '{auth_type}'. Expected 'pat', 'service_principal', or 'interactive'.",
        status_code=400,
        category="validation",
    )


def _parse_expires_at(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, datetime):
        return raw.timestamp()
    try:
        return float(str(raw).strip())
    except ValueError:
        try:
            return datetime.fromisoformat(str(raw).strip()).timestamp()
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Per-user / per-account credential loading with owner checks
# ---------------------------------------------------------------------------

def _load_account_databricks_credentials(
    connection_id: str,
    user_id: int,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Load and decrypt a Databricks Account row, enforcing owner isolation."""
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager

    owns_session = db is None
    session = db_manager.get_session_factory()() if owns_session else db
    try:
        account = session.execute(
            select(Account).where(Account.id == connection_id)
        ).scalar_one_or_none()
        if account is None or str(account.connector_type or "").upper() != "DATABRICKS":
            raise HTTPException(status_code=404, detail="Databricks connection not found.")

        if user_id > 0:
            if account.owner_id is not None and account.owner_id != user_id:
                raise HTTPException(status_code=403, detail="Access denied to Databricks connection.")
            if auth_enabled() and account.owner_id != user_id:
                raise HTTPException(status_code=403, detail="Access denied to Databricks connection.")

        bundle: Dict[str, Any] = {}
        if account.encrypted_token:
            decrypted = decrypt_token(account.encrypted_token)
            if decrypted:
                try:
                    parsed = json.loads(decrypted)
                    if isinstance(parsed, dict):
                        bundle.update(parsed)
                    else:
                        bundle["access_token"] = decrypted
                except json.JSONDecodeError:
                    bundle["access_token"] = decrypted

        if account.refresh_token and not bundle.get("refresh_token"):
            bundle["refresh_token"] = decrypt_token(account.refresh_token)
        if account.auth_type and not bundle.get("auth_type"):
            bundle["auth_type"] = account.auth_type
        if account.token_expires_at and not bundle.get("expires_at"):
            bundle["expires_at"] = str(int(account.token_expires_at.timestamp()))
        if account.identity_email and not bundle.get("account_username"):
            bundle["account_username"] = account.identity_email

        # Decrypt any nested Fernet-encrypted values inside the bundle
        for secret_key in DATABRICKS_SECRET_KEYS:
            if bundle.get(secret_key):
                bundle[secret_key] = decrypt_databricks_secret(bundle[secret_key])
        if bundle.get("schema") and not bundle.get("schema_name"):
            bundle["schema_name"] = bundle["schema"]
        bundle["_account_id"] = str(account.id)
        return bundle
    finally:
        if owns_session:
            session.close()


def _load_saved_databricks_credentials(user_id: int = 0) -> Dict[str, Any]:
    """Load the user's saved Databricks credentials from CredentialManager (decrypted)."""
    from semabridge.repository.credential_manager import CredentialManager

    cm = CredentialManager()
    raw = cm.get_credentials(
        "databricks",
        mask_secrets=False,
        user_id=user_id,
        include_env_fallback=not auth_enabled(),
    )
    if not raw:
        return {}
    out: Dict[str, Any] = dict(raw)
    for secret_key in DATABRICKS_SECRET_KEYS:
        if out.get(secret_key):
            out[secret_key] = decrypt_databricks_secret(out[secret_key])
    out["_from_credential_manager"] = True
    return out


def resolve_effective_databricks_credentials(
    user_id: int,
    payload: Optional[Dict[str, Any]] = None,
    *,
    db: Optional[Session] = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    """Merge saved connection credentials (by ``connection_id`` or user scope) with draft overrides.

    Returns ``(decrypted_creds_dict, account_id_or_None)``.
    """
    draft = dict(payload or {})
    connection_id = str(
        draft.get("connection_id") or draft.get("account_id") or draft.get("identity_id") or ""
    ).strip()

    base: Dict[str, Any] = {}
    account_id: Optional[str] = None

    if connection_id:
        base = _load_account_databricks_credentials(connection_id, user_id, db=db)
        account_id = base.get("_account_id") or connection_id
    else:
        base = _load_saved_databricks_credentials(user_id=user_id)
        if not base:
            # Also check if the user has a saved Account row for DATABRICKS
            try:
                from semabridge.repository.orm.models import Account
                from semabridge.repository.orm.session_factory import db_manager

                owns_session = db is None
                session = db_manager.get_session_factory()() if owns_session else db
                try:
                    stmt = select(Account).where(Account.connector_type == "DATABRICKS")
                    if user_id > 0:
                        stmt = stmt.where(Account.owner_id == user_id)
                    acct = session.execute(stmt).scalars().first()
                    if acct is not None:
                        base = _load_account_databricks_credentials(str(acct.id), user_id, db=session)
                        account_id = str(acct.id)
                finally:
                    if owns_session:
                        session.close()
            except HTTPException:
                raise
            except Exception as exc:
                logger.debug("Could not fall back to Account table for Databricks credentials: %s", exc)

    # Overlay non-empty, non-masked draft fields on top of base credentials
    merged = dict(base)
    if draft.get("schema") and not draft.get("schema_name"):
        draft["schema_name"] = draft["schema"]
    for key in (
        "host",
        "auth_type",
        "token",
        "client_id",
        "client_secret",
        "access_token",
        "refresh_token",
        "warehouse_id",
        "catalog",
        "schema_name",
    ):
        val = draft.get(key)
        if val is None:
            continue
        str_val = str(val).strip()
        if not str_val or is_masked_placeholder(str_val):
            continue
        if key == "host":
            merged[key] = normalize_databricks_host(str_val)
        elif key in DATABRICKS_SECRET_KEYS:
            merged[key] = decrypt_databricks_secret(str_val)
        else:
            merged[key] = str_val

    if merged.get("host"):
        merged["host"] = normalize_databricks_host(merged["host"])

    return merged, account_id


# ---------------------------------------------------------------------------
# OAuth PKCE Login / Callback / Poll / Auth Status / Logout
# ---------------------------------------------------------------------------

async def databricks_native_oauth_login(request: Request, payload: Dict[str, Any] = None):
    """Initiate native Databricks OAuth authorization-code flow with PKCE."""
    _cleanup_stale_poll_sessions()

    if not payload:
        raise ValidationError("Missing payload")

    host = normalize_databricks_host(payload.get("host"))
    client_id = str(payload.get("client_id") or "").strip()
    redirect_uri = str(payload.get("redirect_uri") or "").strip()

    if not host or not client_id or not redirect_uri:
        raise ValidationError("host, client_id, and redirect_uri are required.")

    code_verifier = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8").rstrip("=")
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("utf-8")).digest()
    ).decode("utf-8").rstrip("=")

    flow_id = str(_uuid.uuid4())
    user_ip = _get_client_ip(request)
    user_id = _resolve_request_user_id(request)

    with _poll_sessions_lock:
        _poll_sessions[flow_id] = {
            "status": "polling",
            "service": "databricks",
            "expires_at": _time.time() + _POLL_SESSION_TTL,
            "user_ip": user_ip,
            "user_id": user_id,
            "host": host,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "warehouse_id": payload.get("warehouse_id", ""),
            "catalog": payload.get("catalog", ""),
            "schema_name": payload.get("schema_name") or payload.get("schema") or "",
        }

    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "offline_access all-apis",
        "state": flow_id,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }

    auth_url = f"https://{host}/oidc/v1/authorize?" + urllib.parse.urlencode(auth_params)
    return {
        "flow_id": flow_id,
        "verification_uri": auth_url,
        "user_code": "",
        "message": "Please log in using the opened browser tab.",
    }


async def databricks_oauth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    error_description: str = None,
):
    """Callback endpoint for Databricks native OAuth redirect."""
    from fastapi.responses import HTMLResponse

    html_template = """
    <html>
        <head><title>Databricks Login</title><style>body {{ font-family: sans-serif; text-align: center; padding-top: 50px; }}</style></head>
        <body>
            <h2>{title}</h2>
            <p>{message}</p>
            <script>setTimeout(function() {{ window.close(); }}, 3000);</script>
        </body>
    </html>
    """

    if error:
        return HTMLResponse(
            html_template.format(
                title="Authentication Failed",
                message=f"Databricks returned an error: {error} - {error_description}",
            )
        )

    if not code or not state:
        return HTMLResponse(
            html_template.format(title="Error", message="Missing code or state parameter.")
        )

    with _poll_sessions_lock:
        session_data = _poll_sessions.get(state)
        if not session_data or session_data.get("service") != "databricks":
            return HTMLResponse(
                html_template.format(
                    title="Error",
                    message="Session expired or invalid. Please try logging in again.",
                )
            )

    host = normalize_databricks_host(session_data["host"])
    form_payload = {
        "grant_type": "authorization_code",
        "client_id": session_data["client_id"],
        "redirect_uri": session_data["redirect_uri"],
        "code": code,
        "code_verifier": session_data["code_verifier"],
    }

    try:
        token_data = _post_oidc_token(
            host,
            form_payload,
            purpose="Databricks OAuth callback token exchange",
        )
        with _poll_sessions_lock:
            _poll_sessions[state].update(
                {
                    "status": "success",
                    "access_token": token_data.get("access_token"),
                    "refresh_token": token_data.get("refresh_token", ""),
                    "expires_in": token_data.get("expires_in", 3600),
                    "username": token_data.get("user_id", "databricks_user"),
                }
            )

        return HTMLResponse(
            html_template.format(
                title="Authentication Successful!",
                message="You have successfully logged in. You can close this tab and return to Semabridge.",
            )
        )
    except DatabricksAPIError as exc:
        logger.warning("Failed to exchange Databricks OAuth code: %s", exc.message)
        with _poll_sessions_lock:
            _poll_sessions[state]["status"] = "failed"
            _poll_sessions[state]["message"] = exc.message
        return HTMLResponse(
            html_template.format(
                title="Error",
                message="Failed to exchange authorization code.",
            )
        )
    except Exception as exc:
        logger.exception("Error during Databricks token exchange: %s", exc)
        with _poll_sessions_lock:
            _poll_sessions[state]["status"] = "failed"
            _poll_sessions[state]["message"] = "Internal error during token exchange."
        return HTMLResponse(
            html_template.format(title="Error", message="Internal error during token exchange.")
        )


async def databricks_device_code_poll(request: Request, payload: Dict[str, Any] = None):
    """Return the Databricks OAuth flow status for a specific flow id."""
    from semabridge.repository.credential_manager import CredentialManager

    flow_id = (payload or {}).get("flow_id", "")
    if not flow_id:
        raise ValidationError(str({"status": "error", "message": "Missing flow_id."}))

    now = _time.time()
    if now - _last_poll_time.get(flow_id, 0) < 1.0:
        raise RateLimitError(str({"status": "too_many_requests", "message": "Slow down polling."}))
    _last_poll_time[flow_id] = now

    with _poll_sessions_lock:
        state = _poll_sessions.get(flow_id, {}).copy()

    if not state:
        raise ValidationError(str({"status": "expired", "message": "Unknown or expired flow_id. Please login again."}))

    user_ip = _get_client_ip(request)
    session_ip = state.get("user_ip")
    if session_ip and session_ip != user_ip:
        logger.warning(
            "[FlowID=%s] Databricks IP Mismatch! Login: %s, Poll: %s",
            flow_id,
            session_ip,
            user_ip,
        )

    if state["status"] == "polling":
        return {"status": "pending", "message": "Waiting for user to authenticate in browser..."}

    if state["status"] == "success":
        logger.info("[FlowID=%s] Databricks login success.", flow_id)
        with _poll_sessions_lock:
            _poll_sessions.pop(flow_id, None)
            _last_poll_time.pop(flow_id, None)

        owner_id: int = int(state.get("user_id") or 0)
        extra = payload or {}
        host = normalize_databricks_host(extra.get("host") or state.get("host") or "")
        warehouse_id = str(extra.get("warehouse_id") or state.get("warehouse_id") or "").strip()
        catalog = str(extra.get("catalog") or state.get("catalog") or "").strip()
        schema_name = str(
            extra.get("schema_name") or extra.get("schema") or state.get("schema_name") or ""
        ).strip()
        expires_in = int(state.get("expires_in", 3600))
        expires_at = _time.time() + expires_in
        access_token = state["access_token"]
        refresh_token = state.get("refresh_token", "")
        username = state.get("username", "databricks_user")
        client_id = state.get("client_id", "")

        try:
            cm = CredentialManager()
            encrypted_creds = encrypt_databricks_payload(
                {
                    "auth_type": "interactive",
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "account_username": username,
                    "expires_at": str(int(expires_at)),
                    "host": host,
                    "client_id": client_id,
                    "warehouse_id": warehouse_id,
                    "catalog": catalog,
                    "schema_name": schema_name,
                }
            )
            cm.save_credentials("databricks", encrypted_creds, user_id=owner_id)

            # Persist full encrypted credential bundle to Account table for multi-user token refresh
            from semabridge.repository.orm.models import Account
            from semabridge.repository.orm.session_factory import db_manager

            bundle = {
                "auth_type": "interactive",
                "host": host,
                "client_id": client_id,
                "warehouse_id": warehouse_id,
                "catalog": catalog,
                "schema_name": schema_name,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": str(int(expires_at)),
                "account_username": username,
            }
            session = db_manager.get_session_factory()()
            try:
                stmt = select(Account).where(
                    Account.connector_type == "DATABRICKS",
                    Account.identity_email == username,
                )
                if owner_id > 0:
                    stmt = stmt.where(Account.owner_id == owner_id)
                existing_acct = session.execute(stmt).scalar_one_or_none()
                enc_bundle = encrypt_token(json.dumps(bundle))
                enc_refresh = encrypt_token(refresh_token) if refresh_token else None
                exp_dt = datetime.fromtimestamp(expires_at, tz=timezone.utc)
                if existing_acct is not None:
                    existing_acct.encrypted_token = enc_bundle
                    existing_acct.refresh_token = enc_refresh
                    existing_acct.token_expires_at = exp_dt
                    existing_acct.auth_type = "interactive"
                    existing_acct.status = "Active"
                else:
                    session.add(
                        Account(
                            id=str(_uuid.uuid4()),
                            connector_type="DATABRICKS",
                            tag=f"DATABRICKS-{username}",
                            identity_email=username,
                            encrypted_token=enc_bundle,
                            refresh_token=enc_refresh,
                            token_expires_at=exp_dt,
                            auth_type="interactive",
                            status="Active",
                            is_default=False,
                            owner_id=owner_id if owner_id > 0 else None,
                        )
                    )
                session.commit()
            finally:
                session.close()

            reload_settings()
        except Exception as exc:
            logger.exception("Failed to persist Databricks OAuth token: %s", exc)
            raise InternalError("Database write failed while saving Databricks OAuth session.")

        return {
            "status": "success",
            "username": username,
            "tenant_id": "",
            "message": "Signed in successfully.",
        }

    with _poll_sessions_lock:
        _poll_sessions.pop(flow_id, None)
    return {"status": "failed", "message": state.get("message", "Unknown error")}


def databricks_auth_status(request: Request = None):
    """Get the current user's Databricks authentication status (DB-backed, no secrets)."""
    user_id = _resolve_request_user_id(request)
    try:
        creds, _ = resolve_effective_databricks_credentials(user_id=user_id)
        if not creds:
            return {"auth_method": "none", "logged_in": False, "token_valid": False}

        auth_type = str(creds.get("auth_type") or "pat").strip().lower()
        if auth_type == "pat" and creds.get("token"):
            return {"auth_method": "pat", "logged_in": True, "token_valid": True}
        if auth_type in ("service_principal", "m2m", "client_credentials") and creds.get("client_id") and creds.get("client_secret"):
            return {"auth_method": "service_principal", "logged_in": True, "token_valid": True}
        if auth_type in ("interactive", "oauth", "u2m") and creds.get("access_token"):
            expires_at = _parse_expires_at(creds.get("expires_at"))
            has_refresh = bool(creds.get("refresh_token"))
            not_expired = expires_at is None or time.time() < (expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS)
            return {
                "auth_method": "interactive",
                "logged_in": True,
                "token_valid": not_expired or has_refresh,
                "token_expired": not not_expired,
                "can_refresh": has_refresh,
                "username": creds.get("account_username", ""),
            }
        return {"auth_method": "none", "logged_in": False, "token_valid": False}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Error reading Databricks auth status for user %d: %s", user_id, exc)
        return {"auth_method": "none", "logged_in": False, "token_valid": False, "error": "Unable to read auth status"}


def databricks_logout(request: Request = None):
    """Clear the current user's stored Databricks credentials from the DB."""
    from semabridge.repository.credential_manager import CredentialManager

    user_id = _resolve_request_user_id(request)
    try:
        cm = CredentialManager()
        cm.delete_credentials("databricks", user_id=user_id)
        reload_settings()
        logger.info("Databricks session cleared for owner=%d", user_id)
        return {"status": "logged_out"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to clear Databricks session for owner=%d: %s", user_id, exc)
        raise InternalError("Failed to clear Databricks session.")


# ---------------------------------------------------------------------------
# Requirement 2: Structured Connection Test & Explicit Warehouse Start
# ---------------------------------------------------------------------------

def run_databricks_connection_test(
    user_id: int,
    payload: Optional[Dict[str, Any]] = None,
    *,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Run the 4-step Databricks connection diagnostic:

    (a) Authenticate (resolve/refresh token and validate against workspace)
    (b) Check SQL warehouse exists and report state (RUNNING/STOPPED/STARTING) — never auto-start
    (c) List Unity Catalog catalogs and schemas
    (d) Check target ``catalog.schema`` exists
    """
    creds, account_id = resolve_effective_databricks_credentials(user_id, payload, db=db)

    host = normalize_databricks_host(creds.get("host"))
    warehouse_id = str(creds.get("warehouse_id") or "").strip()
    target_catalog = str(creds.get("catalog") or "").strip()
    target_schema = str(creds.get("schema_name") or "").strip()

    steps: List[Dict[str, Any]] = []
    principal: Optional[str] = None
    warehouse_info: Dict[str, Any] = {
        "id": warehouse_id,
        "name": "",
        "state": "UNKNOWN",
        "can_start": False,
    }
    catalog_names: List[str] = []
    schemas_by_catalog: Dict[str, List[str]] = {}
    selected_schemas: List[str] = []

    if not host:
        steps.append({
            "id": "auth",
            "label": "Authentication",
            "status": "failed",
            "message": "Databricks workspace host is required.",
        })
        return {
            "status": "failed",
            "message": "Databricks workspace host is required.",
            "principal": None,
            "warehouse": warehouse_info,
            "catalogs": [],
            "schemas": [],
            "schemas_by_catalog": {},
            "steps": steps,
        }

    # --- Step (a): Authenticate ---
    try:
        bearer_token, _ = resolve_databricks_bearer_token(
            creds,
            user_id=user_id,
            account_id=account_id,
            db=db,
            persist=bool(account_id or creds.get("_from_credential_manager")),
        )
    except DatabricksAPIError as exc:
        steps.append({
            "id": "auth",
            "label": "Authentication",
            "status": "failed",
            "message": exc.message,
        })
        return {
            "status": "failed",
            "message": exc.message,
            "principal": None,
            "warehouse": warehouse_info,
            "catalogs": [],
            "schemas": [],
            "schemas_by_catalog": {},
            "steps": steps,
        }

    client = DatabricksRestClient(host, bearer_token)
    auth_step: Dict[str, Any] = {
        "id": "auth",
        "label": "Authentication",
        "status": "passed",
        "message": f"Authenticated to {host}.",
    }
    try:
        me = client.get_current_user()
        principal = me.get("userName") or me.get("displayName") or me.get("id")
        if principal:
            auth_step["message"] = f"Authenticated to {host} as {principal}."
    except DatabricksAPIError as exc:
        if exc.status_code == 401 or exc.category in ("auth", "tls", "network"):
            auth_step["status"] = "failed"
            auth_step["message"] = exc.message
            steps.append(auth_step)
            return {
                "status": "failed",
                "message": exc.message,
                "principal": None,
                "warehouse": warehouse_info,
                "catalogs": [],
                "schemas": [],
                "schemas_by_catalog": {},
                "steps": steps,
            }
        # Some workspaces restrict SCIM /Me (403/404) while allowing warehouse/UC APIs
        logger.debug("SCIM /Me returned %s; continuing test steps", exc.status_code)
    steps.append(auth_step)

    # --- Step (b): SQL Warehouse check ---
    warehouse_step: Dict[str, Any]
    if not warehouse_id:
        warehouse_step = {
            "id": "warehouse",
            "label": "SQL Warehouse",
            "status": "warning",
            "message": "No SQL warehouse ID configured.",
            "state": "UNCONFIGURED",
            "can_start": False,
        }
    else:
        try:
            wh = client.get_warehouse(warehouse_id)
            wh_name = str(wh.get("name") or warehouse_id)
            wh_state = str(wh.get("state") or "UNKNOWN").upper()
            can_start = wh_state in ("STOPPED", "DEGRADED")
            warehouse_info = {
                "id": warehouse_id,
                "name": wh_name,
                "state": wh_state,
                "can_start": can_start,
            }
            if wh_state == "RUNNING":
                warehouse_step = {
                    "id": "warehouse",
                    "label": "SQL Warehouse",
                    "status": "passed",
                    "message": f"SQL warehouse '{wh_name}' is RUNNING.",
                    "state": wh_state,
                    "can_start": False,
                }
            elif wh_state == "STARTING":
                warehouse_step = {
                    "id": "warehouse",
                    "label": "SQL Warehouse",
                    "status": "warning",
                    "message": f"SQL warehouse '{wh_name}' is STARTING. Queries will run once it is ready.",
                    "state": wh_state,
                    "can_start": False,
                }
            elif can_start:
                warehouse_step = {
                    "id": "warehouse",
                    "label": "SQL Warehouse",
                    "status": "warning",
                    "message": f"SQL warehouse '{wh_name}' is {wh_state}. Use 'Start warehouse' if you want to start it.",
                    "state": wh_state,
                    "can_start": True,
                }
            else:
                warehouse_step = {
                    "id": "warehouse",
                    "label": "SQL Warehouse",
                    "status": "warning",
                    "message": f"SQL warehouse '{wh_name}' is in state {wh_state}.",
                    "state": wh_state,
                    "can_start": False,
                }
        except DatabricksAPIError as exc:
            if exc.status_code == 401:
                auth_step["status"] = "failed"
                auth_step["message"] = exc.message
                return {
                    "status": "failed",
                    "message": exc.message,
                    "principal": principal,
                    "warehouse": warehouse_info,
                    "catalogs": [],
                    "schemas": [],
                    "schemas_by_catalog": {},
                    "steps": steps,
                }
            warehouse_step = {
                "id": "warehouse",
                "label": "SQL Warehouse",
                "status": "failed",
                "message": (
                    f"SQL warehouse '{warehouse_id}' not found."
                    if exc.status_code == 404
                    else exc.message
                ),
                "state": "NOT_FOUND" if exc.status_code == 404 else "ERROR",
                "can_start": False,
            }
    steps.append(warehouse_step)

    # --- Step (c): List Catalogs & Schemas ---
    catalogs_step: Dict[str, Any]
    try:
        raw_catalogs = client.list_all_catalogs()
        catalog_names = sorted(
            str(c.get("name")) for c in raw_catalogs if isinstance(c, dict) and c.get("name")
        )
        catalog_to_inspect = (
            target_catalog
            if target_catalog and target_catalog in catalog_names
            else (catalog_names[0] if catalog_names else "")
        )
        if catalog_to_inspect:
            try:
                raw_schemas = client.list_all_schemas(catalog_to_inspect)
                schema_list = sorted(
                    str(s.get("name")) for s in raw_schemas if isinstance(s, dict) and s.get("name")
                )
                schemas_by_catalog[catalog_to_inspect] = schema_list
                selected_schemas = schema_list
            except DatabricksAPIError as schema_exc:
                logger.warning("Could not list schemas for catalog '%s': %s", catalog_to_inspect, schema_exc.message)
                schemas_by_catalog[catalog_to_inspect] = []
                selected_schemas = []

        catalogs_step = {
            "id": "catalogs",
            "label": "Unity Catalog Metadata",
            "status": "passed",
            "message": (
                f"Found {len(catalog_names)} catalog(s)"
                + (
                    f" and {len(selected_schemas)} schema(s) in '{catalog_to_inspect}'."
                    if catalog_to_inspect
                    else "."
                )
            ),
        }
    except DatabricksAPIError as exc:
        if exc.status_code == 401:
            auth_step["status"] = "failed"
            auth_step["message"] = exc.message
        catalogs_step = {
            "id": "catalogs",
            "label": "Unity Catalog Metadata",
            "status": "failed",
            "message": exc.message,
        }
    steps.append(catalogs_step)

    # --- Step (d): Target catalog.schema check ---
    target_step: Dict[str, Any]
    if catalogs_step["status"] == "failed":
        target_step = {
            "id": "target",
            "label": "Target Catalog & Schema",
            "status": "skipped",
            "message": "Skipped because Unity Catalog metadata could not be listed.",
        }
    elif not target_catalog and not target_schema:
        target_step = {
            "id": "target",
            "label": "Target Catalog & Schema",
            "status": "skipped",
            "message": "Select a target catalog and schema from the dropdowns.",
        }
    elif target_catalog not in catalog_names:
        target_step = {
            "id": "target",
            "label": "Target Catalog & Schema",
            "status": "failed",
            "message": f"Target catalog '{target_catalog}' was not found ({len(catalog_names)} catalog(s) available).",
        }
    elif not target_schema:
        target_step = {
            "id": "target",
            "label": "Target Catalog & Schema",
            "status": "warning",
            "message": f"Catalog '{target_catalog}' exists; select a target schema.",
        }
    else:
        cat_schemas = schemas_by_catalog.get(target_catalog, [])
        if target_schema in cat_schemas:
            target_step = {
                "id": "target",
                "label": "Target Catalog & Schema",
                "status": "passed",
                "message": f"Target '{target_catalog}.{target_schema}' exists.",
            }
        else:
            target_step = {
                "id": "target",
                "label": "Target Catalog & Schema",
                "status": "failed",
                "message": (
                    f"Target schema '{target_catalog}.{target_schema}' was not found "
                    f"({len(cat_schemas)} schema(s) available in '{target_catalog}')."
                ),
            }
    steps.append(target_step)

    # Compute overall status
    step_statuses = [s["status"] for s in steps]
    if "failed" in step_statuses:
        overall_status = "failed"
        first_fail = next(s["message"] for s in steps if s["status"] == "failed")
        summary_msg = first_fail
    elif "warning" in step_statuses:
        overall_status = "warning"
        first_warn = next(s["message"] for s in steps if s["status"] == "warning")
        summary_msg = first_warn
    else:
        overall_status = "success"
        summary_msg = f"Databricks connection to {host} verified."

    return {
        "status": overall_status,
        "message": summary_msg,
        "principal": principal,
        "warehouse": warehouse_info,
        "catalogs": catalog_names,
        "schemas": selected_schemas,
        "schemas_by_catalog": schemas_by_catalog,
        "steps": steps,
    }


async def databricks_test_connection(request: Request, payload: Dict[str, Any] = None):
    """POST /api/connections/databricks/test — structured multi-step connection test."""
    user_id = _resolve_request_user_id(request)
    body = payload
    if body is None:
        try:
            raw = await request.body()
            body = json.loads(raw) if raw else {}
        except Exception as exc:
            logger.debug("Empty or non-JSON body on /api/connections/databricks/test: %s", exc)
            body = {}
    return run_databricks_connection_test(user_id=user_id, payload=body)


async def databricks_start_warehouse(request: Request, payload: Dict[str, Any] = None):
    """POST /api/connections/databricks/warehouse/start — explicitly start a stopped SQL warehouse."""
    user_id = _resolve_request_user_id(request)
    body = payload or {}
    creds, account_id = resolve_effective_databricks_credentials(user_id, body)
    host = normalize_databricks_host(creds.get("host"))
    warehouse_id = str(creds.get("warehouse_id") or "").strip()

    if not host or not warehouse_id:
        raise HTTPException(status_code=400, detail="Databricks host and warehouse_id are required.")

    try:
        token, _ = resolve_databricks_bearer_token(
            creds,
            user_id=user_id,
            account_id=account_id,
            persist=bool(account_id or creds.get("_from_credential_manager")),
        )
        client = DatabricksRestClient(host, token)
        client.start_warehouse(warehouse_id)
        return {
            "status": "starting",
            "warehouse_id": warehouse_id,
            "message": f"Start request sent for SQL warehouse '{warehouse_id}'.",
        }
    except DatabricksAPIError as exc:
        http_status = exc.status_code if exc.status_code in (400, 401, 403, 404, 429) else 502
        raise HTTPException(status_code=http_status, detail=exc.message) from exc


async def databricks_list_schemas(request: Request, payload: Dict[str, Any] = None):
    """POST /api/connections/databricks/schemas — list schemas for a catalog using saved or draft credentials."""
    user_id = _resolve_request_user_id(request)
    body = payload or {}
    catalog = str(body.get("catalog") or "").strip()
    if not catalog:
        raise HTTPException(status_code=400, detail="catalog is required.")

    creds, account_id = resolve_effective_databricks_credentials(user_id, body)
    host = normalize_databricks_host(creds.get("host"))
    if not host:
        raise HTTPException(status_code=400, detail="Databricks host is required.")

    try:
        token, _ = resolve_databricks_bearer_token(
            creds,
            user_id=user_id,
            account_id=account_id,
            persist=bool(account_id or creds.get("_from_credential_manager")),
        )
        client = DatabricksRestClient(host, token)
        raw_schemas = client.list_all_schemas(catalog)
        schema_names = sorted(
            str(s.get("name")) for s in raw_schemas if isinstance(s, dict) and s.get("name")
        )
        return {"catalog": catalog, "schemas": schema_names}
    except DatabricksAPIError as exc:
        http_status = exc.status_code if exc.status_code in (400, 401, 403, 404, 429) else 502
        raise HTTPException(status_code=http_status, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Requirement 3: Unity Catalog Effective Permissions Check
# ---------------------------------------------------------------------------

def _normalize_privilege(priv: str) -> str:
    return str(priv or "").strip().upper().replace("_", " ")


def _extract_privileges_from_effective_response(
    response: Dict[str, Any],
    principal: Optional[str] = None,
) -> Tuple[List[str], Optional[str]]:
    """Parse Unity Catalog ``effective-permissions`` response into normalized privilege names."""
    assignments = response.get("privilege_assignments")
    if not isinstance(assignments, list):
        return [], principal

    resolved_principal = principal
    matched_assignments = assignments
    if principal:
        lower_p = principal.strip().lower()
        exact = [
            a for a in assignments
            if isinstance(a, dict) and str(a.get("principal") or "").strip().lower() == lower_p
        ]
        if exact:
            matched_assignments = exact

    privileges: set[str] = set()
    for assignment in matched_assignments:
        if not isinstance(assignment, dict):
            continue
        if not resolved_principal and assignment.get("principal"):
            resolved_principal = str(assignment["principal"])
        for entry in assignment.get("privileges") or []:
            if isinstance(entry, dict) and entry.get("privilege"):
                privileges.add(_normalize_privilege(entry["privilege"]))
            elif isinstance(entry, str) and entry.strip():
                privileges.add(_normalize_privilege(entry))

    return sorted(privileges), resolved_principal


def _quote_uc_identifier(name: str) -> str:
    """Safely backtick-quote a dotted Unity Catalog securable name (catalog.schema.table)."""
    parts = [p.strip().strip("`") for p in str(name or "").split(".") if p.strip()]
    return ".".join(f"`{p.replace('`', '``')}`" for p in parts)


def _quote_uc_principal(principal: str) -> str:
    """Safely backtick-quote a Unity Catalog principal (email / group / SP) without splitting on dots."""
    cleaned = str(principal or "").strip().strip("`")
    return f"`{cleaned.replace('`', '``')}`"


def run_databricks_permissions_check(
    user_id: int,
    payload: Optional[Dict[str, Any]] = None,
    *,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Check Unity Catalog effective permissions for target catalog.schema and source tables."""
    body = dict(payload or {})
    creds, account_id = resolve_effective_databricks_credentials(user_id, body, db=db)

    host = normalize_databricks_host(creds.get("host"))
    catalog = str(creds.get("catalog") or "").strip()
    schema_name = str(creds.get("schema_name") or "").strip()
    source_tables = [
        str(t).strip()
        for t in (body.get("source_tables") or [])
        if str(t).strip()
    ]
    explicit_principal = str(body.get("principal") or "").strip() or None

    if not host:
        raise HTTPException(status_code=400, detail="Databricks host is required.")
    if not catalog or not schema_name:
        raise HTTPException(status_code=400, detail="Both catalog and schema_name are required.")

    try:
        token, _ = resolve_databricks_bearer_token(
            creds,
            user_id=user_id,
            account_id=account_id,
            db=db,
            persist=bool(account_id or creds.get("_from_credential_manager")),
        )
    except DatabricksAPIError as exc:
        http_status = exc.status_code if exc.status_code in (400, 401, 403) else 502
        raise HTTPException(status_code=http_status, detail=exc.message) from exc

    client = DatabricksRestClient(host, token)
    principal = explicit_principal
    if not principal:
        try:
            me = client.get_current_user()
            principal = me.get("userName") or me.get("displayName") or creds.get("client_id")
        except DatabricksAPIError as exc:
            if exc.status_code == 401:
                raise HTTPException(status_code=401, detail=exc.message) from exc
            principal = creds.get("client_id") or creds.get("account_username")

    principal_label = principal or "<principal>"
    principal_sql = _quote_uc_principal(principal_label) if principal else "`<principal>`"

    checks: List[Dict[str, Any]] = []
    warnings: List[str] = []
    missing_grants: List[str] = []
    recommended_grants: List[str] = []
    recommended_revokes: List[str] = []

    def _audit_securable(
        securable_type: str,
        full_name: str,
        required: List[str],
        excessive_set: frozenset[str],
    ) -> None:
        nonlocal principal, principal_label, principal_sql
        try:
            resp = client.get_effective_permissions(securable_type, full_name, principal=principal)
            granted, discovered_principal = _extract_privileges_from_effective_response(resp, principal)
            if not principal and discovered_principal:
                principal = discovered_principal
                principal_label = principal
                principal_sql = _quote_uc_principal(principal)
        except DatabricksAPIError as exc:
            if exc.status_code == 401:
                raise HTTPException(status_code=401, detail=exc.message) from exc
            checks.append({
                "securable_type": securable_type,
                "full_name": full_name,
                "required": required,
                "granted": [],
                "missing": list(required),
                "excessive": [],
                "passed": False,
                "error": exc.message,
            })
            quoted_obj = _quote_uc_identifier(full_name)
            for req in required:
                stmt = f"GRANT {req} ON {securable_type.upper()} {quoted_obj} TO {principal_sql};"
                missing_grants.append(stmt)
                recommended_grants.append(stmt)
            return

        granted_set = set(granted)
        has_all = "ALL PRIVILEGES" in granted_set
        missing = [r for r in required if not (r in granted_set or has_all)]
        excessive = sorted(p for p in granted_set if p in excessive_set)

        checks.append({
            "securable_type": securable_type,
            "full_name": full_name,
            "required": required,
            "granted": granted,
            "missing": missing,
            "excessive": excessive,
            "passed": len(missing) == 0,
        })

        quoted_obj = _quote_uc_identifier(full_name)
        for req in required:
            stmt = f"GRANT {req} ON {securable_type.upper()} {quoted_obj} TO {principal_sql};"
            recommended_grants.append(stmt)
            if req in missing:
                missing_grants.append(stmt)

        if excessive:
            warnings.append(
                f"Principal '{principal_label}' has more privileges than needed on "
                f"{securable_type} '{full_name}': {', '.join(excessive)}. "
                "Follow least-privilege access by revoking excessive grants."
            )
            for exc_priv in excessive:
                recommended_revokes.append(
                    f"REVOKE {exc_priv} ON {securable_type.upper()} {quoted_obj} FROM {principal_sql};"
                )

    # 1. Catalog: USE CATALOG
    _audit_securable("catalog", catalog, ["USE CATALOG"], _EXCESSIVE_CATALOG_PRIVILEGES)

    # 2. Target Schema: USE SCHEMA, CREATE VIEW
    _audit_securable(
        "schema",
        f"{catalog}.{schema_name}",
        ["USE SCHEMA", "CREATE VIEW"],
        _EXCESSIVE_SCHEMA_PRIVILEGES,
    )

    # 3. Source Tables: SELECT (and USE CATALOG/USE SCHEMA if in another catalog/schema)
    for tbl in source_tables:
        _audit_securable("table", tbl, ["SELECT"], _EXCESSIVE_TABLE_PRIVILEGES)

    has_missing = any(not c["passed"] for c in checks)
    has_warnings = len(warnings) > 0
    if has_missing:
        overall_status = "missing_permissions"
    elif has_warnings:
        overall_status = "warning"
    else:
        overall_status = "ok"

    rec_lines: List[str] = []
    if recommended_revokes:
        rec_lines.append("-- Revoke excessive privileges (recommended for least-privilege):")
        rec_lines.extend(recommended_revokes)
        rec_lines.append("")
    rec_lines.append("-- Minimum required GRANT statements:")
    rec_lines.extend(recommended_grants)
    recommendation_text = "\n".join(rec_lines)

    return {
        "status": overall_status,
        "principal": principal,
        "target": {"catalog": catalog, "schema_name": schema_name},
        "checks": checks,
        "warnings": warnings,
        "missing_grants": missing_grants,
        "recommended_grants": recommended_grants,
        "recommended_revokes": recommended_revokes,
        "recommendation_text": recommendation_text,
    }


async def databricks_check_permissions(request: Request, payload: Dict[str, Any] = None):
    """POST /api/connections/databricks/permissions — check Unity Catalog effective permissions."""
    user_id = _resolve_request_user_id(request)
    body = payload
    if body is None:
        try:
            raw = await request.body()
            body = json.loads(raw) if raw else {}
        except Exception as exc:
            logger.debug("Empty or non-JSON body on /api/connections/databricks/permissions: %s", exc)
            body = {}
    return run_databricks_permissions_check(user_id=user_id, payload=body)


# ---------------------------------------------------------------------------
# Requirement 6: Paginated Discovery (/api/discovery/databricks/sources)
# ---------------------------------------------------------------------------

def _encode_discovery_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def _decode_discovery_cursor(token: Optional[str]) -> int:
    if not token:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8"))
        offset = int(data.get("offset", 0))
        return max(0, offset)
    except Exception:
        return 0


async def discover_databricks_sources(
    request: Request,
    identity_id: Optional[str] = None,
    connection_id: Optional[str] = None,
    catalog: Optional[str] = None,
    schema_name: Optional[str] = None,
    schema: Optional[str] = None,
    page_size: int = 50,
    page_token: Optional[str] = None,
):
    """GET /api/discovery/databricks/sources — list catalogs, schemas, and tables (paginated)."""
    user_id = _resolve_request_user_id(request)
    effective_conn_id = connection_id or identity_id
    effective_schema = schema_name or schema
    clamped_page_size = max(1, min(int(page_size or 50), 500))

    creds, account_id = resolve_effective_databricks_credentials(
        user_id,
        {"connection_id": effective_conn_id} if effective_conn_id else {},
    )
    host = normalize_databricks_host(creds.get("host"))
    if not host:
        raise HTTPException(
            status_code=400,
            detail="Databricks connection is not configured. Configure host and credentials in Settings.",
        )

    try:
        token, _ = resolve_databricks_bearer_token(
            creds,
            user_id=user_id,
            account_id=account_id,
            persist=bool(account_id or creds.get("_from_credential_manager")),
        )
        client = DatabricksRestClient(host, token)

        # 1. Catalogs
        if catalog:
            catalog_names = [catalog.strip()]
        else:
            raw_catalogs = client.list_all_catalogs()
            catalog_names = sorted(
                str(c.get("name"))
                for c in raw_catalogs
                if isinstance(c, dict) and c.get("name") and str(c.get("name")) != "system"
            )

        # 2. Schemas & Tables (flatten with deterministic ordering, then paginate cleanly)
        flat_items: List[Dict[str, Any]] = []
        for cat in catalog_names:
            if effective_schema:
                schema_names = [effective_schema.strip()]
            else:
                raw_schemas = client.list_all_schemas(cat)
                schema_names = sorted(
                    str(s.get("name"))
                    for s in raw_schemas
                    if isinstance(s, dict) and s.get("name") and str(s.get("name")) != "information_schema"
                )
            for sch in schema_names:
                tbl_cursor: Optional[str] = None
                for _ in range(20):
                    raw_tables, tbl_cursor = client.list_tables(cat, sch, page_token=tbl_cursor)
                    for tbl in raw_tables:
                        if not isinstance(tbl, dict) or not tbl.get("name"):
                            continue
                        t_name = str(tbl["name"])
                        flat_items.append({
                            "catalog": cat,
                            "schema": sch,
                            "table": t_name,
                            "full_name": tbl.get("full_name") or f"{cat}.{sch}.{t_name}",
                            "table_type": tbl.get("table_type") or "TABLE",
                        })
                    if not tbl_cursor:
                        break

        offset = _decode_discovery_cursor(page_token)
        page_items = flat_items[offset : offset + clamped_page_size]
        has_more = (offset + clamped_page_size) < len(flat_items)
        next_token = _encode_discovery_cursor(offset + clamped_page_size) if has_more else None

        # Group page items into {catalog, schema, tables: [...]} for StepSourceBrowser compatibility
        grouped: Dict[Tuple[str, str], List[str]] = {}
        for item in page_items:
            grouped.setdefault((item["catalog"], item["schema"]), []).append(item["table"])
        sources = [
            {"catalog": cat, "schema": sch, "tables": tbls}
            for (cat, sch), tbls in grouped.items()
        ]

        return {
            "catalogs": catalog_names,
            "sources": sources,
            "schemas": sources,
            "items": page_items,
            "next_page_token": next_token,
            "has_more": has_more,
            "total_returned": len(page_items),
            "total_count": len(flat_items),
        }
    except DatabricksAPIError as exc:
        http_status = exc.status_code if exc.status_code in (400, 401, 403, 404, 429) else 502
        raise HTTPException(status_code=http_status, detail=exc.message) from exc
