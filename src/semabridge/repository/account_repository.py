"""
Account Repository for SemaBridge.

Handles atomic token writes to the Account ORM table.
"""
from __future__ import annotations

from sqlalchemy.orm import Session
from sqlalchemy import update

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

        Encrypts the raw access_token (not a JSON blob) because list_workspaces
        decrypts and uses the value directly as a Bearer token.

        Args:
            connector_type: e.g. "FABRIC"
            state: Poll state dict containing access_token, username, tenant_id.
            expires_at: Unix timestamp when the token expires.

        Raises:
            RuntimeError: If no default account exists (rowcount == 0).
        """
        # Store only the raw access_token so decrypt_token() returns a usable Bearer value.
        safe_token = encrypt_token(state["access_token"])

        res = self.session.execute(
            update(Account)
            .where(
                Account.connector_type == connector_type,
                Account.is_default == True,  # noqa: E712
            )
            .values(
                encrypted_token=safe_token,
                identity_email=state.get("username", "unknown"),
                status="Active",
            )
        )

        if res.rowcount == 0:
            self.session.rollback()
            raise RuntimeError(
                "Strict Write failed: no active default FABRIC account found. "
                "Create an account identity before authenticating."
            )

        self.session.commit()
        logger.info(
            "Atomic token write succeeded for default %s account (%s)",
            connector_type,
            state.get("username", "unknown"),
        )

