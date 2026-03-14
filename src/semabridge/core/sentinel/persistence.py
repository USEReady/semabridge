"""
Sentinel Persistence Layer.

Provides a local SQLite-based store for tracking query failures,
enabling offline diagnosis and fix tracking.
"""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
from dataclasses import dataclass

from semabridge.utils.logger import get_logger
from semabridge.core.sentinel.monitor import QueryFailure

logger = get_logger(__name__)

class FailureStore:
    """
    Persists query failures to a local SQLite database.
    """
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Store DB in the project root by default
            project_root = Path(__file__).resolve().parent.parent.parent.parent
            db_path = str(project_root / "semabridge_sentinel.db")
            
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        """Initialize database schema."""
        conn = self._get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS failures (
                    query_id TEXT PRIMARY KEY,
                    query_text TEXT,
                    database_name TEXT,
                    schema_name TEXT,
                    error_code INTEGER,
                    error_message TEXT,
                    start_time TIMESTAMP,
                    status TEXT DEFAULT 'DETECTED',
                    fix_query TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def add_failure(self, failure: QueryFailure) -> bool:
        """
        Add a failure to the store.
        Returns True if added, False if already exists.
        """
        conn = self._get_connection()
        try:
            # Check if exists
            exists = conn.execute("SELECT 1 FROM failures WHERE query_id = ?", (failure.query_id,)).fetchone()
            if exists:
                return False

            conn.execute("""
                INSERT INTO failures (
                    query_id, query_text, database_name, schema_name, 
                    error_code, error_message, start_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                failure.query_id,
                failure.query_text,
                failure.database_name,
                failure.schema_name,
                failure.error_code,
                failure.error_message,
                failure.start_time
            ))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to add failure {failure.query_id}: {e}")
            return False
        finally:
            conn.close()

    def get_failure(self, query_id: str) -> Optional[QueryFailure]:
        """Retrieve a failure by ID."""
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT query_id, query_text, database_name, schema_name, 
                       error_code, error_message, start_time
                FROM failures
                WHERE query_id = ?
            """, (query_id,)).fetchone()

            if not row:
                return None

            return QueryFailure(
                query_id=row[0],
                query_text=row[1],
                database_name=row[2],
                schema_name=row[3],
                error_code=row[4],
                error_message=row[5],
                start_time=row[6] # SQLite returns string/timestamp, might need conversion if QueryFailure expects datetime
            )
        except Exception as e:
            logger.error(f"Failed to get failure {query_id}: {e}")
            return None
        finally:
            conn.close()

    def get_failure_status(self, query_id: str) -> Optional[dict]:
        """Get status and fix details for a failure."""
        conn = self._get_connection()
        try:
            row = conn.execute("""
                SELECT status, fix_query
                FROM failures
                WHERE query_id = ?
            """, (query_id,)).fetchone()

            if not row:
                return None
            
            return {
                "status": row[0],
                "fix_query": row[1]
            }
        except Exception as e:
            logger.error(f"Failed to get failure status {query_id}: {e}")
            return None
        finally:
            conn.close()

    def update_status(self, query_id: str, status: str, fix_query: str = None) -> bool:
        """Update the status of a failure."""
        conn = self._get_connection()
        try:
            if fix_query:
                conn.execute("""
                    UPDATE failures 
                    SET status = ?, fix_query = ?
                    WHERE query_id = ?
                """, (status, fix_query, query_id))
            else:
                conn.execute("""
                    UPDATE failures 
                    SET status = ?
                    WHERE query_id = ?
                """, (status, query_id))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update status for {query_id}: {e}")
            return False
        finally:
            conn.close()
