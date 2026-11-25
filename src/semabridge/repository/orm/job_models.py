"""Durable work queue: one row per unit of background work (a project run).

The API enqueues; worker threads/processes claim a row atomically, beat a
heartbeat while executing, and mark it done/failed. A claimed row whose
heartbeat stops (worker crashed or was redeployed) is re-queued, up to
``max_attempts``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.compat import UTC_DATETIME


class RunJob(Base):
    __tablename__ = "run_jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    # queued | claimed | done | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTC_DATETIME, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTC_DATETIME, nullable=True)

    __table_args__ = (
        Index("ix_run_jobs_status_created", "status", "created_at"),
        Index("ix_run_jobs_project", "project_id"),
    )
