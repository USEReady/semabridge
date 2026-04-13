"""
Account Repository for SemaBridge.

Handles atomic token writes to the Account ORM table.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import select

from semabridge.repository.orm.models import Account
from semabridge.auth.encryption import encrypt_token
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class AccountRepository:
    """Handles atomic token persistence for Account rows.

    Args:
        session: An active SQLAlchemy Session. Caller is responsible for closing it.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_account(
        self,
        connector_type: str,
        state: dict,
        expires_at: float,
        *,
        account_id: Optional[str] = None,
    ) -> None:
        """Persist the latest access token for a connector.

        When ``account_id`` is given, only that specific account row is updated.
        Otherwise, the legacy behaviour of updating the *sole* account of the
        given ``connector_type`` is preserved (ambiguity guard skips the write
        if more than one account exists).

        Args:
            connector_type: E.g. ``"FABRIC"``, ``"SNOWFLAKE"``, ``"DATABRICKS"``.
            state: Token payload dict with keys like ``access_token``,
                ``refresh_token``, ``username``, ``tenant_id``, ``auth_type``.
            expires_at: Unix epoch timestamp when the access token expires.
            account_id: Explicit account ID to target. If omitted, the repo
                uses the legacy "single-account" lookup.
        """
        # Store only the raw access_token so decrypt_token() returns a usable Bearer value.
        safe_token = encrypt_token(state["access_token"])
        safe_refresh = (
            encrypt_token(state["refresh_token"])
            if state.get("refresh_token")
            else None
        )

        if account_id:
            account = self.session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()
            if not account:
                logger.warning(
                    "Account %s not found — cannot persist token", account_id
                )
                return
        else:
            accounts = self.session.execute(
                select(Account).where(Account.connector_type == connector_type)
            ).scalars().all()

            if len(accounts) != 1:
                logger.info(
                    "Skipping ambiguous token write for %s (%s accounts present)",
                    connector_type,
                    len(accounts),
                )
                return

            account = accounts[0]

        account.encrypted_token = safe_token
        account.refresh_token = safe_refresh
        account.identity_email = state.get("username", "unknown")
        account.status = "Active"
        account.auth_type = state.get("auth_type")

        # Convert Unix epoch to timezone-aware datetime for the ORM column.
        if expires_at:
            account.token_expires_at = datetime.fromtimestamp(
                expires_at, tz=timezone.utc
            )

        self.session.commit()
        logger.info(
            "Token write succeeded for %s account (%s), expires_at=%s",
            connector_type,
            state.get("username", "unknown"),
            account.token_expires_at,
        )

    def get_account_by_id(self, account_id: str) -> Optional[Account]:
        """Retrieve an Account row by its primary key."""
        return self.session.execute(
            select(Account).where(Account.id == account_id)
        ).scalar_one_or_none()

    def get_default_account(self, connector_type: str) -> Optional[Account]:
        """Retrieve the default Account for a connector type.

        Falls back to the first (or only) account if none is marked as default.
        """
        default = self.session.execute(
            select(Account).where(
                Account.connector_type == connector_type.upper(),
                Account.is_default == True,  # noqa: E712
            )
        ).scalar_one_or_none()

        if default:
            return default

        # Fallback: single-account scenario
        accounts = self.session.execute(
            select(Account).where(
                Account.connector_type == connector_type.upper()
            )
        ).scalars().all()

        return accounts[0] if len(accounts) == 1 else None
