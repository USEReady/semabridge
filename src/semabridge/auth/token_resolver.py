"""
auth.token_resolver
===================

Public helper for resolving a live Fabric access token from various sources
(DB identity, request bearer token, environment variable).

Extracted from ``api.services.connection_domain_service`` so that
``core.engine.auth`` (and any other core module) can call it without
creating a reverse dependency on the API layer.
"""

from __future__ import annotations

import logging
from typing import Optional

from semabridge.auth.fabric_validator import fabric_validator
from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.domain.exceptions import AuthenticationError, SemaBridgeError

logger = logging.getLogger(__name__)


def resolve_fabric_access_token(
    header_bearer_token: Optional[str],
    identity_id: Optional[str] = None,
) -> str:
    """Resolve Fabric access token with compatibility-safe precedence.

    Precedence:
    1. Explicit identity-scoped account token, if ``identity_id`` is supplied.
    2. Authorization header bearer token from current request.
    3. Default stored MSAL token from interactive Connections login flow.
       - If expired, silently refresh using the stored refresh_token.
    4. FABRIC_ACCESS_TOKEN environment variable from the process or .env file.

    Explicit ``identity_id`` selection wins over any ambient bearer token so
    the UI-selected account is always honored.
    """
    # ── Phase 0: Header token (no DB needed) ─────────────────────────────
    if identity_id:
        header_bearer_token = None

    if header_bearer_token:
        try:
            fabric_validator.validate_msal_token(header_bearer_token)
            logger.info("Using and validated Fabric token from Authorization header")
            return header_bearer_token
        except SemaBridgeError:
            raise
        except Exception as e:
            logger.error(f"Fabric token validation error: {e}")
            raise AuthenticationError(str({"status": "invalid_token", "message": "Signature or claim validation failed."}))

    if not identity_id:
        env_token = get_fabric_access_token_from_env()
        if env_token:
            logger.info("Using temporary Fabric access token from .env / environment")
            return env_token

    # ── Phase 1: Read everything we need from DB in ONE session ──────────
    # Variables populated by Phase 1:
    encrypted_token: Optional[str] = None
    account_tag: Optional[str] = None
    matched_account_id: Optional[str] = None
    account_auth_type: Optional[str] = None
    has_account_row: bool = False

    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Account
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            session.expire_all()

            if identity_id:
                default_account = session.execute(
                    select(Account).where(
                        Account.connector_type == "FABRIC",
                        Account.id == identity_id,
                    )
                ).scalars().first()
            else:
                fabric_accounts = session.execute(
                    select(Account).where(Account.connector_type == "FABRIC")
                ).scalars().all()
                default_account = next((account for account in fabric_accounts if account.is_default), None)
                if not default_account and fabric_accounts:
                    default_account = fabric_accounts[0]

            if default_account:
                has_account_row = True
                account_tag = default_account.tag
                matched_account_id = default_account.id
                encrypted_token = default_account.encrypted_token
                account_auth_type = default_account.auth_type
        # ── Session is now CLOSED — connection returned to pool ──────────

    except SemaBridgeError:
        raise
    except Exception as exc:
        logger.warning(f"Failed to query default account token in DB: {exc}")

    # ── Phase 2: Validate / refresh tokens (no session held) ─────────────
    if has_account_row:
        logger.debug(f"Attempting Fabric token resolution for: {account_tag}")

        if not encrypted_token:
            raise AuthenticationError(str({"error": "reauth_required"}))

        # Decrypted token from Account row
        try:
            import json
            from semabridge.auth.encryption import decrypt_token
            tok_raw = decrypt_token(encrypted_token)

            is_json_payload = False
            access_token = tok_raw
            refresh_token = None
            tenant_id = "organizations"
            payload_dict = {}

            try:
                payload_dict = json.loads(tok_raw)
                if isinstance(payload_dict, dict):
                    is_json_payload = True
                    access_token = payload_dict.get("access_token", tok_raw)
                    refresh_token = payload_dict.get("refresh_token")
                    tenant_id = payload_dict.get("tenant_id", "organizations")
            except json.JSONDecodeError:
                pass

            # Service-principal authentication
            if (
                account_auth_type == "service_principal"
                or (is_json_payload and payload_dict.get("auth_type") == "service_principal")
                or (is_json_payload and "client_secret" in payload_dict and "client_id" in payload_dict)
            ):
                tenant_id = payload_dict.get("tenant_id") or "organizations"
                client_id = payload_dict.get("client_id")
                client_secret = payload_dict.get("client_secret")
                if not client_id or not client_secret:
                    raise AuthenticationError(
                        str({"error": "invalid_credentials", "message": "Service principal client_id and client_secret required."})
                    )
                from msal import ConfidentialClientApplication

                app = ConfidentialClientApplication(
                    client_id=client_id,
                    client_credential=client_secret,
                    authority=f"https://login.microsoftonline.com/{tenant_id}",
                )
                result = app.acquire_token_for_client(scopes=["https://api.fabric.microsoft.com/.default"])
                if "access_token" in result:
                    logger.info(f"Using service-principal access token for Fabric account: {account_tag}")
                    return result["access_token"]
                error = result.get("error_description", result.get("error", "Failed to acquire service principal token"))
                raise AuthenticationError(str({"error": "auth_failed", "message": error}))

            try:
                fabric_validator.validate_msal_token(access_token)
            except SemaBridgeError:
                logger.warning("Decrypted Fabric token from DB is expired.")
                if is_json_payload and refresh_token and matched_account_id:
                    logger.info(f"Attempting isolated silent refresh for account {account_tag}...")
                    from semabridge.api.services.connection_domain_service import _refresh_account_token
                    refreshed_access_token = _refresh_account_token(
                        matched_account_id, account_tag, refresh_token, tenant_id, payload_dict
                    )
                    if refreshed_access_token:
                        return refreshed_access_token

                logger.warning("Silent refresh failed. Forcing reauthentication.")
                raise AuthenticationError(str({"error": "reauth_required"}))

            logger.info(f"Using access token from default Fabric account: {account_tag}")
            return access_token
        except SemaBridgeError:
            raise
        except Exception as e:
            logger.warning(f"Failed to decrypt token for account {account_tag}: {e}")
            raise AuthenticationError(str({"error": "reauth_required"})) from e

    logger.warning("No valid Fabric access token available")
    raise AuthenticationError(str({"error": "reauth_required"}))
