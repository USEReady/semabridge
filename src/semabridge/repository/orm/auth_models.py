"""Auth/identity ORM models: User, UserCredential, RefreshToken, PasswordResetToken."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, true, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class User(Base):
    """Application user with authentication support.

    Attributes:
        id:            Auto-increment integer primary key.
        username:      Unique, non-nullable string (max 50 chars).
        email:         Unique, non-nullable string (max 255 chars).
        password_hash: bcrypt hash of the user's password.
        role:          User role — ``admin`` or ``viewer`` (default).
        is_active:     Soft-delete flag (default True).
        created_at:    Server-default creation timestamp.
        updated_at:    Auto-updated modification timestamp.
        posts:         One-to-many relationship to :class:`Post`.
        credentials:   One-to-many relationship to :class:`UserCredential`.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, onupdate=func.now(), nullable=True,
    )

    # Relationships ----------------------------------------------------------
    posts: Mapped[List["Post"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    credentials: Mapped[List["UserCredential"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    accounts: Mapped[List["Account"]] = relationship(
        back_populates="owner",
        lazy="selectin",
    )
    password_reset_tokens: Mapped[List["PasswordResetToken"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r}, role={self.role!r})>"


class UserCredential(Base):
    """Per-user, per-service credential storage.

    Each row stores a single credential key/value pair scoped to
    a user and a service (e.g. ``fabric``, ``snowflake``).

    Attributes:
        id:         Auto-increment integer primary key.
        user_id:    FK → ``users.id``.
        service:    Service name (``fabric``, ``snowflake``, etc.).
        key:        Credential key (e.g. ``tenant_id``, ``password``).
        value:      Credential value (stored as text; encrypt at rest
                    via OS-level DB file permissions).
        created_at: Server-default creation timestamp.
        updated_at: Auto-updated modification timestamp.
    """

    __tablename__ = "user_credentials"
    __table_args__ = (
        Index("ix_usercred_user_service", "user_id", "service"),
        Index("ix_usercred_user_service_key", "user_id", "service", "key", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    service: Mapped[str] = mapped_column(String(50), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, onupdate=func.now(), nullable=True,
    )

    # Relationships ----------------------------------------------------------
    user: Mapped["User"] = relationship(back_populates="credentials")

    def __repr__(self) -> str:
        return (
            f"<UserCredential(id={self.id}, user_id={self.user_id}, "
            f"service={self.service!r}, key={self.key!r})>"
        )


class RefreshToken(Base):
    """JWT refresh token for session management.

    Supports one-time-use rotation: each refresh token can only be
    exchanged once for a new access + refresh pair. Reuse of an
    already-consumed token triggers revocation of the entire family.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_token_hash", "token_hash", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return f"<RefreshToken(id={self.id}, user_id={self.user_id}, revoked={self.is_revoked})>"


class PasswordResetToken(Base):
    """One-time password reset token.

    Tokens are hashed before storage (SHA-256) and expire after a
    configurable window (default 60 minutes).  Each token can only be
    used once (``used`` flag).  Requesting a new reset invalidates all
    previous outstanding tokens for the same user.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_token_hash", "token_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False  # DuckDB does not support ON DELETE CASCADE
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    used: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False,
    )

    # Relationships ----------------------------------------------------------
    user: Mapped["User"] = relationship(back_populates="password_reset_tokens")

    def __repr__(self) -> str:
        return (
            f"<PasswordResetToken(id={self.id}, user_id={self.user_id}, used={self.used})>"
        )
