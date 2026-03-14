"""
Enterprise Logger.

Structured, file-based logging for CLI operations with:
- Operations log: Track all CLI operations
- Errors log: Track failures and exceptions
- Audit log: Track user actions for compliance

Uses standard Python logging with RotatingFileHandler for log rotation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

# Singleton instance
_enterprise_logger: Optional["EnterpriseLogger"] = None


class LogLevel(str, Enum):
    """Log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class MessageType(str, Enum):
    """Structured message types."""
    OPERATION_START = "OPERATION_START"
    OPERATION_SUCCESS = "OPERATION_SUCCESS"
    OPERATION_FAILED = "OPERATION_FAILED"
    SNAPSHOT_CREATED = "SNAPSHOT_CREATED"
    ROLLBACK_INITIATED = "ROLLBACK_INITIATED"
    ROLLBACK_SUCCESS = "ROLLBACK_SUCCESS"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ADAPTER_EXECUTION = "ADAPTER_EXECUTION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EXCEPTION = "EXCEPTION"
    DIAGNOSTIC = "DIAGNOSTIC"
    USER_ACTION = "USER_ACTION"
    SCHEDULED_JOB = "SCHEDULED_JOB"
    STATE_CHANGE = "STATE_CHANGE"
    PERFORMANCE = "PERFORMANCE"


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured log messages."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record with structured fields."""
        # Get message type if available
        message_type = getattr(record, "message_type", "GENERAL")
        
        # Get extra fields
        extra_fields = getattr(record, "extra_fields", {})
        
        # Build structured message
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        
        # Format extra fields as key=value pairs
        extra_str = " ".join(f"{k}={v}" for k, v in extra_fields.items())
        
        if extra_str:
            return f"[{timestamp}] [{level}] [{message_type}] {record.getMessage()} {extra_str}"
        else:
            return f"[{timestamp}] [{level}] [{message_type}] {record.getMessage()}"


class EnterpriseLogger:
    """
    Enterprise-grade logging system.
    
    Features:
    - Separate log files for operations, errors, and audit
    - Structured logging with message types
    - Log rotation based on file size
    - Configurable retention policy
    - Thread-safe operation
    """
    
    DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100MB
    DEFAULT_BACKUP_COUNT = 10
    
    def __init__(
        self,
        logs_dir: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the enterprise logger.
        
        Args:
            logs_dir: Directory for log files. Defaults to .semantic_metadata/logs
            config: Optional configuration dictionary.
        """
        if logs_dir is None:
            project_root = Path(__file__).parent.parent.parent
            logs_dir = str(project_root / ".semantic_metadata" / "logs")
        
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Load or use default config
        self.config = config or self._get_default_config()
        
        # Initialize loggers
        self._operations_logger = self._create_logger(
            name="semabridge.operations",
            filename_pattern="operations_{date}.log",
            level=self.config.get("operations_level", "INFO"),
            max_bytes=self.config.get("operations_max_bytes", self.DEFAULT_MAX_BYTES),
            backup_count=self.config.get("operations_backup_count", self.DEFAULT_BACKUP_COUNT),
        )
        
        self._errors_logger = self._create_logger(
            name="semabridge.errors",
            filename_pattern="errors_{date}.log",
            level=self.config.get("errors_level", "ERROR"),
            max_bytes=self.config.get("errors_max_bytes", 50 * 1024 * 1024),
            backup_count=self.config.get("errors_backup_count", 5),
        )
        
        self._audit_logger = self._create_logger(
            name="semabridge.audit",
            filename_pattern="audit_{date}.log",
            level=self.config.get("audit_level", "INFO"),
            max_bytes=self.config.get("audit_max_bytes", 200 * 1024 * 1024),
            backup_count=self.config.get("audit_backup_count", 20),
        )
        
        # Performance tracking
        self._slow_threshold_ms = self.config.get("slow_operation_threshold_ms", 5000)
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Get default logging configuration."""
        return {
            "version": "1.0",
            "environment": "production",
            "operations_level": "INFO",
            "errors_level": "ERROR",
            "audit_level": "INFO",
            "operations_max_bytes": 100 * 1024 * 1024,
            "errors_max_bytes": 50 * 1024 * 1024,
            "audit_max_bytes": 200 * 1024 * 1024,
            "operations_backup_count": 10,
            "errors_backup_count": 5,
            "audit_backup_count": 20,
            "retention_days": 90,
            "slow_operation_threshold_ms": 5000,
        }
    
    def _create_logger(
        self,
        name: str,
        filename_pattern: str,
        level: str,
        max_bytes: int,
        backup_count: int,
    ) -> logging.Logger:
        """Create a configured logger with rotating file handler."""
        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, level.upper()))
        
        # Remove existing handlers
        logger.handlers.clear()
        
        # Create filename with current date
        date_str = datetime.now().strftime("%Y_%m_%d")
        filename = filename_pattern.replace("{date}", date_str)
        filepath = self.logs_dir / filename
        
        # Create rotating file handler
        handler = RotatingFileHandler(
            filepath,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        handler.setLevel(getattr(logging, level.upper()))
        handler.setFormatter(StructuredFormatter())
        
        logger.addHandler(handler)
        
        # Prevent propagation to root logger
        logger.propagate = False
        
        return logger
    
    def _log_with_extras(
        self,
        logger: logging.Logger,
        level: int,
        message: str,
        message_type: MessageType,
        **extra_fields: Any,
    ) -> None:
        """Log with extra structured fields."""
        record = logger.makeRecord(
            name=logger.name,
            level=level,
            fn="",
            lno=0,
            msg=message,
            args=(),
            exc_info=None,
        )
        record.message_type = message_type.value
        record.extra_fields = {k: str(v) for k, v in extra_fields.items()}
        logger.handle(record)
    
    # ========== Operations Logging ==========
    
    def log_operation_start(
        self,
        operation_id: str,
        command: str,
        adapter: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Log operation start."""
        self._log_with_extras(
            self._operations_logger,
            logging.INFO,
            f"Operation started: {command}",
            MessageType.OPERATION_START,
            operation_id=operation_id,
            command=command,
            adapter=adapter or "n/a",
            **kwargs,
        )
    
    def log_operation_success(
        self,
        operation_id: str,
        duration_ms: int,
        new_version: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Log operation success."""
        level = logging.INFO
        
        # Log as warning if slow
        if duration_ms > self._slow_threshold_ms:
            level = logging.WARNING
        
        self._log_with_extras(
            self._operations_logger,
            level,
            f"Operation completed successfully",
            MessageType.OPERATION_SUCCESS,
            operation_id=operation_id,
            duration_ms=duration_ms,
            new_version=new_version or "n/a",
            **kwargs,
        )
    
    def log_operation_failed(
        self,
        operation_id: str,
        error: str,
        duration_ms: int = 0,
        **kwargs: Any,
    ) -> None:
        """Log operation failure."""
        self._log_with_extras(
            self._operations_logger,
            logging.ERROR,
            f"Operation failed: {error}",
            MessageType.OPERATION_FAILED,
            operation_id=operation_id,
            error=error,
            duration_ms=duration_ms,
            **kwargs,
        )
        
        # Also log to errors log
        self._log_with_extras(
            self._errors_logger,
            logging.ERROR,
            f"Operation failed: {error}",
            MessageType.OPERATION_FAILED,
            operation_id=operation_id,
            **kwargs,
        )
    
    def log_snapshot_created(
        self,
        snapshot_id: str,
        adapter: str,
        entity_count: int,
        **kwargs: Any,
    ) -> None:
        """Log snapshot creation."""
        self._log_with_extras(
            self._operations_logger,
            logging.DEBUG,
            f"Snapshot created",
            MessageType.SNAPSHOT_CREATED,
            snapshot_id=snapshot_id,
            adapter=adapter,
            entity_count=entity_count,
            **kwargs,
        )
    
    def log_adapter_execution(
        self,
        adapter: str,
        status: str,
        operation: str = "",
        **kwargs: Any,
    ) -> None:
        """Log adapter execution status."""
        self._log_with_extras(
            self._operations_logger,
            logging.INFO,
            f"Adapter {adapter}: {status}",
            MessageType.ADAPTER_EXECUTION,
            adapter=adapter,
            status=status,
            operation=operation,
            **kwargs,
        )
    
    # ========== Rollback Logging ==========
    
    def log_rollback_initiated(
        self,
        operation_id: str,
        adapter: str,
        from_version: str,
        to_version: str,
        user: str = "system",
        reason: str = "",
    ) -> None:
        """Log rollback initiation."""
        self._log_with_extras(
            self._operations_logger,
            logging.INFO,
            f"Rollback initiated",
            MessageType.ROLLBACK_INITIATED,
            operation_id=operation_id,
            adapter=adapter,
            from_version=from_version,
            to_version=to_version,
            user=user,
            reason=reason,
        )
        
        # Also audit
        self._log_with_extras(
            self._audit_logger,
            logging.INFO,
            f"Rollback initiated by {user}",
            MessageType.ROLLBACK_INITIATED,
            operation_id=operation_id,
            adapter=adapter,
            from_version=from_version,
            to_version=to_version,
            reason=reason,
        )
    
    def log_rollback_success(
        self,
        operation_id: str,
        new_version: str,
        state_hash: str,
        duration_ms: int,
    ) -> None:
        """Log rollback success."""
        self._log_with_extras(
            self._operations_logger,
            logging.INFO,
            f"Rollback completed successfully",
            MessageType.ROLLBACK_SUCCESS,
            operation_id=operation_id,
            new_version=new_version,
            state_hash=state_hash[:16],  # Truncate hash
            duration_ms=duration_ms,
        )
    
    def log_rollback_failed(
        self,
        operation_id: str,
        error: str,
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log rollback failure."""
        self._log_with_extras(
            self._errors_logger,
            logging.ERROR,
            f"Rollback failed: {error}",
            MessageType.ROLLBACK_FAILED,
            operation_id=operation_id,
            error=error,
        )
        
        # Log diagnostics separately
        if diagnostics:
            self._log_with_extras(
                self._errors_logger,
                logging.ERROR,
                f"Rollback diagnostics",
                MessageType.DIAGNOSTIC,
                operation_id=operation_id,
                **diagnostics,
            )
    
    # ========== Error Logging ==========
    
    def log_exception(
        self,
        error_type: str,
        message: str,
        adapter: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Log exception."""
        self._log_with_extras(
            self._errors_logger,
            logging.ERROR,
            message,
            MessageType.EXCEPTION,
            error_type=error_type,
            adapter=adapter or "n/a",
            **kwargs,
        )
    
    def log_diagnostic(
        self,
        message: str,
        **kwargs: Any,
    ) -> None:
        """Log diagnostic information."""
        self._log_with_extras(
            self._errors_logger,
            logging.ERROR,
            message,
            MessageType.DIAGNOSTIC,
            **kwargs,
        )
    
    def log_validation_error(
        self,
        entity: str,
        error: str,
        **kwargs: Any,
    ) -> None:
        """Log validation error."""
        self._log_with_extras(
            self._errors_logger,
            logging.ERROR,
            f"Validation failed for {entity}: {error}",
            MessageType.VALIDATION_ERROR,
            entity=entity,
            error=error,
            **kwargs,
        )
    
    # ========== Audit Logging ==========
    
    def log_user_action(
        self,
        user: str,
        action: str,
        adapter: Optional[str] = None,
        version: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Log user action for audit trail."""
        self._log_with_extras(
            self._audit_logger,
            logging.INFO,
            f"User action: {action}",
            MessageType.USER_ACTION,
            user=user,
            action=action,
            adapter=adapter or "n/a",
            version=version or "n/a",
            **kwargs,
        )
    
    def log_scheduled_job(
        self,
        job_id: str,
        trigger: str,
        adapter: str,
        status: str,
        **kwargs: Any,
    ) -> None:
        """Log scheduled job execution."""
        self._log_with_extras(
            self._audit_logger,
            logging.INFO,
            f"Scheduled job: {job_id}",
            MessageType.SCHEDULED_JOB,
            job_id=job_id,
            trigger=trigger,
            adapter=adapter,
            status=status,
            **kwargs,
        )
    
    def log_state_change(
        self,
        entity: str,
        old_state: str,
        new_state: str,
        **kwargs: Any,
    ) -> None:
        """Log state change."""
        self._log_with_extras(
            self._audit_logger,
            logging.INFO,
            f"State change: {entity}",
            MessageType.STATE_CHANGE,
            entity=entity,
            old_state=old_state,
            new_state=new_state,
            **kwargs,
        )
    
    # ========== Log Querying ==========
    
    def get_log_files(self) -> Dict[str, List[Path]]:
        """Get all log files grouped by type."""
        result = {
            "operations": [],
            "errors": [],
            "audit": [],
        }
        
        for log_file in self.logs_dir.iterdir():
            if log_file.is_file() and log_file.suffix == ".log":
                if log_file.name.startswith("operations"):
                    result["operations"].append(log_file)
                elif log_file.name.startswith("errors"):
                    result["errors"].append(log_file)
                elif log_file.name.startswith("audit"):
                    result["audit"].append(log_file)
        
        # Sort by modification time
        for key in result:
            result[key].sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        return result
    
    def read_log(
        self,
        log_type: str,
        lines: int = 100,
        search_pattern: Optional[str] = None,
    ) -> List[str]:
        """
        Read lines from a log file.
        
        Args:
            log_type: Type of log (operations, errors, audit).
            lines: Number of lines to return.
            search_pattern: Optional pattern to filter lines.
            
        Returns:
            List of log lines.
        """
        log_files = self.get_log_files()
        files = log_files.get(log_type, [])
        
        if not files:
            return []
        
        # Read from most recent file
        result = []
        for log_file in files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    file_lines = f.readlines()
                    
                    if search_pattern:
                        file_lines = [
                            line for line in file_lines
                            if search_pattern.lower() in line.lower()
                        ]
                    
                    result.extend(file_lines)
                    
                    if len(result) >= lines:
                        break
            except Exception:
                continue
        
        # Return last N lines
        return result[-lines:]
    
    def cleanup_old_logs(self, retention_days: int = 90) -> int:
        """
        Remove logs older than retention period.
        
        Args:
            retention_days: Number of days to keep logs.
            
        Returns:
            Number of files removed.
        """
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=retention_days)
        removed = 0
        
        for log_file in self.logs_dir.iterdir():
            if log_file.is_file() and log_file.suffix == ".log":
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < cutoff:
                    try:
                        log_file.unlink()
                        removed += 1
                    except Exception:
                        pass
        
        return removed


# ========== Module-level convenience functions ==========

def get_enterprise_logger() -> EnterpriseLogger:
    """Get or create the singleton enterprise logger."""
    global _enterprise_logger
    if _enterprise_logger is None:
        _enterprise_logger = EnterpriseLogger()
    return _enterprise_logger


def log_operation(
    operation_id: str,
    command: str,
    adapter: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Log operation start (convenience function)."""
    get_enterprise_logger().log_operation_start(
        operation_id=operation_id,
        command=command,
        adapter=adapter,
        **kwargs,
    )


def log_error(
    error_type: str,
    message: str,
    adapter: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Log exception (convenience function)."""
    get_enterprise_logger().log_exception(
        error_type=error_type,
        message=message,
        adapter=adapter,
        **kwargs,
    )


def log_audit(
    user: str,
    action: str,
    adapter: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Log user action (convenience function)."""
    get_enterprise_logger().log_user_action(
        user=user,
        action=action,
        adapter=adapter,
        **kwargs,
    )
