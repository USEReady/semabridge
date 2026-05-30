"""Infrastructure ORM models: Credential, CommandLog."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func, false
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.repository.schema_compat import PROJECT_ID_LENGTH

_UTC_DT = DateTime(timezone=True)


class Credential(Base):
    """Service credential storage — user-scoped via composite PK.

    Replaces the ``semabridge_credentials`` table created by
    CredentialManager DDL.

    Primary Key: ``(owner_id, service, key)``

    Scoping:
        - ``owner_id = 0``          → global / system row used by the CLI,
                                       background sync, and startup injection.
                                       This is the sentinel value (no matching
                                       ``users`` row required — FK is not
                                       enforced for the sentinel).
        - ``owner_id = <user.id>``  → user-scoped row.  Takes precedence over
                                       the sentinel row when both exist for the
                                       same ``(service, key)`` pair.

    Lookup precedence (inside CredentialManager):
        1. User-scoped row  (owner_id == user_id, user_id > 0)
        2. Global row       (owner_id == 0)
        3. os.environ       (populated from .env at startup)

    The leading ``owner_id`` column in the composite PK physically co-locates
    each user's credentials on disk, making per-user range scans an index seek
    rather than a full scan.  This matches the industry-standard multi-tenant
    composite-key pattern (HashiCorp Vault, Stripe internal KV store).
    """

    __tablename__ = "semabridge_credentials"
    __table_args__ = (
        # Composite index on (owner_id, service) enables efficient
        # "give me all credentials for user X and service Y" queries.
        Index("ix_credential_owner_service", "owner_id", "service"),
    )

    # owner_id=0 is the sentinel for system/global credentials.
    # User rows use the actual users.id value (always > 0).
    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    service: Mapped[str] = mapped_column(String(50), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Credential(owner_id={self.owner_id}, service={self.service!r}, key={self.key!r})>"


class CommandLog(Base):
    """CLI command audit trail.

    Replaces the ``command_log`` table created by CommandLogger DDL.
    ``details`` is stored as ``Text`` (JSON-serialised).
    """

    __tablename__ = "command_log"
    __table_args__ = (
        Index("ix_command_log_started", "started_at"),
    )

    log_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    command: Mapped[str] = mapped_column(String(50), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    adapter: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(PROJECT_ID_LENGTH), nullable=True)
    initiated_by: Mapped[str] = mapped_column(
        String(20), nullable=False, default="cli"
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<CommandLog(log_id={self.log_id!r}, command={self.command!r}, "
            f"status={self.status!r})>"
        )
