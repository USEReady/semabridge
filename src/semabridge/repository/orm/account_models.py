"""External account ORM models: Account, Post."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class Account(Base):
    """Account definition for multi-session connections/vault.
    Maps a user identity to a connector via OAuth tokens or credentials.

    The ``refresh_token`` and ``token_expires_at`` columns enable automatic
    token renewal for scheduled / headless pipeline runs without requiring
    the user to re-authenticate interactively.
    """

    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("owner_id", "tag", name="uq_account_owner_tag"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)  # FABRIC, SNOWFLAKE, DATABRICKS
    tag: Mapped[str] = mapped_column(String(255), nullable=False)
    identity_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    encrypted_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    auth_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True,
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="Active")
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Relationships
    owner: Mapped[Optional["User"]] = relationship(back_populates="accounts")
    projects: Mapped[List["Project"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Account(id={self.id!r}, tag={self.tag!r}, connector_type={self.connector_type!r})>"


class Post(Base):
    """Content item owned by a User.

    Attributes:
        id:      Auto-increment integer primary key.
        title:   Non-nullable string (max 200 chars).
        user_id: Foreign key referencing ``users.id``.
        user:    Many-to-one relationship back to :class:`User`.
    """

    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Relationship -----------------------------------------------------------
    user: Mapped["User"] = relationship(back_populates="posts")

    def __repr__(self) -> str:
        return f"<Post(id={self.id}, title={self.title!r}, user_id={self.user_id})>"
