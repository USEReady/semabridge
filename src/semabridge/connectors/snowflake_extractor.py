"""
Snowflake Metadata Extractor.

Extracts table, column, and relationship metadata from Snowflake
using INFORMATION_SCHEMA and system functions.

Key improvements over semantic-sync:
1. Batch column extraction (single query for all tables)
2. Foreign key detection via SHOW commands
3. Efficient caching integration
4. Clean separation of concerns
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Optional

import snowflake.connector
from snowflake.connector import SnowflakeConnection

from semabridge.core.settings import SnowflakeConfig
from semabridge.utils.logger import get_logger
from semabridge.utils.cache import MetadataCache

logger = get_logger(__name__)


class ExtractionError(Exception):
    """Raised when metadata extraction fails."""
    pass


class SnowflakeExtractor:
    """
    Extracts metadata from Snowflake for semantic model generation.
    
    Features:
    - Batch table and column extraction
    - Foreign key relationship detection
    - Primary key detection
    - Incremental extraction with caching
    - Custom _SEMANTIC_* table support
    """
    
    def __init__(
        self,
        config: SnowflakeConfig,
        cache: Optional[MetadataCache] = None,
        exclude_tables: Optional[list[str]] = None,
        include_tables: Optional[list[str]] = None,
    ):
        """
        Initialize the extractor.
        
        Args:
            config: Snowflake connection configuration
            cache: Optional metadata cache for incremental extraction
            exclude_tables: Tables to exclude (case-insensitive)
            include_tables: Tables to include (if set, only these are extracted)
        """
        self.config = config
        self.cache = cache
        self.exclude_tables = set(t.upper() for t in (exclude_tables or []))
        self.include_tables = set(t.upper() for t in (include_tables or [])) if include_tables else None
        
        # Extracted data
        self._tables: dict[str, dict[str, Any]] = {}
        self._columns: dict[str, list[dict[str, Any]]] = {}
        self._primary_keys: dict[str, list[str]] = {}
        self._foreign_keys: list[dict[str, Any]] = []
    
    @contextmanager
    def connection(self) -> Generator[SnowflakeConnection, None, None]:
        """Context manager for database connections."""
        conn = None
        try:
            logger.debug(f"Connecting to Snowflake: {self.config.account}")
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
            raise ExtractionError(f"Failed to connect to Snowflake: {e}") from e
        finally:
            if conn:
                conn.close()
    
    def test_connection(self) -> bool:
        """Test Snowflake connectivity."""
        with self.connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()")
            result = cur.fetchone()
            logger.info(f"Connected: {result[1]}.{result[2]} (Warehouse: {result[0]})")
            return True
    
    def extract_all(
        self,
        parallel: bool = False,
        max_workers: int = 5,
    ) -> dict[str, Any]:
        """
        Extract all metadata from Snowflake.
        
        When parallel=True, primary key and foreign key extraction run
        concurrently since they are independent per-table SHOW commands.
        Table/column extraction stays sequential (single batch query).
        
        Args:
            parallel: Enable concurrent PK/FK extraction.
            max_workers: Number of worker threads for parallel mode.

        Returns:
            Dict containing:
            - tables: Dict of table_name -> table metadata
            - columns: Dict of table_name -> list of column metadata
            - primary_keys: Dict of table_name -> list of PK column names
            - foreign_keys: List of FK relationship dicts
        """
        logger.info(f"Starting metadata extraction from {self.config.database}.{self.config.schema_name}")
        
        with self.connection() as conn:
            # Extract tables/columns first (single batch query, already fast)
            self._extract_tables(conn)
            self._extract_columns_batch(conn)
            
            if parallel and len(self._tables) > 1:
                self._extract_pk_fk_parallel(conn, max_workers)
            else:
                self._extract_primary_keys(conn)
                self._extract_foreign_keys(conn)
        
        # Update cache if available
        if self.cache:
            self._update_cache()
        
        result = {
            "database": self.config.database,
            "schema": self.config.schema_name,
            "tables": self._tables,
            "columns": self._columns,
            "primary_keys": self._primary_keys,
            "foreign_keys": self._foreign_keys,
        }
        
        logger.info(
            f"Extraction complete: {len(self._tables)} tables, "
            f"{sum(len(cols) for cols in self._columns.values())} columns, "
            f"{len(self._foreign_keys)} relationships"
        )
        
        return result

    # ------------------------------------------------------------------
    # Semantic View Discovery (Fabric ↔ Snowflake sync)
    # ------------------------------------------------------------------

    def discover_semantic_views(self) -> list[dict[str, Any]]:
        """
        List all semantic views in the configured schema.

        Tries ``SHOW SEMANTIC VIEWS`` first (requires Snowflake Cortex / Enterprise).
        Falls back to ``SHOW VIEWS`` filtered by DDL pattern for environments
        where the native command is unavailable.

        Returns:
            List of dicts with keys: name, schema, database, comment, created_on.
        """
        with self.connection() as conn:
            cur = conn.cursor()
            results: list[dict[str, Any]] = []

            try:
                cur.execute(
                    f'SHOW SEMANTIC VIEWS IN SCHEMA '
                    f'"{self.config.database}"."{self.config.schema_name}"'
                )
                for row in cur.fetchall():
                    # Typical columns: created_on, name, database_name, schema_name, comment
                    name = row[1] if len(row) > 1 else row[0]
                    results.append(
                        {
                            "name": name,
                            "schema": self.config.schema_name,
                            "database": self.config.database,
                            "comment": row[4] if len(row) > 4 else "",
                            "created_on": str(row[0]) if row[0] else None,
                        }
                    )
                logger.info(
                    f"Discovered {len(results)} semantic views via SHOW SEMANTIC VIEWS"
                )
            except Exception as exc:
                logger.warning(
                    f"SHOW SEMANTIC VIEWS not available ({exc}); "
                    "falling back to SHOW VIEWS DDL scan"
                )
                cur.execute(
                    f'SHOW VIEWS IN SCHEMA '
                    f'"{self.config.database}"."{self.config.schema_name}"'
                )
                for row in cur.fetchall():
                    name = row[1] if len(row) > 1 else row[0]
                    # Check comment/definition column for semantic view marker
                    comment = row[4] if len(row) > 4 else ""
                    ddl_col = row[6] if len(row) > 6 else ""
                    is_semantic = (
                        "SEMANTIC VIEW" in str(ddl_col).upper()
                        or "SEMANTIC VIEW" in str(comment).upper()
                    )
                    if is_semantic:
                        results.append(
                            {
                                "name": name,
                                "schema": self.config.schema_name,
                                "database": self.config.database,
                                "comment": comment,
                                "created_on": str(row[0]) if row[0] else None,
                            }
                        )
                logger.info(
                    f"Discovered {len(results)} semantic views via SHOW VIEWS fallback"
                )

        return results

    def extract_semantic_view_ddl(self, view_name: str) -> str:
        """
        Retrieve the DDL of a Snowflake semantic view.

        Uses ``GET_DDL('SEMANTIC_VIEW', <fqn>)``.  This DDL string is the
        input consumed by ``SemanticViewToOSIConverter``.

        Args:
            view_name: Unqualified semantic view name in the configured schema.

        Returns:
            Full DDL string (``CREATE OR REPLACE SEMANTIC VIEW ...``).

        Raises:
            ExtractionError: If the view does not exist or SELECT fails.
        """
        fqn = (
            f'"{self.config.database}"."{self.config.schema_name}"."{view_name}"'
        )
        with self.connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(f"SELECT GET_DDL('SEMANTIC_VIEW', '{fqn}')")
                row = cur.fetchone()
                if not row:
                    raise ExtractionError(
                        f"GET_DDL returned no result for semantic view: {fqn}"
                    )
                ddl: str = row[0]
                logger.debug(
                    f"Retrieved DDL for semantic view '{view_name}' "
                    f"({len(ddl)} chars)"
                )
                return ddl
            except ExtractionError:
                raise
            except Exception as exc:
                raise ExtractionError(
                    f"Failed to retrieve DDL for semantic view '{fqn}': {exc}"
                ) from exc

    def _extract_tables(self, conn: SnowflakeConnection) -> None:
        """Extract table metadata from INFORMATION_SCHEMA."""
        cur = conn.cursor()
        
        logger.debug("Extracting table metadata...")
        cur.execute("""
            SELECT 
                TABLE_NAME,
                TABLE_TYPE,
                ROW_COUNT,
                BYTES,
                LAST_ALTERED,
                COMMENT
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_SCHEMA = %s
              AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
        """, (self.config.schema_name.upper(),))
        
        for row in cur.fetchall():
            table_name = row[0]
            
            # Apply filters
            if table_name.upper() in self.exclude_tables:
                logger.debug(f"Skipping excluded table: {table_name}")
                continue
            
            if self.include_tables and table_name.upper() not in self.include_tables:
                logger.debug(f"Skipping non-included table: {table_name}")
                continue
            
            self._tables[table_name] = {
                "name": table_name,
                "type": row[1],
                "row_count": row[2],
                "bytes": row[3],
                "last_altered": row[4].isoformat() if row[4] else None,
                "description": row[5] or "",
            }
        
        logger.debug(f"Found {len(self._tables)} tables")
    
    def _extract_columns_batch(self, conn: SnowflakeConnection) -> None:
        """
        Extract column metadata for all tables in a single query.
        
        This is much faster than querying per-table.
        """
        if not self._tables:
            return
        
        cur = conn.cursor()
        table_names = list(self._tables.keys())
        
        # Initialize column lists
        for table_name in table_names:
            self._columns[table_name] = []
        
        logger.debug(f"Extracting columns for {len(table_names)} tables...")
        
        # Build placeholders for IN clause
        placeholders = ", ".join(["%s"] * len(table_names))
        
        cur.execute(f"""
            SELECT 
                TABLE_NAME,
                COLUMN_NAME,
                ORDINAL_POSITION,
                DATA_TYPE,
                IS_NULLABLE,
                COLUMN_DEFAULT,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                COMMENT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """, (self.config.schema_name.upper(), *table_names))
        
        for row in cur.fetchall():
            table_name = row[0]
            
            # Build full data type string
            data_type = row[3]
            if row[6]:  # Character length
                data_type = f"{data_type}({row[6]})"
            elif row[7] and row[8]:  # Numeric precision/scale
                data_type = f"{data_type}({row[7]},{row[8]})"
            
            col_info = {
                "name": row[1],
                "ordinal": row[2],
                "data_type": data_type,
                "is_nullable": row[4] == "YES",
                "default_value": row[5],
                "description": row[9] or "",
                "is_key": False,  # Updated by PK extraction
            }
            
            self._columns[table_name].append(col_info)
        
        total_cols = sum(len(cols) for cols in self._columns.values())
        logger.debug(f"Extracted {total_cols} columns")
    
    def _extract_primary_keys(self, conn: SnowflakeConnection) -> None:
        """Extract primary key information using SHOW commands."""
        cur = conn.cursor()
        
        logger.debug("Extracting primary keys...")
        
        for table_name in self._tables:
            try:
                cur.execute(f'SHOW PRIMARY KEYS IN TABLE "{self.config.schema_name}"."{table_name}"')
                pk_cols = []
                for row in cur.fetchall():
                    pk_col = row[4]  # Column name is at index 4
                    pk_cols.append(pk_col)
                    
                    # Mark column as key
                    for col in self._columns.get(table_name, []):
                        if col["name"] == pk_col:
                            col["is_key"] = True
                
                if pk_cols:
                    self._primary_keys[table_name] = pk_cols
            except Exception as e:
                logger.debug(f"Could not get PKs for {table_name}: {e}")
        
        logger.debug(f"Found primary keys for {len(self._primary_keys)} tables")
    
    def _extract_foreign_keys(self, conn: SnowflakeConnection) -> None:
        """Extract foreign key relationships using SHOW commands.
        
        SHOW IMPORTED KEYS output format (index positions):
        0: created_on
        1: pk_database_name
        2: pk_schema_name
        3: pk_table_name
        4: pk_column_name
        5: fk_database_name
        6: fk_schema_name
        7: fk_table_name
        8: fk_column_name
        9: key_sequence
        10: update_rule
        11: delete_rule
        12: fk_name (constraint name)
        13: pk_name
        14: deferrability
        15: rely
        """
        cur = conn.cursor()
        
        logger.debug("Extracting foreign keys...")
        
        for table_name in self._tables:
            try:
                cur.execute(f'SHOW IMPORTED KEYS IN TABLE "{self.config.schema_name}"."{table_name}"')
                for row in cur.fetchall():
                    # Get FK constraint name (index 12), fallback to generated name
                    fk_name = row[12] if len(row) > 12 and row[12] else None
                    
                    # Extract table and column info (correct indices)
                    from_table = row[7]  # fk_table_name
                    from_column = row[8]  # fk_column_name
                    to_table = row[3]     # pk_table_name
                    to_column = row[4]    # pk_column_name
                    
                    # Generate unique name if FK name is missing or is a rule name
                    if not fk_name or fk_name in ("NO ACTION", "CASCADE", "SET NULL", "SET DEFAULT", "RESTRICT"):
                        fk_name = f"FK_{from_table}_{from_column}_{to_table}_{to_column}"
                    
                    fk_info = {
                        "name": fk_name,
                        "from_table": from_table,
                        "from_column": from_column,
                        "to_table": to_table,
                        "to_column": to_column,
                    }
                    self._foreign_keys.append(fk_info)
            except Exception as e:
                logger.debug(f"Could not get FKs for {table_name}: {e}")
        
        logger.debug(f"Found {len(self._foreign_keys)} foreign key relationships")
    
    def _extract_pk_fk_parallel(
        self,
        conn: SnowflakeConnection,
        max_workers: int = 5,
    ) -> None:
        """Extract primary keys and foreign keys concurrently.
        
        Each table's SHOW PRIMARY KEYS and SHOW IMPORTED KEYS commands
        are submitted as independent tasks to a thread pool. Each worker
        creates its own cursor from the shared connection.
        
        Args:
            conn: Open Snowflake connection.
            max_workers: Number of worker threads.
        """
        from concurrent.futures import Future
        from semabridge.utils.concurrency import TracedThreadPoolExecutor
        
        logger.info(
            f"Parallel PK/FK extraction: {len(self._tables)} tables "
            f"with {max_workers} workers"
        )
        
        def _extract_pk_for_table(table_name: str) -> tuple[str, list[str]]:
            """Extract PK columns for a single table (runs in worker thread)."""
            cur = conn.cursor()
            try:
                cur.execute(
                    f'SHOW PRIMARY KEYS IN TABLE '
                    f'"{self.config.schema_name}"."{table_name}"'
                )
                pk_cols = [row[4] for row in cur.fetchall()]
                return table_name, pk_cols
            except Exception as e:
                logger.debug(f"Could not get PKs for {table_name}: {e}")
                return table_name, []
            finally:
                cur.close()
        
        def _extract_fk_for_table(
            table_name: str,
        ) -> tuple[str, list[dict[str, Any]]]:
            """Extract FK relationships for a single table (runs in worker)."""
            cur = conn.cursor()
            fk_list: list[dict[str, Any]] = []
            try:
                cur.execute(
                    f'SHOW IMPORTED KEYS IN TABLE '
                    f'"{self.config.schema_name}"."{table_name}"'
                )
                for row in cur.fetchall():
                    fk_name = row[12] if len(row) > 12 and row[12] else None
                    from_table = row[7]
                    from_column = row[8]
                    to_table = row[3]
                    to_column = row[4]
                    
                    if not fk_name or fk_name in (
                        "NO ACTION", "CASCADE", "SET NULL",
                        "SET DEFAULT", "RESTRICT",
                    ):
                        fk_name = (
                            f"FK_{from_table}_{from_column}_"
                            f"{to_table}_{to_column}"
                        )
                    
                    fk_list.append({
                        "name": fk_name,
                        "from_table": from_table,
                        "from_column": from_column,
                        "to_table": to_table,
                        "to_column": to_column,
                    })
                return table_name, fk_list
            except Exception as e:
                logger.debug(f"Could not get FKs for {table_name}: {e}")
                return table_name, []
            finally:
                cur.close()
        
        table_names = list(self._tables.keys())
        
        with TracedThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all PK and FK queries concurrently
            pk_futures: list[Future[tuple[str, list[str]]]] = [
                executor.submit(_extract_pk_for_table, t) for t in table_names
            ]
            fk_futures: list[Future[tuple[str, list[dict[str, Any]]]]] = [
                executor.submit(_extract_fk_for_table, t) for t in table_names
            ]
            
            # Collect PK results
            for future in pk_futures:
                try:
                    table_name, pk_cols = future.result()
                    if pk_cols:
                        self._primary_keys[table_name] = pk_cols
                        # Mark columns as keys
                        for col in self._columns.get(table_name, []):
                            if col["name"] in pk_cols:
                                col["is_key"] = True
                except Exception as e:
                    logger.warning(f"PK extraction worker failed: {e}")
            
            # Collect FK results
            for future in fk_futures:
                try:
                    _, fk_list = future.result()
                    self._foreign_keys.extend(fk_list)
                except Exception as e:
                    logger.warning(f"FK extraction worker failed: {e}")
        
        logger.debug(
            f"Parallel extraction complete: "
            f"{len(self._primary_keys)} PK tables, "
            f"{len(self._foreign_keys)} FK relationships"
        )
    
    def _update_cache(self) -> None:
        """Update the metadata cache with extracted data."""
        if not self.cache:
            return
        
        for table_name, columns in self._columns.items():
            col_hash = self.cache._compute_hash(columns)
            table_info = self._tables.get(table_name, {})
            
            self.cache.set_table_hash(
                database=self.config.database,
                schema=self.config.schema_name,
                table_name=table_name,
                column_hash=col_hash,
                row_count=table_info.get("row_count", 0),
                last_altered=table_info.get("last_altered"),
            )
    
    def get_table_info(self, table_name: str) -> Optional[dict[str, Any]]:
        """Get metadata for a specific table."""
        return self._tables.get(table_name)
    
    def get_columns(self, table_name: str) -> list[dict[str, Any]]:
        """Get columns for a specific table."""
        return self._columns.get(table_name, [])
    
    def get_primary_key(self, table_name: str) -> list[str]:
        """Get primary key columns for a table."""
        return self._primary_keys.get(table_name, [])
    
    def get_foreign_keys(self) -> list[dict[str, Any]]:
        """Get all foreign key relationships."""
        return self._foreign_keys
    
    def read_semantic_tables(self) -> dict[str, Any]:
        """
        Read existing semantic metadata from _SEMANTIC_* tables.
        
        Returns:
            Dict with 'metadata', 'measures', 'relationships', 'columns' keys
        """
        result = {
            "metadata": [],
            "measures": [],
            "relationships": [],
            "columns": [],
        }
        
        with self.connection() as conn:
            cur = conn.cursor()
            
            # Check which semantic tables exist
            cur.execute("""
                SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES 
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME LIKE '_SEMANTIC%%'
            """, (self.config.schema_name.upper(),))
            
            existing = {row[0] for row in cur.fetchall()}
            
            # Read _SEMANTIC_MEASURES
            if "_SEMANTIC_MEASURES" in existing:
                try:
                    cur.execute("""
                        SELECT MEASURE_NAME, TABLE_NAME, EXPRESSION, DESCRIPTION,
                               DISPLAY_FOLDER, FORMAT_STRING, IS_HIDDEN, DATA_TYPE
                        FROM _SEMANTIC_MEASURES
                    """)
                    for row in cur.fetchall():
                        result["measures"].append({
                            "name": row[0],
                            "table_name": row[1],
                            "expression": row[2],
                            "description": row[3] or "",
                            "folder": row[4],
                            "format_string": row[5],
                            "is_hidden": row[6] or False,
                            "data_type": row[7],
                        })
                    logger.info(f"Found {len(result['measures'])} existing measures")
                except Exception as e:
                    logger.warning(f"Could not read _SEMANTIC_MEASURES: {e}")
            
            # Read _SEMANTIC_RELATIONSHIPS
            if "_SEMANTIC_RELATIONSHIPS" in existing:
                try:
                    cur.execute("""
                        SELECT RELATIONSHIP_NAME, FROM_TABLE, FROM_COLUMN,
                               TO_TABLE, TO_COLUMN, CARDINALITY, IS_ACTIVE
                        FROM _SEMANTIC_RELATIONSHIPS
                    """)
                    for row in cur.fetchall():
                        result["relationships"].append({
                            "name": row[0],
                            "from_table": row[1],
                            "from_column": row[2],
                            "to_table": row[3],
                            "to_column": row[4],
                            "cardinality": row[5] or "many-to-one",
                            "is_active": row[6] if row[6] is not None else True,
                        })
                    logger.info(f"Found {len(result['relationships'])} existing relationships")
                except Exception as e:
                    logger.warning(f"Could not read _SEMANTIC_RELATIONSHIPS: {e}")
            
            # Read _SEMANTIC_COLUMNS for additional column metadata
            if "_SEMANTIC_COLUMNS" in existing:
                try:
                    cur.execute("""
                        SELECT TABLE_NAME, COLUMN_NAME, DISPLAY_NAME, DESCRIPTION,
                               IS_HIDDEN, FORMAT_STRING, FOLDER
                        FROM _SEMANTIC_COLUMNS
                    """)
                    for row in cur.fetchall():
                        result["columns"].append({
                            "table_name": row[0],
                            "column_name": row[1],
                            "display_name": row[2],
                            "description": row[3] or "",
                            "is_hidden": row[4] or False,
                            "format_string": row[5],
                            "folder": row[6],
                        })
                    logger.info(f"Found {len(result['columns'])} column metadata records")
                except Exception as e:
                    logger.warning(f"Could not read _SEMANTIC_COLUMNS: {e}")
        
        return result
