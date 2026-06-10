"""Version snapshot ORM models: SnapshotRow, Change, ModelVersion, ModelVersionHistory, SchemaVersionRow."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class SnapshotRow(Base):
    """Point-in-time SML state capture.

    Mirrors the ``snapshots`` table created by DuckDBManager DDL.
    ``sml_blob`` is stored as ``Text`` (JSON-serialised) for SQLite compat.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshots_project_ts", "project_id", "timestamp"),
        # Composite index for common queries that filter by project and status
        Index("ix_snapshots_project_status_ts", "project_id", "status", "timestamp"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    version_tag: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sml_blob: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="success")
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    sync_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="copy")

    # Relationships
    project: Mapped["Project"] = relationship(back_populates="snapshots")
    changes: Mapped[List["Change"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<SnapshotRow(snapshot_id={self.snapshot_id!r}, "
            f"project_id={self.project_id!r}, status={self.status!r}, sync_mode={self.sync_mode!r})>"
        )


class Change(Base):
    """Granular diff record per snapshot.

    Mirrors the ``changes`` table created by DuckDBManager DDL.
    """

    __tablename__ = "changes"

    change_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("snapshots.snapshot_id"), nullable=False
    )
    object_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    object_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    diff_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    snapshot: Mapped["SnapshotRow"] = relationship(back_populates="changes")

    def __repr__(self) -> str:
        return (
            f"<Change(change_id={self.change_id!r}, "
            f"diff_type={self.diff_type!r}, object_name={self.object_name!r})>"
        )


class ModelVersion(Base):
    """Per-model version control row (REQ-VC-001).

    Mirrors the ``model_versions`` table created by DuckDBManager DDL.
    ``snapshot`` is stored as ``Text`` (JSON-serialised).
    """

    __tablename__ = "model_versions"
    __table_args__ = (
        Index("ix_mv_model_workspace", "model_id", "workspace_id"),
    )

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False
    )
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    version_tag: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_rollback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    rollback_from_version: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ModelVersion(version_id={self.version_id!r}, "
            f"model_id={self.model_id!r}, version_tag={self.version_tag!r})>"
        )


class ModelVersionHistory(Base):
    """Tracks SHA-256 hashes of ``semabridge.yaml`` across syncs.

    Each row represents a version observed at a particular point in time.
    New rows are inserted only when the content hash changes, providing
    an immutable audit trail.

    Attributes:
        id:          Auto-increment integer primary key.
        model_name:  Semantic model name extracted from the YAML.
        version_tag: Version string extracted from the YAML (e.g. ``"v1.0"``).
        yaml_hash:   Full SHA-256 hex digest (64 chars) of the raw file.
        applied_at:  Server-default timestamp of when this version was recorded.
    """

    __tablename__ = "model_version_history"
    __table_args__ = (
        Index("ix_mvh_model_name", "model_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version_tag: Mapped[str] = mapped_column(String(100), nullable=False)
    yaml_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ModelVersionHistory(id={self.id}, model_name={self.model_name!r}, "
            f"version_tag={self.version_tag!r}, yaml_hash={self.yaml_hash[:12]}…)>"
        )


class SchemaVersionRow(Base):
    """Schema evolution history.

    Mirrors the ``schema_versions`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "schema_versions"

    version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    changes_from_previous: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default="[]"
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )
    created_by_job_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SchemaVersionRow(version_id={self.version_id!r}, "
            f"model_name={self.model_name!r}, version_number={self.version_number})>"
        )
