"""Multi-PBIX background dry-run job ORM models: DryRunJob, DryRunJobFile.

Gives multi-file dry-run the same background-job-plus-polling shape real
deploys already use (see run_models.py's Run/SourceArtifact), so a batch of
N PBIX files can be dry-run in parallel without risking an HTTP timeout, and
so each file's status/result is independently pollable and independently
re-runnable without touching its siblings.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class DryRunJob(Base):
    """One multi-file dry-run request: 1 job, N DryRunJobFile children.

    ``status`` is the aggregate across all files: ``pending`` (created, not
    yet dispatched), ``running`` (at least one file still pending/running),
    ``success`` (every file succeeded), ``partial`` (a mix of success and
    failure — never hidden as an overall failure, per the "show all files
    and their individual outcomes" requirement), ``failed`` (every file
    failed).
    """

    __tablename__ = "dry_run_jobs"
    __table_args__ = (
        Index("ix_dry_run_jobs_project", "project_id"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # Deliberately NOT a ForeignKey to projects.project_id: dry-run jobs run
    # against transient "preview-*" projects that live in the compat
    # YAML/in-memory store (see mappings_controller.py's preview-project
    # bookkeeping), not only against real ORM-persisted projects. A hard FK
    # would reject every preview-project dry-run job.
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    # Snapshot of the request's source_config/target_config as submitted, so
    # a per-file rerun (see dry_run_job_service.rerun_dry_run_job_file) can
    # replay the exact same pipeline call for one file without the caller
    # needing to resend the whole original request.
    source_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    files: Mapped[List["DryRunJobFile"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="DryRunJobFile.file_index",
    )

    def __repr__(self) -> str:
        return f"<DryRunJob(job_id={self.job_id!r}, status={self.status!r})>"


class DryRunJobFile(Base):
    """One PBIX file's independent dry-run within a DryRunJob batch.

    Each row's ``status``/``error_message``/``result_json`` is written only
    by the one background task processing that specific file — re-running a
    single failed file (see dry_run_job_service.rerun_dry_run_job_file)
    resets and rewrites only its own row, never touching sibling rows.
    """

    __tablename__ = "dry_run_job_files"
    __table_args__ = (
        Index("ix_dry_run_job_files_job", "job_id"),
    )

    file_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("dry_run_jobs.job_id"), nullable=False)
    file_index: Mapped[int] = mapped_column(Integer, nullable=False)
    pbix_path: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The same JSON shape _run_dry_run_pipeline()/dry_run_mapping() has always
    # returned for a single file (entity_mappings, summary, dropped_entities,
    # ...), scoped to just this one file — this is what the drill-in view
    # (Part C) fetches per file.
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)

    job: Mapped["DryRunJob"] = relationship(back_populates="files")

    def __repr__(self) -> str:
        return (
            f"<DryRunJobFile(file_id={self.file_id!r}, display_name={self.display_name!r}, "
            f"status={self.status!r})>"
        )
