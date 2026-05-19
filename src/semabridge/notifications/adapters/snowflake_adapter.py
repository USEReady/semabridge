"""
Snowflake adapter for persisting notification events to Snowflake.
Write-only sink for analytics and audit trails.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional
from datetime import datetime

try:
    import snowflake.connector
    from snowflake.connector import OperationalError
except ImportError:
    snowflake = None
    OperationalError = Exception

from ..models import NotificationEvent
from .base import BaseAdapter, AdapterSendError

logger = logging.getLogger(__name__)

# Connection pool: key = (account, database, schema), value = connection
_CONNECTION_POOL: Dict[tuple, Any] = {}


class SnowflakeAdapter(BaseAdapter):
    """
    Persist notification events to Snowflake table.
    This is a write-only sink, not a delivery channel.
    Uses connection pooling and async wrapping via executor.
    """
    
    TIMEOUT_SECONDS = 30
    
    async def send(
        self,
        payload: Dict[str, Any],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Insert a single row into Snowflake.
        
        Args:
            payload: Formatted event dict from formatter
            channel_config: Snowflake connection config
        
        Returns:
            Result dict with success, duration_ms, etc.
        """
        is_valid, error = await self.validate_config(channel_config)
        if not is_valid:
            return {
                "success": False,
                "error": f"Invalid Snowflake config: {error}",
                "duration_ms": 0,
            }
        
        start_time = time.time()
        
        try:
            # Run blocking Snowflake operation in executor
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._insert_row,
                    channel_config,
                    payload
                ),
                timeout=self.TIMEOUT_SECONDS
            )
            
            duration_ms = self._measure_duration(start_time)
            return {
                "success": True,
                "response_code": 200,
                "duration_ms": duration_ms,
            }
        
        except asyncio.TimeoutError:
            duration_ms = self._measure_duration(start_time)
            error_msg = "Snowflake insert timeout"
            logger.error(f"{error_msg} after {self.TIMEOUT_SECONDS}s")
            return {
                "success": False,
                "error": error_msg,
                "duration_ms": duration_ms,
            }
        
        except Exception as e:
            duration_ms = self._measure_duration(start_time)
            error_msg = f"Snowflake insert failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "success": False,
                "error": error_msg,
                "duration_ms": duration_ms,
            }
    
    async def validate_config(self, config: Dict[str, Any]) -> tuple[bool, str]:
        """
        Validate Snowflake configuration.
        
        Args:
            config: Configuration dict
        
        Returns:
            (is_valid, error_message)
        """
        required_keys = ["account", "user", "password", "warehouse", "database", "schema", "table"]
        
        for key in required_keys:
            if not config.get(key):
                return False, f"Missing or empty '{key}'"
        
        # Test connection
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._test_connection,
                    config
                ),
                timeout=self.TIMEOUT_SECONDS
            )
            return True, ""
        
        except asyncio.TimeoutError:
            return False, "Connection timeout"
        
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    async def test_connection(self, config: Dict[str, Any] = None) -> tuple[bool, str]:
        """
        Test Snowflake connection.
        
        Args:
            config: Configuration to test (uses self.config if None)
        
        Returns:
            (is_connected, error_message)
        """
        test_config = config or self.config
        return await self.validate_config(test_config)
    
    async def bulk_insert(
        self,
        rows: list[Dict[str, Any]],
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Insert multiple rows in a single batch.
        Used by analytics_service and replay_service.
        
        Args:
            rows: List of row dicts to insert
            channel_config: Snowflake connection config
        
        Returns:
            {"inserted": n, "failed": 0, "duration_ms": int}
        """
        is_valid, error = await self.validate_config(channel_config)
        if not is_valid:
            return {
                "inserted": 0,
                "failed": len(rows),
                "duration_ms": 0,
                "error": error,
            }
        
        if not rows:
            return {
                "inserted": 0,
                "failed": 0,
                "duration_ms": 0,
            }
        
        start_time = time.time()
        
        try:
            loop = asyncio.get_event_loop()
            inserted_count = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._bulk_insert_rows,
                    channel_config,
                    rows
                ),
                timeout=self.TIMEOUT_SECONDS
            )
            
            duration_ms = self._measure_duration(start_time)
            return {
                "inserted": inserted_count,
                "failed": len(rows) - inserted_count,
                "duration_ms": duration_ms,
            }
        
        except asyncio.TimeoutError:
            duration_ms = self._measure_duration(start_time)
            return {
                "inserted": 0,
                "failed": len(rows),
                "duration_ms": duration_ms,
                "error": "Bulk insert timeout",
            }
        
        except Exception as e:
            duration_ms = self._measure_duration(start_time)
            error_msg = f"Bulk insert failed: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                "inserted": 0,
                "failed": len(rows),
                "duration_ms": duration_ms,
                "error": error_msg,
            }
    
    @staticmethod
    def _get_connection(config: Dict[str, Any]) -> Any:
        """
        Get or create a Snowflake connection from the pool.
        Reconnects on OperationalError.
        
        Args:
            config: Connection config
        
        Returns:
            Snowflake connection object
        """
        if snowflake is None:
            raise ImportError("snowflake-connector-python not installed")
        
        pool_key = (
            config.get("account"),
            config.get("database"),
            config.get("schema")
        )
        
        # Try to get from pool
        if pool_key in _CONNECTION_POOL:
            try:
                conn = _CONNECTION_POOL[pool_key]
                # Test the connection
                conn.cursor().execute("SELECT 1").fetchone()
                return conn
            except OperationalError:
                # Connection is stale, remove from pool
                logger.warning(f"Removing stale Snowflake connection from pool: {pool_key}")
                del _CONNECTION_POOL[pool_key]
        
        # Create new connection
        conn = snowflake.connector.connect(
            account=config.get("account"),
            user=config.get("user"),
            password=config.get("password"),
            warehouse=config.get("warehouse"),
            database=config.get("database"),
            schema=config.get("schema"),
        )
        
        _CONNECTION_POOL[pool_key] = conn
        return conn
    
    @staticmethod
    def _insert_row(config: Dict[str, Any], row: Dict[str, Any]) -> None:
        """
        Insert a single row into Snowflake.
        
        Args:
            config: Connection config
            row: Row data dict
        """
        conn = SnowflakeAdapter._get_connection(config)
        cursor = conn.cursor()
        
        try:
            table = config.get("table", "NOTIFICATION_EVENTS")
            columns = ", ".join(row.keys())
            placeholders = ", ".join(["%s"] * len(row))
            values = tuple(row.values())
            
            query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
            cursor.execute(query, values)
            cursor.close()
        
        except Exception:
            if cursor:
                cursor.close()
            raise
    
    @staticmethod
    def _bulk_insert_rows(config: Dict[str, Any], rows: list[Dict[str, Any]]) -> int:
        """
        Bulk insert rows into Snowflake.
        
        Args:
            config: Connection config
            rows: List of row dicts
        
        Returns:
            Number of rows inserted
        """
        if not rows:
            return 0
        
        conn = SnowflakeAdapter._get_connection(config)
        cursor = conn.cursor()
        
        try:
            table = config.get("table", "NOTIFICATION_EVENTS")
            # All rows should have the same columns
            columns = list(rows[0].keys())
            columns_str = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))
            query = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"
            
            # Prepare data tuples
            data_tuples = [
                tuple(row.get(col) for col in columns)
                for row in rows
            ]
            
            # Execute with batching
            cursor.executemany(query, data_tuples)
            cursor.close()
            
            return len(rows)
        
        except Exception:
            if cursor:
                cursor.close()
            raise
    
    @staticmethod
    def _test_connection(config: Dict[str, Any]) -> None:
        """
        Test Snowflake connection.
        
        Args:
            config: Connection config
        """
        conn = SnowflakeAdapter._get_connection(config)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT 1")
            cursor.close()
        except Exception:
            if cursor:
                cursor.close()
            raise
