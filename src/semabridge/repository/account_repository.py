"""
Account Repository for SemaBridge.

Handles atomic token writes to the Account ORM table.
"""
from __future__ import annotations

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

    def upsert_account(self, connector_type: str, state: dict, expires_at: float) -> None:
        """Persist the latest access token for a connector when the account is unambiguous."""
        # Store only the raw access_token so decrypt_token() returns a usable Bearer value.
        safe_token = encrypt_token(state["access_token"])

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
        account.identity_email = state.get("username", "unknown")
        account.status = "Active"
        self.session.commit()
        logger.info(
            "Token write succeeded for %s account (%s)",
            connector_type,
            state.get("username", "unknown"),
        )

