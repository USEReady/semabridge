"""Shared application state: one row per entity, the single source of truth.

Replaces the per-process compat dicts + ``.semabridge_compat_store.json`` as
the authority for mappings, schedules, folders, snapshots lists, run lists
and run detail blobs. Every server process writes the rows it changed and
reads the rows other processes changed (``updated_at``), so several
processes (or a restarted one) see the same state.

A deleted entity keeps its row as a tombstone (``deleted=True``) so other
processes learn about the delete on their next refresh.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.compat import UTC_DATETIME


class StateDocument(Base):
    __tablename__ = "state_documents"

    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    digest: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    __table_args__ = (Index("ix_state_documents_updated_at", "updated_at"),)
