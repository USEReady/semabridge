"""
Sentinel Query Monitor.

Polls Snowflake for query execution errors, specifically targeting
compilation failures that affect semantic model deployment.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Generator, Optional
from dataclasses import dataclass

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class QueryFailure:
    """Represents a failed query execution."""
    query_id: str
    query_text: str
    database_name: Optional[str]
    schema_name: Optional[str]
    error_code: int
    error_message: str
    start_time: datetime
    
    @property
    def is_invalid_identifier(self) -> bool:
        """Check if error is invalid identifier (000904)."""
        return self.error_code == 904
        
    @property
    def is_object_not_found(self) -> bool:
        """Check if error is object not found (002003)."""
        # Note: can also be 2001 (Table doesn't exist) in some contexts
        return self.error_code == 2003 or self.error_code == 2001


class QueryMonitor:
    """
    Monitors Snowflake query history for compilation errors.
    
    Focuses on:
    - 000904: Invalid Identifier
    - 002003: Object Not Found / Authorization missing
    """
    
    # Target error codes
    TARGET_ERROR_CODES = (904, 2003, 2001)
    
    def __init__(self, config: SnowflakeConfig):
        """
        Initialize the monitor.
        
        Args:
            config: Snowflake connection configuration
        """
        self.config = config
        self._last_poll_time = datetime.now()
        
    @contextmanager
    def connection(self) -> Generator[SnowflakeConnection, None, None]:
        """Context manager for database connections."""
        conn = None
        try:
            conn = snowflake.connector.connect(
                user=self.config.user,
                password=self.config.password.get_secret_value(),
                account=self.config.account,
                warehouse=self.config.warehouse,
                database=self.config.database,
                schema=self.config.schema_name,
                role=self.config.role,
            )
            yield conn
        except Exception as e:
            logger.error(f"Snowflake connection failed: {e}")
            raise
        finally:
            if conn:
                conn.close()

    def poll_failures(self, window_minutes: int = 15, limit: int = 100) -> list[QueryFailure]:
        """
        Poll for recent query failures.
        
        Uses INFORMATION_SCHEMA.QUERY_HISTORY for near real-time results.
        Note: This returns queries run by the current user, or all users
        if the current role has MONITOR privileges.
        
        Args:
            window_minutes: Look back window in minutes
            limit: Max results to return
            
        Returns:
            List of QueryFailure objects
        """
        failures = []
        
        query = f"""
            SELECT 
                QUERY_ID, 
                QUERY_TEXT, 
                DATABASE_NAME, 
                SCHEMA_NAME, 
                ERROR_CODE, 
                ERROR_MESSAGE, 
                START_TIME 
            FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY(
                END_TIME_RANGE_START => DATEADD(minute, -{window_minutes}, CURRENT_TIMESTAMP()),
                RESULT_LIMIT => {limit}
            ))
            WHERE ERROR_CODE IN ({", ".join(map(str, self.TARGET_ERROR_CODES))})
            ORDER BY START_TIME DESC
        """
        
        try:
            with self.connection() as conn:
                cur = conn.cursor()
                cur.execute(query)
                
                for row in cur.fetchall():
                    failures.append(QueryFailure(
                        query_id=row[0],
                        query_text=row[1],
                        database_name=row[2],
                        schema_name=row[3],
                        error_code=int(row[4]),
                        error_message=row[5],
                        start_time=row[6]
                    ))
                    
            if failures:
                logger.info(f"Found {len(failures)} recent query failures")
                
        except Exception as e:
            logger.error(f"Failed to poll query history: {e}")
            
        return failures
