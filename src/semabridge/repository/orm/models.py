"""
SQLAlchemy 2.0 ORM model definitions for SemaBridge.

Tables
------
Application layer:
- **User** — application user with authentication fields.
- **UserCredential** — per-user, per-service encrypted credential storage.
- **Post** — content item belonging to exactly one user.
- **ModelVersionHistory** — tracks SHA-256 hashes for semabridge.yaml.

Version-control layer (replaces raw DDL in DuckDBManager):
- **Project** — registered semantic projects.
- **Snapshot** — point-in-time SML state captures.
- **Change** — granular diff records per snapshot.
- **Run** — execution run lifecycle records.
- **SourceArtifact** — raw source format blobs per run.
- **ModelVersion** — per-model version control rows (REQ-VC-001).

Sync engine layer (replaces SYNC_SCHEMA_DDL in sync/repository.py):
- **SyncJob** — sync job lifecycle records.
- **SyncJobItem** — individual model items within a job.
- **ModelMappingRow** — source ↔ target mapping registry.
- **SchemaVersionRow** — schema evolution history.
- **SyncConflictRow** — conflict detection and resolution log.
- **SyncCheckpointRow** — resumable progress markers.

Credential & audit layer:
- **Credential** — service credentials (replaces semabridge_credentials DDL).
- **CommandLog** — CLI command audit trail.

All JSON payloads use ``Text`` columns for cross-dialect compatibility
(SQLite, PostgreSQL, DuckDB).  Repository methods handle
``json.dumps`` / ``json.loads`` serialisation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, false, func, text, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from semabridge.repository.orm.base import Base

# Timezone-aware DateTime used everywhere for portability.
# SQLite stores as TEXT, PostgreSQL as TIMESTAMPTZ, MySQL as DATETIME,
# DuckDB as TIMESTAMPTZ — SQLAlchemy handles the conversion.
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


# =============================================================================
# Version-Control Layer  (replaces raw DDL in DuckDBManager._init_db)
# =============================================================================

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

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
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

    # Relationships
    account: Mapped[Optional["Account"]] = relationship(back_populates="projects")
    snapshots: Mapped[List["SnapshotRow"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    runs: Mapped[List["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    artifact_metadata: Mapped[List["ArtifactMetadata"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project(project_id={self.project_id!r}, name={self.name!r})>"


class SnapshotRow(Base):
    """Point-in-time SML state capture.

    Mirrors the ``snapshots`` table created by DuckDBManager DDL.
    ``sml_blob`` is stored as ``Text`` (JSON-serialised) for SQLite compat.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        Index("ix_snapshots_project_ts", "project_id", "timestamp"),
        Index("ix_snapshots_connector", "connector_id"),
        Index("ix_snapshots_trigger", "trigger"),
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
    initiated_by: Mapped[str] = mapped_column(String(20), nullable=False, default="cli")
    run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    connector_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    trigger: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
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


class ArtifactMetadata(Base):
    """Artifact lifecycle and versioning metadata.
    
    Tracks the state of artifacts (semantic views, datasets, models) across
    the sync lifecycle: created, updated, deprecated, archived. Enables
    contract validation and version pinning for downstream consumers.
    
    Versioning strategy:
    - artifact_ref: immutable reference (e.g., "model:AggregateMetrics")
    - version: semantic version or timestamp-based (e.g., "1.0.0" or "20260512.1")
    - status: "active", "deprecated", "archived", "failed"
    - contract_version: schema contract enforced at sync time (breaks detected if mismatched)
    - deprecation_date: when deprecated; null if active or never deprecated
    - archived_at: when archived; null otherwise
    """

    __tablename__ = "artifact_metadata"
    __table_args__ = (
        Index("ix_artifact_ref_project", "project_id", "artifact_ref"),
        Index("ix_artifact_version_status", "artifact_ref", "status"),
        Index("ix_artifact_deprecation_date", "deprecation_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.project_id"), nullable=False
    )
    artifact_ref: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g., "model:AggregateMetrics"
    version: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "1.0.0" or "20260512.1"
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # "active", "deprecated", "archived", "failed"
    contract_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")  # Schema version
    
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _UTC_DT, nullable=False, default=func.now(), onupdate=func.now()
    )
    deprecation_date: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)  # When deprecated
    archived_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)  # When archived
    
    run_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # Associated run
    snapshot_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)  # Associated snapshot
    
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Extra metadata (JSON)
    
    # Relationships
    project: Mapped["Project"] = relationship(back_populates="artifact_metadata")

    def __repr__(self) -> str:
        return (
            f"<ArtifactMetadata(artifact_ref={self.artifact_ref!r}, "
            f"version={self.version!r}, status={self.status!r})>"
        )


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


# =============================================================================
# Sync Engine Layer  (replaces SYNC_SCHEMA_DDL in sync/repository.py)
# =============================================================================

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


class ModelMappingRow(Base):
    """Source ↔ target mapping registry.

    Mirrors the ``model_mappings`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "model_mappings"

    mapping_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    last_osi_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    def __repr__(self) -> str:
        return (
            f"<ModelMappingRow(mapping_id={self.mapping_id!r}, "
            f"model_name={self.model_name!r})>"
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


# =============================================================================
# Credential & Audit Layer
# =============================================================================

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
    project_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
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
