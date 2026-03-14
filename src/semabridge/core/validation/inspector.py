"""
Snowflake Inspector.

Queries Snowflake metadata (INFORMATION_SCHEMA) to inspect the physical state
of the database. Used for validation before synchronization.
"""

from __future__ import annotations

from typing import Dict, List, Set, Any, Optional
import snowflake.connector

from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.logger import get_logger
from semabridge.core.exceptions import ConnectorError

logger = get_logger(__name__)


class SnowflakeInspector:
    """
    Inspects Snowflake physical schema.
    """
    
    def __init__(self, config: SnowflakeConfig):
        self.config = config
        self._connection = None

    def connect(self) -> None:
        """Establish connection to Snowflake."""
        if self._connection:
            return

        try:
            self._connection = snowflake.connector.connect(
                user=self.config.user,
                password=self.config.password.get_secret_value(),
                account=self.config.account,
                warehouse=self.config.warehouse,
                database=self.config.database,
                schema=self.config.schema_name,
                role=self.config.role,
            )
            logger.info(f"Connected to Snowflake inspector: {self.config.account}")
        except Exception as e:
            logger.error(f"Failed to connect to Snowflake: {e}")
            raise ConnectorError(f"Snowflake connection failed: {e}")

    def close(self) -> None:
        """Close connection."""
        if self._connection:
            self._connection.close()
            self._connection = None

    def check_namespace(self) -> bool:
        """
        Verify if the configured database and schema exist.
        """
        self.connect()
        cursor = self._connection.cursor()
        try:
            cursor.execute(f"USE SCHEMA {self.config.database}.{self.config.schema_name}")
            return True
        except Exception as e:
            logger.warning(f"Namespace check failed: {e}")
            return False
        finally:
            cursor.close()

    def get_table_schema(self, table_name: str) -> Dict[str, str]:
        """
        Get column definitions for a specific table.
        
        Args:
            table_name: Unquoted, case-insensitive table name (e.g. "FACT")
            
        Returns:
            Dict[column_name, data_type] - keys are uppercase
        """
        self.connect()
        cursor = self._connection.cursor()
        
        # Sanitize uppercase
        safe_table = table_name.upper().strip('"')
        
        try:
            # Use DESCRIBE for precise type info
            cursor.execute(f'DESC TABLE {self.config.database}.{self.config.schema_name}."{safe_table}"')
            rows = cursor.fetchall()
            
            # Format: name (0), type (1), kind (2), null? (3), default (4), primary key (5), unique key (6), check (7), expression (8), comment (9), policy name (10)
            schema = {}
            for row in rows:
                col_name = row[0].upper()
                col_type = row[1].upper()
                schema[col_name] = col_type
                
            return schema
            
        except Exception as e:
            # Table likely doesn't exist
            logger.debug(f"Could not describe table {safe_table}: {e}")
            return {}
        finally:
            cursor.close()

    def get_all_table_schemas(self) -> Dict[str, Dict[str, str]]:
        """Batch-retrieve column metadata for all tables in the schema.

        Queries ``INFORMATION_SCHEMA.COLUMNS`` once for the configured
        database/schema and returns a nested mapping::

            { TABLE_NAME: { COLUMN_NAME: DATA_TYPE, … }, … }

        This is significantly more efficient than calling
        ``get_table_schema()`` per table when validating an entire model.

        Returns:
            Nested dict keyed by uppercase table name, each value being
            a ``{column_name: data_type}`` mapping.

        Raises:
            ConnectorError: If the Snowflake query fails.
        """
        self.connect()
        cursor = self._connection.cursor()

        query = (
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )

        try:
            cursor.execute(
                query,
                (self.config.database.upper(), self.config.schema_name.upper()),
            )
            rows = cursor.fetchall()

            result: Dict[str, Dict[str, str]] = {}
            for table_name, col_name, data_type in rows:
                table_key = table_name.upper()
                if table_key not in result:
                    result[table_key] = {}
                result[table_key][col_name.upper()] = data_type.upper()

            logger.info(
                f"Retrieved schemas for {len(result)} tables from "
                f"{self.config.database}.{self.config.schema_name}"
            )
            return result

        except Exception as e:
            logger.error(f"Failed to retrieve table schemas: {e}")
            raise ConnectorError(f"Batch schema retrieval failed: {e}")
        finally:
            cursor.close()

    def table_exists(self, table_name: str) -> bool:
        """Check if a table exists."""
        schema = self.get_table_schema(table_name)
        return bool(schema)

    def get_column_sets(self) -> Dict[str, Set]:
        """Batch-retrieve column metadata as sets for validation.

        Similar to :meth:`get_all_table_schemas` but returns sets of
        column names instead of ``{col: type}`` dicts.  This is the
        format expected by ``GlobalValidator.snowflake_metadata``.

        Returns:
            ``{TABLE_NAME: {COL_A, COL_B, …}}``
        """
        full = self.get_all_table_schemas()
        return {
            table: set(cols.keys())
            for table, cols in full.items()
        }

    def semantic_view_exists(self, view_name: str) -> bool:
        """Check whether a Semantic View exists in Snowflake.

        Semantic Views are invisible to ``INFORMATION_SCHEMA.VIEWS``;
        the only discovery mechanism is ``SHOW SEMANTIC VIEWS``.

        Args:
            view_name: The semantic view name to check (case-insensitive).

        Returns:
            ``True`` if the view exists.
        """
        import re as _re

        self.connect()
        cursor = self._connection.cursor()
        try:
            safe = _re.sub(r"[^A-Za-z0-9_]", "_", view_name).upper()
            cursor.execute(f"SHOW SEMANTIC VIEWS LIKE '{safe}'")
            return len(cursor.fetchall()) > 0
        except Exception as exc:
            logger.debug(f"SHOW SEMANTIC VIEWS check failed: {exc}")
            return False
        finally:
            cursor.close()

    def inspector_context(self):
        """Context manager for using the inspector."""
        class InspectorContext:
            def __init__(self, inspector):
                self.inspector = inspector
            
            def __enter__(self):
                self.inspector.connect()
                return self.inspector
            
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.inspector.close()
                
        return InspectorContext(self)
