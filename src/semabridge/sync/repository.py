"""
Sync Repository â€” SQLAlchemy ORM persistence for the synchronization engine.

Covers:
- sync_jobs / sync_job_items â€” job lifecycle tracking
- model_mappings â€” sourceâ†”target mapping registry
- schema_versions â€” schema evolution history
- sync_conflicts â€” conflict detection and resolution log
- sync_checkpoints â€” resumable progress markers

All tables are defined as SQLAlchemy ORM models in
:mod:`semabridge.repository.orm.models` and created via
``Base.metadata.create_all(engine)``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update, delete, and_
from sqlalchemy.orm import Session

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import (
    ModelMappingRow,
    SchemaVersionRow,
    SyncCheckpointRow,
    SyncConflictRow,
    SyncJob as SyncJobRow,
    SyncJobItem as SyncJobItemRow,
)
from semabridge.sync.models import (
    ConflictResolution,
    ModelMapping,
    SchemaVersion,
    SyncCheckpoint,
    SyncConflict,
    SyncJob,
    SyncJobItem,
    SyncJobStatus,
    SyncItemStatus,
    _utc_now,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class SyncRepository:
    """
    SQLAlchemy-backed persistence layer for the sync engine.

    Args:
        db_path: (Deprecated) legacy DuckDB path â€" translated to a DuckDB URL.
        url_override: SQLAlchemy connection URL (for tests: ``duckdb:///:memory:``).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        url_override: Optional[str] = None,
    ) -> None:
        # Accept legacy db_path arg; translate to DuckDB URL (never SQLite).
        if url_override:
            resolved_url: Optional[str] = url_override
        elif db_path:
            resolved_url = f"duckdb:///{db_path}"
        else:
            resolved_url = None

        if resolved_url:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker

            engine = create_engine(resolved_url, echo=False, future=True)
            Base.metadata.create_all(engine)
            self._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        else:
            from semabridge.repository.orm.session_factory import (
                get_engine,
                get_session_factory,
            )

            engine = get_engine()
            Base.metadata.create_all(engine)
            self._SessionLocal = get_session_factory()

        logger.debug("Sync repository initialised")

    def _session(self) -> Session:
        return self._SessionLocal()

    # -----------------------------------------------------------------
    # Internal helpers: ORM row ↔ Pydantic model conversion
    # -----------------------------------------------------------------

    @staticmethod
    def _dt_to_str(value: object) -> str | None:
        """Convert a datetime (returned by PostgreSQL) to ISO-8601 string.

        PostgreSQL TIMESTAMP columns return Python ``datetime`` objects.
        Our Pydantic models store timestamps as ISO-8601 strings for
        JSON/DuckDB compatibility.  This helper is the bridge.
        """
        if value is None:
            return None
        if isinstance(value, str):
            return value
        # datetime / date objects
        try:
            return value.isoformat()  # type: ignore[union-attr]
        except AttributeError:
            return str(value)

    @staticmethod
    def _str_to_dt(value: object) -> datetime | None:
        """Convert ISO-8601 string (from Pydantic) to python datetime (for SQLAlchemy)."""
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                # python 3.10+ fromisoformat handles standard iso strings well, replace Z
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return None

    @staticmethod
    def _job_row_to_model(row: SyncJobRow) -> SyncJob:
        data = {
            "job_id": row.job_id,
            "direction": row.direction,
            "status": row.status,
            "conflict_resolution": row.conflict_resolution,
            "created_at": SyncRepository._dt_to_str(row.created_at),
            "started_at": SyncRepository._dt_to_str(row.started_at),
            "completed_at": SyncRepository._dt_to_str(row.completed_at),
            "initiated_by": row.initiated_by,
            "source_folder": row.source_folder,
            "source_connection": row.source_connection,
            "target_workspace_id": row.target_workspace_id,
            "target_snowflake_schema": row.target_snowflake_schema,
            "total_items": row.total_items,
            "completed_items": row.completed_items,
            "failed_items": row.failed_items,
            "duration_ms": row.duration_ms,
            "error_message": row.error_message,
        }
        return SyncJob(**data)

    @staticmethod
    def _item_row_to_model(row: SyncJobItemRow) -> SyncJobItem:
        osi = row.osi_snapshot
        if isinstance(osi, str):
            try:
                osi = json.loads(osi)
            except Exception:
                osi = None
        return SyncJobItem(
            item_id=row.item_id,
            job_id=row.job_id,
            model_name=row.model_name,
            source_path=row.source_path,
            status=row.status,
            started_at=SyncRepository._dt_to_str(row.started_at),
            completed_at=SyncRepository._dt_to_str(row.completed_at),
            error_message=row.error_message,
            osi_snapshot=osi,
            target_artifact_id=row.target_artifact_id,
            duration_ms=row.duration_ms,
        )

    @staticmethod
    def _mapping_row_to_model(row: ModelMappingRow) -> ModelMapping:
        return ModelMapping(
            mapping_id=row.mapping_id,
            source_type=row.source_type,
            source_identifier=row.source_identifier,
            target_type=row.target_type,
            target_identifier=row.target_identifier,
            model_name=row.model_name,
            last_synced_at=SyncRepository._dt_to_str(row.last_synced_at),
            last_osi_hash=row.last_osi_hash,
            created_at=SyncRepository._dt_to_str(row.created_at),
            is_active=bool(row.is_active),
        )

    @staticmethod
    def _schema_row_to_model(row: SchemaVersionRow) -> SchemaVersion:
        snap = row.schema_snapshot
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except Exception:
                snap = {}
        changes = row.changes_from_previous
        if isinstance(changes, str):
            try:
                changes = json.loads(changes)
            except Exception:
                changes = []
        return SchemaVersion(
            version_id=row.version_id,
            model_name=row.model_name,
            version_number=row.version_number,
            schema_hash=row.schema_hash,
            schema_snapshot=snap,
            changes_from_previous=changes,
            created_at=SyncRepository._dt_to_str(row.created_at),
            created_by_job_id=row.created_by_job_id,
        )

    @staticmethod
    def _conflict_row_to_model(row: SyncConflictRow) -> SyncConflict:
        src = row.source_value
        tgt = row.target_value
        if isinstance(src, str):
            try:
                src = json.loads(src)
            except Exception:
                src = None
        if isinstance(tgt, str):
            try:
                tgt = json.loads(tgt)
            except Exception:
                tgt = None
        return SyncConflict(
            conflict_id=row.conflict_id,
            job_id=row.job_id,
            item_id=row.item_id,
            model_name=row.model_name,
            change_type=row.change_type,
            severity=row.severity,
            description=row.description,
            source_value=src,
            target_value=tgt,
            resolution=row.resolution,
            resolved_at=SyncRepository._dt_to_str(row.resolved_at),
            resolved_by=row.resolved_by,
            resolution_note=getattr(row, "resolution_note", None),
            escalated=getattr(row, "escalated", False) or False,
            created_at=SyncRepository._dt_to_str(row.created_at),
        )

    # -----------------------------------------------------------------
    # Sync Jobs
    # -----------------------------------------------------------------

    def create_job(self, job: SyncJob) -> SyncJob:
        """Persist a new sync job."""
        with self._session() as session:
            session.add(
                SyncJobRow(
                    job_id=job.job_id,
                    direction=job.direction.value
                        if hasattr(job.direction, "value") else job.direction,
                    status=job.status.value
                        if hasattr(job.status, "value") else job.status,
                    conflict_resolution=job.conflict_resolution.value
                        if hasattr(job.conflict_resolution, "value")
                        else job.conflict_resolution,
                    created_at=SyncRepository._str_to_dt(job.created_at),
                    started_at=SyncRepository._str_to_dt(job.started_at),
                    completed_at=SyncRepository._str_to_dt(job.completed_at),
                    initiated_by=job.initiated_by,
                    source_folder=job.source_folder,
                    source_connection=job.source_connection,
                    target_workspace_id=job.target_workspace_id,
                    target_snowflake_schema=job.target_snowflake_schema,
                    total_items=job.total_items,
                    completed_items=job.completed_items,
                    failed_items=job.failed_items,
                    duration_ms=job.duration_ms,
                    error_message=job.error_message,
                )
            )
            session.commit()
        logger.info("Created sync job %s [%s]", job.job_id, job.direction)
        return job

    def update_job(self, job: SyncJob) -> None:
        """Update an existing sync job."""
        with self._session() as session:
            session.execute(
                update(SyncJobRow)
                .where(SyncJobRow.job_id == job.job_id)
                .values(
                    status=job.status.value
                        if hasattr(job.status, "value") else job.status,
                    started_at=SyncRepository._str_to_dt(job.started_at),
                    completed_at=SyncRepository._str_to_dt(job.completed_at),
                    total_items=job.total_items,
                    completed_items=job.completed_items,
                    failed_items=job.failed_items,
                    duration_ms=job.duration_ms,
                    error_message=job.error_message,
                )
            )
            session.commit()

    def get_job(self, job_id: str) -> Optional[SyncJob]:
        """Retrieve a sync job by ID."""
        with self._session() as session:
            row = session.get(SyncJobRow, job_id)
            return self._job_row_to_model(row) if row else None

    def list_jobs(
        self,
        status: Optional[SyncJobStatus] = None,
        limit: int = 50,
    ) -> List[SyncJob]:
        """List sync jobs, optionally filtered by status."""
        with self._session() as session:
            stmt = (
                select(SyncJobRow)
                .order_by(SyncJobRow.created_at.desc())
                .limit(limit)
            )
            if status:
                stmt = stmt.where(
                    SyncJobRow.status == (
                        status.value if hasattr(status, "value") else status
                    )
                )
            rows = session.execute(stmt).scalars().all()
            return [self._job_row_to_model(r) for r in rows]

    # -----------------------------------------------------------------
    # Sync Job Items
    # -----------------------------------------------------------------

    def create_item(self, item: SyncJobItem) -> SyncJobItem:
        """Persist a new sync job item."""
        with self._session() as session:
            session.add(
                SyncJobItemRow(
                    item_id=item.item_id,
                    job_id=item.job_id,
                    model_name=item.model_name,
                    source_path=item.source_path,
                    status=item.status.value
                        if hasattr(item.status, "value") else item.status,
                    started_at=item.started_at,
                    completed_at=item.completed_at,
                    error_message=item.error_message,
                    osi_snapshot=json.dumps(item.osi_snapshot)
                        if item.osi_snapshot else None,
                    target_artifact_id=item.target_artifact_id,
                    duration_ms=item.duration_ms,
                )
            )
            session.commit()
        return item

    def update_item(self, item: SyncJobItem) -> None:
        """Update an existing sync job item."""
        with self._session() as session:
            session.execute(
                update(SyncJobItemRow)
                .where(SyncJobItemRow.item_id == item.item_id)
                .values(
                    status=item.status.value
                        if hasattr(item.status, "value") else item.status,
                    started_at=SyncRepository._str_to_dt(item.started_at),
                    completed_at=SyncRepository._str_to_dt(item.completed_at),
                    error_message=item.error_message,
                    osi_snapshot=json.dumps(item.osi_snapshot)
                        if item.osi_snapshot else None,
                    target_artifact_id=item.target_artifact_id,
                    duration_ms=item.duration_ms,
                )
            )
            session.commit()

    def get_items_for_job(self, job_id: str) -> List[SyncJobItem]:
        """Get all items belonging to a sync job."""
        with self._session() as session:
            rows = session.execute(
                select(SyncJobItemRow)
                .where(SyncJobItemRow.job_id == job_id)
                .order_by(SyncJobItemRow.model_name)
            ).scalars().all()
            return [self._item_row_to_model(r) for r in rows]

    # -----------------------------------------------------------------
    # Model Mappings
    # -----------------------------------------------------------------

    def upsert_mapping(self, mapping: ModelMapping) -> ModelMapping:
        """Insert or update a model mapping."""
        with self._session() as session:
            existing = session.execute(
                select(ModelMappingRow)
                .where(
                    and_(
                        ModelMappingRow.source_type == mapping.source_type,
                        ModelMappingRow.source_identifier == mapping.source_identifier,
                        ModelMappingRow.target_type == mapping.target_type,
                        ModelMappingRow.target_identifier == mapping.target_identifier,
                    )
                )
                .limit(1)
            ).scalars().first()

            if existing:
                existing.model_name = mapping.model_name
                existing.last_synced_at = SyncRepository._str_to_dt(mapping.last_synced_at)
                existing.last_osi_hash = mapping.last_osi_hash
                existing.is_active = mapping.is_active
                mapping = mapping.model_copy(update={"mapping_id": existing.mapping_id}) \
                    if hasattr(mapping, "model_copy") else mapping
            else:
                session.add(
                    ModelMappingRow(
                        mapping_id=mapping.mapping_id,
                        source_type=mapping.source_type,
                        source_identifier=mapping.source_identifier,
                        target_type=mapping.target_type,
                        target_identifier=mapping.target_identifier,
                        model_name=mapping.model_name,
                        last_synced_at=SyncRepository._str_to_dt(mapping.last_synced_at),
                        last_osi_hash=mapping.last_osi_hash,
                        created_at=SyncRepository._str_to_dt(mapping.created_at),
                        is_active=mapping.is_active,
                    )
                )
            session.commit()
        return mapping

    def get_mapping(
        self,
        source_type: str,
        source_identifier: str,
        target_type: str,
    ) -> Optional[ModelMapping]:
        """Find a mapping by source and target type."""
        with self._session() as session:
            row = session.execute(
                select(ModelMappingRow)
                .where(
                    and_(
                        ModelMappingRow.source_type == source_type,
                        ModelMappingRow.source_identifier == source_identifier,
                        ModelMappingRow.target_type == target_type,
                        ModelMappingRow.is_active.is_(True),
                    )
                )
                .limit(1)
            ).scalars().first()
            return self._mapping_row_to_model(row) if row else None

    def list_mappings(self, active_only: bool = True) -> List[ModelMapping]:
        """List all model mappings."""
        with self._session() as session:
            stmt = select(ModelMappingRow).order_by(ModelMappingRow.model_name)
            if active_only:
                stmt = stmt.where(ModelMappingRow.is_active.is_(True))
            rows = session.execute(stmt).scalars().all()
            return [self._mapping_row_to_model(r) for r in rows]

    # -----------------------------------------------------------------
    # Schema Versions
    # -----------------------------------------------------------------

    def create_schema_version(self, version: SchemaVersion) -> SchemaVersion:
        """Persist a new schema version."""
        with self._session() as session:
            session.add(
                SchemaVersionRow(
                    version_id=version.version_id,
                    model_name=version.model_name,
                    version_number=version.version_number,
                    schema_hash=version.schema_hash,
                    schema_snapshot=json.dumps(version.schema_snapshot),
                    changes_from_previous=json.dumps(version.changes_from_previous),
                    created_at=SyncRepository._str_to_dt(version.created_at),
                    created_by_job_id=version.created_by_job_id,
                )
            )
            session.commit()
        return version

    def get_latest_schema_version(self, model_name: str) -> Optional[SchemaVersion]:
        """Get the latest schema version for a model."""
        with self._session() as session:
            row = session.execute(
                select(SchemaVersionRow)
                .where(SchemaVersionRow.model_name == model_name)
                .order_by(SchemaVersionRow.version_number.desc())
                .limit(1)
            ).scalars().first()
            return self._schema_row_to_model(row) if row else None

    def get_schema_history(
        self, model_name: str, limit: int = 20
    ) -> List[SchemaVersion]:
        """Get schema version history for a model."""
        with self._session() as session:
            rows = session.execute(
                select(SchemaVersionRow)
                .where(SchemaVersionRow.model_name == model_name)
                .order_by(SchemaVersionRow.version_number.desc())
                .limit(limit)
            ).scalars().all()
            return [self._schema_row_to_model(r) for r in rows]

    # -----------------------------------------------------------------
    # Conflicts
    # -----------------------------------------------------------------

    def create_conflict(self, conflict: SyncConflict) -> SyncConflict:
        """Persist a new sync conflict."""
        with self._session() as session:
            session.add(
                SyncConflictRow(
                    conflict_id=conflict.conflict_id,
                    job_id=conflict.job_id,
                    item_id=conflict.item_id,
                    model_name=conflict.model_name,
                    change_type=conflict.change_type.value
                        if hasattr(conflict.change_type, "value")
                        else conflict.change_type,
                    severity=conflict.severity.value
                        if hasattr(conflict.severity, "value")
                        else conflict.severity,
                    description=conflict.description,
                    source_value=json.dumps(conflict.source_value)
                        if conflict.source_value else None,
                    target_value=json.dumps(conflict.target_value)
                        if conflict.target_value else None,
                    resolution=conflict.resolution.value
                        if conflict.resolution and hasattr(conflict.resolution, "value")
                        else conflict.resolution,
                    resolved_at=SyncRepository._str_to_dt(conflict.resolved_at),
                    resolved_by=conflict.resolved_by,
                    created_at=SyncRepository._str_to_dt(conflict.created_at),
                )
            )
            session.commit()
        return conflict

    def resolve_conflict(
        self,
        conflict_id: str,
        resolution: ConflictResolution,
        resolved_by: str = "user",
    ) -> None:
        """Mark a conflict as resolved (legacy simple form)."""
        self.resolve_conflict_detailed(
            conflict_id=conflict_id,
            resolution=resolution,
            resolved_by=resolved_by,
        )

    def resolve_conflict_detailed(
        self,
        conflict_id: str,
        resolution: ConflictResolution,
        resolved_by: str = "user",
        resolution_note: Optional[str] = None,
        escalated: bool = False,
    ) -> None:
        """Mark a conflict as resolved with optional note and escalation flag.

        When escalated=True the conflict is tagged for human review and
        the job remains paused even if no other CRITICAL conflicts exist.
        """
        from semabridge.sync.models import ConflictResolution as _CR
        _res_val = resolution.value if hasattr(resolution, "value") else resolution
        with self._session() as session:
            _values: dict = dict(
                resolution=_res_val,
                resolved_at=SyncRepository._str_to_dt(_utc_now()),
                resolved_by=resolved_by,
            )
            # Only write new columns when they are supported (graceful on older DBs)
            try:
                _values["resolution_note"] = resolution_note
                _values["escalated"] = escalated
            except Exception:
                pass
            session.execute(
                update(SyncConflictRow)
                .where(SyncConflictRow.conflict_id == conflict_id)
                .values(**_values)
            )
            session.commit()

    def get_unresolved_conflicts(self, job_id: str) -> List[SyncConflict]:
        """Get all unresolved conflicts for a sync job."""
        with self._session() as session:
            rows = session.execute(
                select(SyncConflictRow)
                .where(
                    and_(
                        SyncConflictRow.job_id == job_id,
                        SyncConflictRow.resolution.is_(None),
                    )
                )
                .order_by(SyncConflictRow.severity.desc(), SyncConflictRow.created_at)
            ).scalars().all()
            return [self._conflict_row_to_model(r) for r in rows]

    def get_conflicts_for_job(self, job_id: str) -> List[SyncConflict]:
        """Get all conflicts (resolved and unresolved) for a job."""
        with self._session() as session:
            rows = session.execute(
                select(SyncConflictRow)
                .where(SyncConflictRow.job_id == job_id)
                .order_by(SyncConflictRow.created_at)
            ).scalars().all()
            return [self._conflict_row_to_model(r) for r in rows]

    # -----------------------------------------------------------------
    # Checkpoints
    # -----------------------------------------------------------------

    def save_checkpoint(self, checkpoint: SyncCheckpoint) -> SyncCheckpoint:
        """Save a resumable progress checkpoint (one per job)."""
        with self._session() as session:
            # Delete previous checkpoint for this job
            session.execute(
                delete(SyncCheckpointRow).where(
                    SyncCheckpointRow.job_id == checkpoint.job_id
                )
            )
            session.add(
                SyncCheckpointRow(
                    checkpoint_id=checkpoint.checkpoint_id,
                    job_id=checkpoint.job_id,
                    last_processed_item_id=checkpoint.last_processed_item_id,
                    last_processed_index=checkpoint.last_processed_index,
                    state_snapshot=json.dumps(checkpoint.state_snapshot),
                    created_at=SyncRepository._str_to_dt(checkpoint.created_at),
                )
            )
            session.commit()
        return checkpoint

    def get_checkpoint(self, job_id: str) -> Optional[SyncCheckpoint]:
        """Get the latest checkpoint for a job."""
        with self._session() as session:
            row = session.execute(
                select(SyncCheckpointRow)
                .where(SyncCheckpointRow.job_id == job_id)
                .order_by(SyncCheckpointRow.created_at.desc())
                .limit(1)
            ).scalars().first()
            if not row:
                return None
            state = row.state_snapshot
            if isinstance(state, str):
                try:
                    state = json.loads(state)
                except Exception:
                    state = {}
            return SyncCheckpoint(
                checkpoint_id=row.checkpoint_id,
                job_id=row.job_id,
                last_processed_item_id=row.last_processed_item_id,
                last_processed_index=row.last_processed_index,
                state_snapshot=state,
                created_at=SyncRepository._dt_to_str(row.created_at),
            )

