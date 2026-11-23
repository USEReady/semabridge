"""Databricks connection details and access tokens for the planner path.

``DatabricksConnectionInfo`` carries what the client needs. Unlike
``DatabricksConfig`` it does not require a warehouse ID, so a missing one can
be auto-discovered (CS-10). It is built from a ``DatabricksConfig`` (e.g. one
made by ``auth.credential_builder.build_databricks_config`` for a saved
account) or from the ``DATABRICKS_*`` environment.

``TokenProvider`` returns a bearer token:
  * pat               -- the personal access token;
  * service_principal -- OAuth M2M: client-credentials exchange at the
                         workspace ``/oidc/v1/token`` endpoint, cached until
                         60 s before expiry;
  * interactive       -- the token saved by the browser sign-in in Settings.

The existing DatabricksPublisher keeps its own copy of this logic and is not
touched; it is being replaced by the planner path.
Secrets never appear in logs, errors or reprs.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from semabridge.target_backends.databricks.http import HttpTransport, RequestsTransport


class DatabricksAuthError(RuntimeError):
    """No usable credentials, or the token exchange failed."""


def _secret(value: Any) -> Optional[str]:
    if value is None:
        return None
    getter = getattr(value, "get_secret_value", None)
    text = getter() if callable(getter) else str(value)
    return text or None


def normalize_host(host: str) -> str:
    """dbc-1.cloud.databricks.com from any of https://dbc-1..../, dbc-1..."""
    value = str(host or "").strip()
    for prefix in ("https://", "http://"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    return value.rstrip("/")


@dataclass(frozen=True)
class DatabricksConnectionInfo:
    host: str
    auth_type: str = "pat"
    token: Optional[str] = field(default=None, repr=False)
    client_id: Optional[str] = None
    client_secret: Optional[str] = field(default=None, repr=False)
    warehouse_id: Optional[str] = None
    catalog: Optional[str] = None
    schema: Optional[str] = None

    def __post_init__(self) -> None:
        host = normalize_host(self.host)
        if not host or host.startswith("your-"):
            raise DatabricksAuthError("Databricks host is not set")
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "auth_type", (self.auth_type or "pat").strip().lower())

    @property
    def base_url(self) -> str:
        return f"https://{self.host}"

    @classmethod
    def from_config(cls, config: Any, *, catalog: Optional[str] = None, schema: Optional[str] = None) -> "DatabricksConnectionInfo":
        """From a DatabricksConfig (global settings or a saved account)."""
        return cls(
            host=getattr(config, "host", "") or "",
            auth_type=getattr(config, "auth_type", "pat") or "pat",
            token=_secret(getattr(config, "token", None)),
            client_id=getattr(config, "client_id", None),
            client_secret=_secret(getattr(config, "client_secret", None)),
            warehouse_id=getattr(config, "warehouse_id", None) or None,
            catalog=catalog or getattr(config, "catalog", None),
            schema=schema or getattr(config, "schema_name", None),
        )

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "DatabricksConnectionInfo":
        """From DATABRICKS_* variables; the warehouse ID may be missing."""
        e = os.environ if env is None else env
        return cls(
            host=e.get("DATABRICKS_HOST", ""),
            auth_type=e.get("DATABRICKS_AUTH_TYPE", "pat") or "pat",
            token=e.get("DATABRICKS_TOKEN") or e.get("DATABRICKS_ACCESS_TOKEN") or None,
            client_id=e.get("DATABRICKS_CLIENT_ID") or None,
            client_secret=e.get("DATABRICKS_CLIENT_SECRET") or None,
            warehouse_id=e.get("DATABRICKS_WAREHOUSE_ID") or None,
            catalog=e.get("DATABRICKS_CATALOG") or None,
            schema=e.get("DATABRICKS_SCHEMA") or None,
        )


class TokenProvider:
    """Bearer tokens for one connection (thread-safe, OAuth tokens cached)."""

    _oauth_cache: Dict[str, Dict[str, Any]] = {}
    _lock = threading.Lock()

    def __init__(self, info: DatabricksConnectionInfo, transport: Optional[HttpTransport] = None,
                 clock=time.time) -> None:
        self.info = info
        self.transport = transport or RequestsTransport()
        self._clock = clock

    def token(self) -> str:
        auth = self.info.auth_type
        if auth == "service_principal":
            return self._oauth_m2m()
        if auth == "interactive":
            if self.info.token:
                return self.info.token
            return self._interactive_saved_token()
        if not self.info.token:
            raise DatabricksAuthError(
                "No Databricks personal access token configured (DATABRICKS_TOKEN or the saved account)."
            )
        return self.info.token

    def _oauth_m2m(self) -> str:
        if not self.info.client_id or not self.info.client_secret:
            raise DatabricksAuthError("Service principal auth needs a client ID and client secret.")
        key = f"{self.info.host}:{self.info.client_id}"
        with self._lock:
            cached = self._oauth_cache.get(key)
            if cached and self._clock() < cached["expires_at"]:
                return cached["token"]
        response = self.transport.request(
            "POST",
            f"{self.info.base_url}/oidc/v1/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.info.client_id,
                "client_secret": self.info.client_secret,
                "scope": "all-apis",
            },
            timeout=15,
        )
        if response.status != 200 or not isinstance(response.body, dict) or not response.body.get("access_token"):
            raise DatabricksAuthError(f"OAuth token exchange failed (HTTP {response.status}).")
        token = str(response.body["access_token"])
        expires_in = int(response.body.get("expires_in") or 3600)
        with self._lock:
            self._oauth_cache[key] = {"token": token, "expires_at": self._clock() + max(expires_in - 60, 0)}
        return token

    @staticmethod
    def _interactive_saved_token() -> str:
        from semabridge.repository.credential_manager import CredentialManager

        manager = CredentialManager()
        if manager.is_databricks_token_expired() and not manager.refresh_databricks_token():
            raise DatabricksAuthError("Databricks sign-in expired. Sign in again in Settings > Connections.")
        data = manager.get_databricks_token() or {}
        token = data.get("access_token")
        if not token:
            raise DatabricksAuthError("No Databricks sign-in found. Sign in in Settings > Connections.")
        return str(token)
