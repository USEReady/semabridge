"""Job scheduling ORM models: SyncJob, SyncJobItem."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class SyncJob(Base):
    """Sync job lifecycle record.

    Mirrors the ``sync_jobs`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "sync_jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    direction: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    conflict_resolution: Mapped[str] = mapped_column(
        String(50), nullable=False, default="fail_and_approve"
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    initiated_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="cli")
    source_folder: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_connection: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_workspace_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    target_snowflake_schema: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    items: Mapped[List["SyncJobItem"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    conflicts: Mapped[List["SyncConflictRow"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    checkpoints: Mapped[List["SyncCheckpointRow"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<SyncJob(job_id={self.job_id!r}, direction={self.direction!r}, status={self.status!r})>"


class SyncJobItem(Base):
    """Individual model item within a sync job.

    Mirrors the ``sync_job_items`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "sync_job_items"

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("sync_jobs.job_id"), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    started_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    osi_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_artifact_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    job: Mapped["SyncJob"] = relationship(back_populates="items")

    def __repr__(self) -> str:
        return (
            f"<SyncJobItem(item_id={self.item_id!r}, "
            f"model_name={self.model_name!r}, status={self.status!r})>"
        )
