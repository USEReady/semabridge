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
        """Atomically write the access token to the default account row.

        Uses ORM object mutation instead of a bulk UPDATE to avoid DuckDB's
        boolean WHERE-clause evaluation bug that causes silent 0-row updates.

        Args:
            connector_type: e.g. "FABRIC"
            state: Poll state dict containing access_token, username, tenant_id.
            expires_at: Unix timestamp when the token expires.
        """
        # Store only the raw access_token so decrypt_token() returns a usable Bearer value.
        safe_token = encrypt_token(state["access_token"])

        # Fetch by connector_type only — avoids DuckDB boolean filter bug on is_default.
        account = self.session.execute(
            select(Account).where(
                Account.connector_type == connector_type
            )
        ).scalars().first()

        if account is not None:
            account.encrypted_token = safe_token
            account.identity_email = state.get("username", "unknown")
            account.status = "Active"
            self.session.commit()
            logger.info(
                "Token write succeeded for default %s account (%s)",
                connector_type,
                state.get("username", "unknown"),
            )
        else:
            # No existing row — create one (first-ever login for this connector).
            import uuid as _uuid
            new_account = Account(
                id=str(_uuid.uuid4()),
                connector_type=connector_type,
                tag=f"{connector_type.capitalize()}-Default",
                identity_email=state.get("username", "unknown"),
                encrypted_token=safe_token,
                status="Active",
                is_default=True,
            )
            self.session.add(new_account)
            self.session.commit()
            logger.info(
                "Created new default %s account for %s",
                connector_type,
                state.get("username", "unknown"),
            )

