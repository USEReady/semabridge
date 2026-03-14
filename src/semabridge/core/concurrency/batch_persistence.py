"""
DuckDB batch persistence extensions for concurrent processing.

Provides helper functions that extend DuckDBManager with
batch-level record keeping (batch start / model result / batch
complete) without modifying the original class.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def ensure_batch_tables(db_manager) -> None:
    """Ensure the batch-level tables exist in DuckDB.

    Idempotent — safe to call multiple times.

    Args:
        db_manager: DuckDBManager instance.
    """
    conn = db_manager._get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_runs (
                batch_id VARCHAR PRIMARY KEY,
                started_at TIMESTAMP NOT NULL,
                completed_at TIMESTAMP,
                total_models INTEGER NOT NULL,
                successful_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status VARCHAR NOT NULL DEFAULT 'running',
                worker_count INTEGER,
                execution_mode VARCHAR,
                error_summary VARCHAR
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_model_results (
                id VARCHAR PRIMARY KEY,
                batch_id VARCHAR NOT NULL,
                model_name VARCHAR NOT NULL,
                success BOOLEAN NOT NULL,
                duration_seconds DOUBLE,
                error_message VARCHAR,
                error_type VARCHAR,
                retry_count INTEGER DEFAULT 0,
                snapshot_id VARCHAR,
                step_failed VARCHAR,
                completed_at TIMESTAMP NOT NULL
            )
            """
        )
    finally:
        conn.close()


def record_batch_start(
    db_manager,
    batch_id: str,
    total_models: int,
    worker_count: int,
    execution_mode: str = "best_effort",
) -> None:
    """Record the start of a batch run.

    Args:
        db_manager: DuckDBManager instance.
        batch_id: Unique batch identifier.
        total_models: Number of models to process.
        worker_count: Number of worker processes.
        execution_mode: "best_effort" or "strict".
    """
    conn = db_manager._get_connection()
    try:
        conn.execute(
            """
            INSERT INTO batch_runs (
                batch_id, started_at, total_models,
                worker_count, execution_mode, status
            ) VALUES (?, ?, ?, ?, ?, 'running')
            """,
            [
                batch_id,
                datetime.utcnow(),
                total_models,
                worker_count,
                execution_mode,
            ],
        )
    finally:
        conn.close()


def record_model_result(
    db_manager,
    batch_id: str,
    model_name: str,
    success: bool,
    duration_seconds: float = 0.0,
    error_message: Optional[str] = None,
    error_type: Optional[str] = None,
    retry_count: int = 0,
    snapshot_id: Optional[str] = None,
    step_failed: Optional[str] = None,
) -> None:
    """Record the result of a single model within a batch.

    Args:
        db_manager: DuckDBManager instance.
        batch_id: Parent batch identifier.
        model_name: Name of the processed model.
        success: Whether the model succeeded.
        duration_seconds: Processing duration.
        error_message: Error message if failed.
        error_type: "transient" or "permanent".
        retry_count: Number of retries attempted.
        snapshot_id: DuckDB snapshot ID if available.
        step_failed: Pipeline step that failed.
    """
    conn = db_manager._get_connection()
    try:
        conn.execute(
            """
            INSERT INTO batch_model_results (
                id, batch_id, model_name, success,
                duration_seconds, error_message, error_type,
                retry_count, snapshot_id, step_failed, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                uuid.uuid4().hex[:12],
                batch_id,
                model_name,
                success,
                duration_seconds,
                error_message,
                error_type,
                retry_count,
                snapshot_id,
                step_failed,
                datetime.utcnow(),
            ],
        )
    finally:
        conn.close()


def record_batch_complete(
    db_manager,
    batch_id: str,
    successful_count: int,
    failed_count: int,
    error_summary: Optional[str] = None,
) -> None:
    """Record the completion of a batch run.

    Args:
        db_manager: DuckDBManager instance.
        batch_id: Batch identifier.
        successful_count: Number of models that succeeded.
        failed_count: Number of models that failed.
        error_summary: Optional summary of errors.
    """
    status = "success" if failed_count == 0 else "partial"
    conn = db_manager._get_connection()
    try:
        conn.execute(
            """
            UPDATE batch_runs
            SET completed_at = ?,
                successful_count = ?,
                failed_count = ?,
                status = ?,
                error_summary = ?
            WHERE batch_id = ?
            """,
            [
                datetime.utcnow(),
                successful_count,
                failed_count,
                status,
                error_summary,
                batch_id,
            ],
        )
    finally:
        conn.close()


def get_batch_history(
    db_manager,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Retrieve recent batch run history.

    Args:
        db_manager: DuckDBManager instance.
        limit: Maximum number of records to return.

    Returns:
        List of batch run dictionaries.
    """
    conn = db_manager._get_connection()
    try:
        rows = conn.execute(
            """
            SELECT batch_id, started_at, completed_at,
                   total_models, successful_count, failed_count,
                   status, worker_count, execution_mode
            FROM batch_runs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()

        return [
            {
                "batch_id": r[0],
                "started_at": str(r[1]),
                "completed_at": str(r[2]) if r[2] else None,
                "total_models": r[3],
                "successful_count": r[4],
                "failed_count": r[5],
                "status": r[6],
                "worker_count": r[7],
                "execution_mode": r[8],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_batch_model_results(
    db_manager,
    batch_id: str,
) -> List[Dict[str, Any]]:
    """Retrieve model-level results for a specific batch.

    Args:
        db_manager: DuckDBManager instance.
        batch_id: Batch identifier.

    Returns:
        List of model result dictionaries.
    """
    conn = db_manager._get_connection()
    try:
        rows = conn.execute(
            """
            SELECT model_name, success, duration_seconds,
                   error_message, error_type, retry_count,
                   snapshot_id, step_failed
            FROM batch_model_results
            WHERE batch_id = ?
            ORDER BY completed_at
            """,
            [batch_id],
        ).fetchall()

        return [
            {
                "model_name": r[0],
                "success": r[1],
                "duration_seconds": r[2],
                "error_message": r[3],
                "error_type": r[4],
                "retry_count": r[5],
                "snapshot_id": r[6],
                "step_failed": r[7],
            }
            for r in rows
        ]
    finally:
        conn.close()
