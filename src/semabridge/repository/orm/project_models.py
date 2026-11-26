"""Project lifecycle ORM models: Project, LocalFolder, RetentionPolicy."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func, true, false
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
    # Project metadata that drives decisions (sb0005). The database row is
    # the authority; the in-memory compat store only caches it.
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    sync_mode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # NULL = never recorded; "" = explicitly in no folder.
    folder_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Soft delete: a deleted project keeps its row as a tombstone so every
    # process (and a project YAML left on another host) sees it deleted.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)

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


class DriftEventRow(Base):
    """One metric drift event (governance/drift_service). Replaces the
    per-host ``~/.semabridge/drift_state.json`` event list (sb0005)."""

    __tablename__ = "drift_events"
    __table_args__ = (
        Index("ix_drift_events_project", "project_id"),
        Index("ix_drift_events_position", "position"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(PROJECT_ID_LENGTH), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Order of the event list (lower first), as the JSON list kept it.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False, default=lambda: _utc_now())


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
    # Provenance of the name (sb0006). NULL: unmarked / historical row (any
    # platform; how every row before sb0006 was saved). "user": an explicit
    # user edit. Only Databricks projects write the marker.
    origin: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<MappingOverride(project_id={self.project_id!r}, model_key={self.model_key!r}, "
            f"source_path={self.source_path!r}, target_name={self.target_name!r})>"
        )


class DatabricksProbeEvidence(Base):
    """One Databricks capability-probe run (Phase 3.5): the platform proof that
    metric-view patterns SemaBridge emits give the expected numbers on one
    workspace + warehouse. Append-only; the newest *started* run wins, so an
    older run finishing late never replaces a newer result.

    ``outcomes_json``: per probe and per gate (baseline, one_to_many_joins,
    mixed_root_views, constant_coalesce): outcome (proven | unsupported |
    wrong_result | inconclusive | defect), expected vs actual, notes.
    Platform proof only: it never authorises anyone to deploy (permissions are
    checked separately) and is not customer-model parity (3.9).
    """

    __tablename__ = "databricks_probe_evidence"
    __table_args__ = (
        Index("ix_dbx_probe_target", "workspace_host", "warehouse_id", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    workspace_host: Mapped[str] = mapped_column(String(512), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(128), nullable=False)
    probe_schema: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    dbsql_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    platform_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # warehouse metadata, settings
    suite_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    writer_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    outcomes_json: Mapped[str] = mapped_column(Text, nullable=False)
    cleanup_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)     # leftovers / cleanup issues
    started_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


# --- Databricks deploy (Phase 3.8) -------------------------------------------------------


class DatabricksDeployment(Base):
    """One Databricks deploy run (3.8): what was deployed, where, by whom, on
    which platform version. Written before any Databricks write."""

    __tablename__ = "databricks_deployments"
    __table_args__ = (
        Index("ix_dbx_deploy_target", "project_id", "model_key", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False)
    model_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    plan_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)   # millisecond revision ids
    writer_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    dbsql_version: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    metastore_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    catalog: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_name: Mapped[str] = mapped_column(String(255), nullable=False)
    workspace_host: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    warehouse_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    identity: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stub_form: Mapped[str] = mapped_column(String(32), nullable=False, default="empty_table")
    # running | succeeded | partial | failed | no_op | uncertain
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)


class DatabricksDeployedObject(Base):
    """An object a deploy run created or updated (view or empty stub table), with
    the evidence used to recognise it later as SemaBridge's own."""

    __tablename__ = "databricks_deployed_objects"
    __table_args__ = (
        Index("ix_dbx_deployed_obj", "project_id", "model_key", "fqn"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False)
    model_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)          # view | stub_table
    fqn: Mapped[str] = mapped_column(String(767), nullable=False)          # normalized catalog.schema.name
    source: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)   # model table
    uc_object_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    body_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    definition_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)   # normalized definition
    readiness: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)       # created | updated | verified
    details_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


class DatabricksDeployedName(Base):
    """A deployed source -> target name (view, field, measure, stub table) per
    metric-view occurrence. Fed back to the planner as reserved names (A5)."""

    __tablename__ = "databricks_deployed_names"
    __table_args__ = (
        Index("ix_dbx_deployed_name", "project_id", "model_key", "run_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False)
    model_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    view_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    view_source: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)          # view | field | measure | stub_table
    source_key_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


class DatabricksDeployJournal(Base):
    """Per-operation journal: written ``intended`` BEFORE a statement is sent, then
    updated. Operations left intended / submitted / uncertain are reconciled
    against Databricks before anything else is done in that schema."""

    __tablename__ = "databricks_deploy_journal"
    __table_args__ = (
        Index("ix_dbx_journal_run", "run_id", "seq", unique=True),
        Index("ix_dbx_journal_dest", "destination", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    destination: Mapped[str] = mapped_column(String(767), nullable=False)  # lease key
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_fqn: Mapped[str] = mapped_column(String(767), nullable=False)
    statement_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # normalized definition hashes: what the statement sets, and what was live before it
    definition_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    previous_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # intended | submitted | succeeded | failed | uncertain | not_executed
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    statement_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


class DatabricksDeployLease(Base):
    """A renewable lease: one deploy at a time per metastore + catalog + schema."""

    __tablename__ = "databricks_deploy_leases"

    lease_key: Mapped[str] = mapped_column(String(767), primary_key=True)
    holder_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


class DatabricksDeployIntent(Base):
    """A stored deployment intent (3.8b): exactly what a preview showed, bound to
    the target, identity, bundle and evidence by a fingerprint. Confirming it
    creates at most one run (``run_id`` is fixed at preview and unique); repeated
    confirmation returns that run. Unused intents expire."""

    __tablename__ = "databricks_deploy_intents"
    __table_args__ = (
        Index("ix_dbx_intent_project", "project_id", "model_key", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False)
    model_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    plan_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)   # millisecond revision ids
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    intent_json: Mapped[str] = mapped_column(Text, nullable=False)
    # open | used | expired | invalidated
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    used_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
