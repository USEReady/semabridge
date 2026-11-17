"""Project lifecycle ORM models: Project, LocalFolder, RetentionPolicy."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, true, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base
from semabridge.repository.schema_compat import PROJECT_ID_LENGTH

_UTC_DT = DateTime(timezone=True)


class LocalFolder(Base):
    """Named local filesystem location that can be reused across projects."""

    __tablename__ = "local_folders"
    __table_args__ = (
        Index("ix_local_folders_tag_name", "tag_name", unique=True),
        Index("ix_local_folders_is_active", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tag_name: Mapped[str] = mapped_column(String(255), nullable=False)
    absolute_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    def __repr__(self) -> str:
        return (
            f"<LocalFolder(id={self.id!r}, tag_name={self.tag_name!r}, "
            f"absolute_path={self.absolute_path!r}, is_active={self.is_active!r})>"
        )


class Project(Base):
    """Registered semantic project.

    Mirrors the ``projects`` table created by DuckDBManager DDL.
    """

    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[Optional[str]] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    warehouse: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    database: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    schema: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    adapter: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_connection: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    connection_tag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notification_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Relationships
    account: Mapped[Optional["Account"]] = relationship(back_populates="projects")
    snapshots: Mapped[List["SnapshotRow"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[List["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project(project_id={self.project_id!r}, name={self.name!r})>"


class RetentionPolicy(Base):
    """User-configurable retention policy per project.

    Supports strategies: 'count', 'days', 'unlimited'.
    """

    __tablename__ = "retention_policies"
    __table_args__ = (
        Index("ix_retention_project", "project_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), nullable=False, unique=True
    )
    strategy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unlimited"
    )
    max_snapshots_per_connector: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_age_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prune_manual_snapshots: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    updated_at: Mapped[datetime] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<RetentionPolicy(project_id={self.project_id!r}, strategy={self.strategy!r})>"


def _utc_now() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


class ProjectConfigVersion(Base):
    """One saved version of a project's configuration (the authoritative copy).

    Every create / save / patch / restore / upload / normalization of a
    project config appends a row here (see
    ``api/services/project_config_store.save_project_config``). The newest
    row per project is the live config; ``Config/projects/<id>.yaml`` is only
    an export of it. No foreign key to ``projects``: a config can exist for a
    project whose ``projects`` row has not been written yet (file import).
    """

    __tablename__ = "project_configs"
    __table_args__ = (
        Index("ux_project_configs_project_version", "project_id", "version", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False, default=_utc_now)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return f"<ProjectConfigVersion(project_id={self.project_id!r}, version={self.version!r}, reason={self.reason!r})>"


class MappingOverride(Base):
    """A user-chosen target name for one source field of one project model.

    The single home of mapping edits (mapping table edits, dry-run renames,
    YAML ``mappings_overrides``). ``model_key`` is the upload-prefix-free
    model name (``sync_execution_service._model_match_key``), ``''`` for an
    untagged / single-model row. ``model_ref`` keeps the original model
    reference (e.g. the PBIX path) so the exported ``model:`` tag matches
    exactly as before.
    """

    __tablename__ = "mapping_overrides"
    __table_args__ = (
        Index("ux_mapping_overrides_identity", "project_id", "model_key", "source_path", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False, index=True)
    model_key: Mapped[str] = mapped_column(String(512), nullable=False, default="", server_default="")
    model_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    target_name: Mapped[str] = mapped_column(String(1024), nullable=False)
    entity_kind: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_name: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False, default=_utc_now)
    updated_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MappingOverride(project_id={self.project_id!r}, model_key={self.model_key!r}, "
            f"source_path={self.source_path!r}, target_name={self.target_name!r})>"
        )
