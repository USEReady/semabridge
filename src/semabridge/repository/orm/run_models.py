"""Execution run ORM models: Run, SourceArtifact, SyncConflictRow, SyncCheckpointRow."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class Run(Base):
    """Execution run lifecycle record.

    Mirrors the ``runs`` table created by DuckDBManager DDL.
    """

    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_project", "project_id"),
    )

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    final_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    target_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    run_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sync_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="copy")
    before_src_snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    restored_from_snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    before_target_snapshot_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_target_snapshot_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="runs")
    source_artifacts: Mapped[List["SourceArtifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Run(run_id={self.run_id!r}, status={self.status!r})>"


class SourceArtifact(Base):
    """Raw source-format blob per run.

    Mirrors the ``source_artifacts`` table created by DuckDBManager DDL.
    """

    __tablename__ = "source_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)

    # Relationships
    run: Mapped["Run"] = relationship(back_populates="source_artifacts")

    def __repr__(self) -> str:
        return (
            f"<SourceArtifact(artifact_id={self.artifact_id!r}, "
            f"source_type={self.source_type!r})>"
        )


class SyncConflictRow(Base):
    """Conflict detection and resolution log.

    Mirrors the ``sync_conflicts`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "sync_conflicts"

    conflict_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sync_jobs.job_id"), nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.run_id"), nullable=True)
    item_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    change_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    escalated: Mapped[bool] = mapped_column(default=False, nullable=False, server_default="false")
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )

    # Relationships
    job: Mapped["SyncJob"] = relationship(back_populates="conflicts")

    def __repr__(self) -> str:
        return (
            f"<SyncConflictRow(conflict_id={self.conflict_id!r}, "
            f"model_name={self.model_name!r}, severity={self.severity!r})>"
        )


class SyncCheckpointRow(Base):
    """Resumable progress marker.

    Mirrors the ``sync_checkpoints`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "sync_checkpoints"

    checkpoint_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("sync_jobs.job_id"), nullable=False)
    last_processed_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    last_processed_index: Mapped[int] = mapped_column(Integer, nullable=False)
    state_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="{}")
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )

    # Relationships
    job: Mapped["SyncJob"] = relationship(back_populates="checkpoints")

    def __repr__(self) -> str:
        return (
            f"<SyncCheckpointRow(checkpoint_id={self.checkpoint_id!r}, "
            f"job_id={self.job_id!r})>"
        )
