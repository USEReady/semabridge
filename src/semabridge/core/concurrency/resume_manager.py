"""
Resume manager for retrying failed models from partial batches.

Persists batch state (failed models + context) to DuckDB and
enables selective re-processing of only the failed models.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ResumeManager:
    """Enable resuming failed models from partial batch completions.

    Persists the list of failed models and batch context to DuckDB,
    and provides methods to retrieve and re-process them.

    Args:
        db_manager: DuckDBManager instance for persistence.
    """

    def __init__(self, db_manager) -> None:
        self._db = db_manager
        self._ensure_tables()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_batch_state(
        self,
        batch_id: str,
        failed_models: List[str],
        successful_models: List[str],
        context: Dict[str, Any],
    ) -> None:
        """Persist the state of a partial batch completion.

        Args:
            batch_id: Unique identifier for the batch.
            failed_models: List of model names that failed.
            successful_models: List of model names that succeeded.
            context: Serializable configuration context for resume.
        """
        conn = self._db._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO batch_state (
                    batch_id, failed_models, successful_models,
                    context_json, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    batch_id,
                    json.dumps(failed_models),
                    json.dumps(successful_models),
                    json.dumps(context),
                    datetime.now().isoformat(),
                    "partial",
                ],
            )
            logger.info(
                "Saved batch state: %s (%d failed, %d succeeded)",
                batch_id,
                len(failed_models),
                len(successful_models),
            )
        except Exception as exc:
            logger.error("Failed to save batch state %s: %s", batch_id, exc)
            raise

    def get_failed_models(self, batch_id: str) -> List[str]:
        """Retrieve the list of failed models from a previous batch.

        Args:
            batch_id: Batch identifier to look up.

        Returns:
            List of failed model names.

        Raises:
            ValueError: If batch_id is not found.
        """
        conn = self._db._get_connection()
        row = conn.execute(
            "SELECT failed_models FROM batch_state WHERE batch_id = ?",
            [batch_id],
        ).fetchone()

        if row is None:
            raise ValueError(f"Batch not found: {batch_id}")

        return json.loads(row[0])

    def get_batch_context(self, batch_id: str) -> Dict[str, Any]:
        """Retrieve the configuration context for a batch.

        Args:
            batch_id: Batch identifier.

        Returns:
            Deserialized context dictionary.
        """
        conn = self._db._get_connection()
        row = conn.execute(
            "SELECT context_json FROM batch_state WHERE batch_id = ?",
            [batch_id],
        ).fetchone()

        if row is None:
            raise ValueError(f"Batch not found: {batch_id}")

        return json.loads(row[0])

    def link_resume_to_original(
        self,
        original_batch_id: str,
        resume_run_id: str,
    ) -> None:
        """Link a resume operation to its original batch for audit trail.

        Args:
            original_batch_id: The original batch that partially failed.
            resume_run_id: The new run that processes the failed models.
        """
        conn = self._db._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO batch_resume_links (
                    original_batch_id, resume_run_id, created_at
                ) VALUES (?, ?, ?)
                """,
                [
                    original_batch_id,
                    resume_run_id,
                    datetime.now().isoformat(),
                ],
            )
        except Exception as exc:
            logger.warning(
                "Failed to link resume %s → %s: %s",
                resume_run_id,
                original_batch_id,
                exc,
            )

    def mark_batch_complete(self, batch_id: str) -> None:
        """Mark a batch as fully completed (all models succeeded on resume).

        Args:
            batch_id: Batch to mark as complete.
        """
        conn = self._db._get_connection()
        conn.execute(
            "UPDATE batch_state SET status = 'complete' WHERE batch_id = ?",
            [batch_id],
        )
        logger.info("Batch %s marked as complete", batch_id)

    def list_resumable_batches(self) -> List[Dict[str, Any]]:
        """List all batches that have failed models available for resume.

        Returns:
            List of batch info dicts with batch_id, failed count, created_at.
        """
        conn = self._db._get_connection()
        rows = conn.execute(
            """
            SELECT batch_id, failed_models, successful_models, created_at
            FROM batch_state
            WHERE status = 'partial'
            ORDER BY created_at DESC
            """
        ).fetchall()

        return [
            {
                "batch_id": row[0],
                "failed_count": len(json.loads(row[1])),
                "successful_count": len(json.loads(row[2])),
                "created_at": row[3],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        """Create the batch_state and batch_resume_links tables if needed."""
        conn = self._db._get_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_state (
                    batch_id VARCHAR PRIMARY KEY,
                    failed_models VARCHAR NOT NULL,
                    successful_models VARCHAR NOT NULL,
                    context_json VARCHAR NOT NULL,
                    created_at VARCHAR NOT NULL,
                    status VARCHAR NOT NULL DEFAULT 'partial'
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS batch_resume_links (
                    original_batch_id VARCHAR NOT NULL,
                    resume_run_id VARCHAR NOT NULL,
                    created_at VARCHAR NOT NULL,
                    PRIMARY KEY (original_batch_id, resume_run_id)
                )
                """
            )
        except Exception as exc:
            logger.warning("Could not create batch tables: %s", exc)
