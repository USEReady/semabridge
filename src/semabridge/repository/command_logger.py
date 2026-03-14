"""
Command Logger.

Persistent logging of all SemaBridge CLI commands with:
- Command type tracking (sync, rollback, diff, validate, etc.)
- Status tracking (success, failed, in_progress)
- Duration and timing metrics
- Adapter-specific context
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from semabridge.utils.logger import get_logger
from semabridge.utils.enterprise_logger import get_enterprise_logger

logger = get_logger(__name__)


class CommandType(str, Enum):
    """Types of CLI commands."""
    DEPLOY = "deploy"
    COMPARE = "compare"
    ROLLBACK = "rollback"
    VALIDATE = "validate"
    EXTRACT = "extract"  # Internal step
    BUILD = "build"      # Internal step
    EMIT = "emit"        # Internal step
    PUBLISH = "publish"  # Internal step
    HISTORY = "history"
    LIST_PROJECTS = "list_projects"
    STATUS_CHECK = "status_check"
    CONFIG = "config"
    LOGS = "logs"
    
    # Deprecated (for historical logs)
    SYNC = "sync"
    REVERSE_SYNC = "reverse_sync"
    DIFF = "diff"
    RUN = "run"


class ActionType(str, Enum):
    """High-level action categories."""
    DEPLOYMENT = "deployment"
    ROLLBACK_OPERATION = "rollback_operation"
    COMPARISON = "comparison"
    VALIDATION = "validation"
    QUERY = "query"
    CONFIGURATION = "configuration"
    
    # Deprecated
    SYNC_OPERATION = "sync_operation"


class CommandStatus(str, Enum):
    """Command execution status."""
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    PREVIEW = "preview"  # For preview phase of rollback
    CONFIRMED = "confirmed"  # User confirmed action
    ABORTED = "aborted"  # User aborted action
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CommandLogEntry:
    """A single command log entry."""
    log_id: str
    command: CommandType
    action_type: ActionType
    status: CommandStatus
    started_at: str
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    adapter: Optional[str] = None
    project_id: Optional[str] = None
    initiated_by: str = "cli"
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def create(
        cls,
        command: CommandType,
        action_type: ActionType,
        adapter: Optional[str] = None,
        project_id: Optional[str] = None,
        initiated_by: str = "cli",
        details: Optional[Dict[str, Any]] = None,
    ) -> "CommandLogEntry":
        """Create a new command log entry."""
        return cls(
            log_id=str(uuid.uuid4()),
            command=command,
            action_type=action_type,
            status=CommandStatus.STARTED,
            started_at=datetime.now(timezone.utc).isoformat(),
            adapter=adapter,
            project_id=project_id,
            initiated_by=initiated_by,
            details=details or {},
        )
    
    def mark_success(self, duration_ms: int, details: Optional[Dict[str, Any]] = None):
        """Mark command as successful."""
        self.status = CommandStatus.SUCCESS
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.duration_ms = duration_ms
        if details:
            self.details.update(details)
    
    def mark_failed(self, error: str, duration_ms: int = 0):
        """Mark command as failed."""
        self.status = CommandStatus.FAILED
        self.completed_at = datetime.now(timezone.utc).isoformat()
        self.duration_ms = duration_ms
        self.error_message = error


class CommandLogger:
    """
    Persistent command logger using SQLAlchemy ORM.

    Tracks all CLI command executions for audit and debugging.
    """

    def __init__(self, db_path: Optional[str] = None, url_override: Optional[str] = None):
        """Initialize the command logger."""
        # Accept legacy db_path arg; translate to a DuckDB URL (never SQLite).
        if url_override:
            resolved_url: Optional[str] = url_override
        elif db_path:
            resolved_url = f"duckdb:///{db_path}"
        else:
            resolved_url = None

        if resolved_url:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from semabridge.repository.orm.base import Base

            engine = create_engine(resolved_url, echo=False, future=True)
            Base.metadata.create_all(engine)
            self._SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        else:
            from semabridge.repository.orm.session_factory import (
                get_engine,
                get_session_factory,
            )
            from semabridge.repository.orm.base import Base

            engine = get_engine()
            Base.metadata.create_all(engine)
            self._SessionLocal = get_session_factory()

    def _session(self):
        """Return a new SQLAlchemy session."""
        return self._SessionLocal()

    def log_start(
        self,
        command: CommandType,
        action_type: ActionType,
        adapter: Optional[str] = None,
        project_id: Optional[str] = None,
        initiated_by: str = "cli",
        details: Optional[Dict[str, Any]] = None,
    ) -> CommandLogEntry:
        """Log the start of a command."""
        import json
        from semabridge.repository.orm.models import CommandLog

        entry = CommandLogEntry.create(
            command=command,
            action_type=action_type,
            adapter=adapter,
            project_id=project_id,
            initiated_by=initiated_by,
            details=details,
        )

        try:
            with self._session() as session:
                session.add(
                    CommandLog(
                        log_id=entry.log_id,
                        command=entry.command.value,
                        action_type=entry.action_type.value,
                        status=entry.status.value,
                        started_at=datetime.fromisoformat(entry.started_at.replace("Z", "+00:00"))
                            if isinstance(entry.started_at, str) else entry.started_at,
                        adapter=entry.adapter,
                        project_id=entry.project_id,
                        initiated_by=entry.initiated_by,
                        details=json.dumps(entry.details),
                    )
                )
                session.commit()

            logger.debug("Command started: %s (id=%s)", command.value, entry.log_id[:8])

            try:
                ent_logger = get_enterprise_logger()
                ent_logger.log_operation_start(
                    operation_id=entry.log_id,
                    command=entry.command.value,
                    adapter=entry.adapter,
                    project_id=entry.project_id or "n/a",
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to persist command log start: %s", exc)

        return entry

    def log_success(
        self,
        entry: CommandLogEntry,
        duration_ms: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log successful command completion."""
        import json
        from sqlalchemy import update
        from semabridge.repository.orm.models import CommandLog

        entry.mark_success(duration_ms, details)

        try:
            with self._session() as session:
                session.execute(
                    update(CommandLog)
                    .where(CommandLog.log_id == entry.log_id)
                    .values(
                        status=entry.status.value,
                        completed_at=datetime.fromisoformat(entry.completed_at.replace("Z", "+00:00"))
                            if entry.completed_at else None,
                        duration_ms=entry.duration_ms,
                        details=json.dumps(entry.details),
                    )
                )
                session.commit()

            logger.info(
                "Command completed: %s (id=%s, duration=%dms)",
                entry.command.value,
                entry.log_id[:8],
                duration_ms,
            )

            try:
                ent_logger = get_enterprise_logger()
                ent_logger.log_operation_success(
                    operation_id=entry.log_id,
                    duration_ms=duration_ms,
                    new_version=entry.details.get("snapshot_id"),
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to persist command log success: %s", exc)

    def log_failure(
        self,
        entry: CommandLogEntry,
        error: str,
        duration_ms: int = 0,
    ) -> None:
        """Log command failure."""
        from sqlalchemy import update
        from semabridge.repository.orm.models import CommandLog

        entry.mark_failed(error, duration_ms)

        try:
            with self._session() as session:
                session.execute(
                    update(CommandLog)
                    .where(CommandLog.log_id == entry.log_id)
                    .values(
                        status=entry.status.value,
                        completed_at=datetime.fromisoformat(entry.completed_at.replace("Z", "+00:00"))
                            if entry.completed_at else None,
                        duration_ms=entry.duration_ms,
                        error_message=entry.error_message,
                    )
                )
                session.commit()

            logger.error(
                "Command failed: %s (id=%s, error=%s)",
                entry.command.value,
                entry.log_id[:8],
                error[:100],
            )

            try:
                ent_logger = get_enterprise_logger()
                ent_logger.log_operation_failed(
                    operation_id=entry.log_id,
                    error=error,
                    duration_ms=duration_ms,
                )
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Failed to persist command log failure: %s", exc)

    def log_phase(
        self,
        entry: CommandLogEntry,
        phase: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a phase transition (e.g., preview, confirmed)."""
        import json
        from sqlalchemy import update
        from semabridge.repository.orm.models import CommandLog

        entry.status = (
            CommandStatus(phase)
            if phase in [s.value for s in CommandStatus]
            else CommandStatus.IN_PROGRESS
        )
        if details:
            entry.details.update(details)
        entry.details["phase"] = phase

        try:
            with self._session() as session:
                session.execute(
                    update(CommandLog)
                    .where(CommandLog.log_id == entry.log_id)
                    .values(
                        status=entry.status.value,
                        details=json.dumps(entry.details),
                    )
                )
                session.commit()

            logger.info(
                "Command phase: %s -> %s (id=%s)",
                entry.command.value,
                phase,
                entry.log_id[:8],
            )
        except Exception as exc:
            logger.warning("Failed to persist command log phase: %s", exc)

    def log_aborted(
        self,
        entry: CommandLogEntry,
        reason: str = "User cancelled",
        duration_ms: int = 0,
    ) -> None:
        """Log command aborted by user."""
        import json
        from sqlalchemy import update
        from semabridge.repository.orm.models import CommandLog

        entry.status = CommandStatus.ABORTED
        entry.completed_at = datetime.now(timezone.utc).isoformat()
        entry.duration_ms = duration_ms
        entry.details["abort_reason"] = reason

        try:
            with self._session() as session:
                session.execute(
                    update(CommandLog)
                    .where(CommandLog.log_id == entry.log_id)
                    .values(
                        status=entry.status.value,
                        completed_at=datetime.now(timezone.utc),
                        duration_ms=entry.duration_ms,
                        details=json.dumps(entry.details),
                    )
                )
                session.commit()

            logger.warning(
                "Command aborted: %s (id=%s, reason=%s)",
                entry.command.value,
                entry.log_id[:8],
                reason,
            )
        except Exception as exc:
            logger.warning("Failed to persist command log abort: %s", exc)

    def get_recent_logs(
        self,
        limit: int = 20,
        command_filter: Optional[CommandType] = None,
        status_filter: Optional[CommandStatus] = None,
    ) -> List[CommandLogEntry]:
        """Get recent command logs."""
        import json
        from sqlalchemy import select, and_
        from semabridge.repository.orm.models import CommandLog as CommandLogModel

        try:
            with self._session() as session:
                stmt = (
                    select(CommandLogModel)
                    .order_by(CommandLogModel.started_at.desc())
                    .limit(limit)
                )
                conditions = []
                if command_filter:
                    conditions.append(CommandLogModel.command == command_filter.value)
                if status_filter:
                    conditions.append(CommandLogModel.status == status_filter.value)
                if conditions:
                    stmt = stmt.where(and_(*conditions))

                rows = session.execute(stmt).scalars().all()
                entries = []
                for row in rows:
                    details: Any = row.details or {}
                    if isinstance(details, str):
                        details = json.loads(details)

                    entries.append(
                        CommandLogEntry(
                            log_id=row.log_id,
                            command=CommandType(row.command),
                            action_type=ActionType(row.action_type),
                            status=CommandStatus(row.status),
                            started_at=str(row.started_at),
                            completed_at=str(row.completed_at) if row.completed_at else None,
                            duration_ms=row.duration_ms,
                            adapter=row.adapter,
                            project_id=row.project_id,
                            initiated_by=row.initiated_by,
                            error_message=row.error_message,
                            details=details,
                        )
                    )
                return entries
        except Exception as exc:
            logger.warning("Failed to load recent logs: %s", exc)
            return []

    def get_command_stats(self, days: int = 30) -> Dict[str, Any]:
        """Get command statistics for the last N days."""
        from sqlalchemy import select, func, case
        from semabridge.repository.orm.models import CommandLog as CommandLogModel

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        try:
            with self._session() as session:
                stmt = (
                    select(
                        CommandLogModel.command,
                        func.count().label("total"),
                        func.sum(
                            case((CommandLogModel.status == "success", 1), else_=0)
                        ).label("success_count"),
                        func.sum(
                            case((CommandLogModel.status == "failed", 1), else_=0)
                        ).label("failed_count"),
                        func.avg(CommandLogModel.duration_ms).label("avg_duration_ms"),
                    )
                    .where(CommandLogModel.started_at >= cutoff)
                    .group_by(CommandLogModel.command)
                    .order_by(func.count().desc())
                )
                rows = session.execute(stmt).all()

                return {
                    "period_days": days,
                    "commands": [
                        {
                            "command": row.command,
                            "total": row.total,
                            "success": row.success_count or 0,
                            "failed": row.failed_count or 0,
                            "success_rate": round(
                                (row.success_count or 0) / row.total * 100, 1
                            )
                            if row.total > 0
                            else 0,
                            "avg_duration_ms": int(row.avg_duration_ms)
                            if row.avg_duration_ms
                            else 0,
                        }
                        for row in rows
                    ],
                }
        except Exception as exc:
            logger.warning("Failed to load command stats: %s", exc)
            return {"period_days": days, "commands": []}


# Singleton instance
_command_logger: Optional[CommandLogger] = None


def get_command_logger() -> CommandLogger:
    """Get the singleton command logger instance."""
    global _command_logger
    if _command_logger is None:
        _command_logger = CommandLogger()
    return _command_logger
