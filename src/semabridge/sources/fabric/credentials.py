"""One Fabric credential resolver for every stage (extraction, measure sync, DAX).

Order (unchanged from the extraction stage, which was the correct one):
  1. the project's source identity (``source.identity_id``)  -> identity token
  2. the run's linked-account token (``RunContext.fabric_access_token``)
  3. the interactive login stored in the credential store
  4. none of the above -> the FabricExtractor's own configured auth
     (service principal / FABRIC_ACCESS_TOKEN), origin "extractor_default"

A configured source that cannot be resolved raises SourceAuthError with the
reason; it never silently falls through to a different tenant's credentials.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from semabridge.sources.base import SourceAuthError, SourceCredentials
from semabridge.sources.tokens import jwt_expiry

logger = logging.getLogger(__name__)

# Same conservative in-memory TTL FabricExtractor applies to request-provided
# tokens when their expiry is unknown (re-checks before a real expiry).
_UNKNOWN_EXPIRY_TTL_SECONDS = 300


def _expiry(token: str) -> float:
    try:
        return jwt_expiry(token)
    except ValueError as exc:
        logger.debug("Fabric token expiry unreadable (%s); refreshing after %ss", exc, _UNKNOWN_EXPIRY_TTL_SECONDS)
        return time.time() + _UNKNOWN_EXPIRY_TTL_SECONDS


def resolve_fabric_credentials(
    *,
    identity_id: Optional[str] = None,
    account_token: Optional[str] = None,
    account_id: Optional[str] = None,
    credential_manager: Any = None,
) -> SourceCredentials:
    identity_id = str(identity_id or "").strip() or None
    if identity_id:
        from semabridge.auth.token_resolver import resolve_fabric_access_token

        try:
            token = resolve_fabric_access_token(None, identity_id)
        except Exception as exc:  # noqa: BLE001 - re-raised with reason
            raise SourceAuthError(
                f"Could not resolve Fabric token for identity '{identity_id}'. "
                f"Please re-authenticate this account in the Connections panel. ({exc})"
            ) from exc
        if not token:
            raise SourceAuthError(
                f"Fabric identity '{identity_id}' returned no access token. "
                f"Please re-authenticate this account in the Connections panel."
            )
        return SourceCredentials("identity", token, _expiry(token), identity_id=identity_id)

    if account_token:
        return SourceCredentials("account", account_token, _expiry(account_token), account_id=account_id)

    try:
        if credential_manager is None:
            from semabridge.repository.credential_manager import CredentialManager

            credential_manager = CredentialManager()
        use_interactive = (
            credential_manager.get_fabric_auth_method() == "interactive" and credential_manager.has_valid_token()
        )
        token_data = credential_manager.get_msal_token() if use_interactive else None
    except Exception as exc:  # noqa: BLE001 - re-raised with reason
        raise SourceAuthError(f"Fabric credential store could not be read: {exc}") from exc

    token = (token_data or {}).get("access_token") if use_interactive else None
    if token:
        return SourceCredentials("interactive", token, _expiry(token))
    return SourceCredentials("extractor_default")


def credentials_for_context(context: Any) -> SourceCredentials:
    """Resolve for an engine run: the project's identity, else the run's account token."""
    source_cfg = getattr(getattr(context, "config", None), "source", None)
    return resolve_fabric_credentials(
        identity_id=getattr(source_cfg, "identity_id", None),
        account_token=getattr(context, "fabric_access_token", None),
        account_id=getattr(context, "account_id", None),
    )


def apply_to_extractor(extractor: Any, creds: SourceCredentials) -> Any:
    """Inject a resolved delegated token into a FabricExtractor (no-op for extractor_default)."""
    if creds.access_token:
        extractor._access_token = creds.access_token
        extractor._token_expires_at = creds.expires_at or (time.time() + _UNKNOWN_EXPIRY_TTL_SECONDS)
    return extractor
