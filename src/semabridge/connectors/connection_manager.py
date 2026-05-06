import time
import re
import snowflake.connector
from typing import Any, Dict, List, Optional
from semabridge.utils.logger import get_logger
from semabridge.core.exceptions import ConnectorError
from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

logger = get_logger(__name__)

class SnowflakeConnectionManager:
    def __init__(self, config, behavior):
        self.config = config
        self.behavior = behavior
        self.sf_behavior = behavior.snowflake
        self._connection = None
        self._session_conn = None
        self._verified_tables = set()

    @property
    def connection(self):
        """Returns either the active session connection or the standalone connection."""
        return self._session_conn or self._connection

    def get_connection(self) -> tuple[Any, bool]:
        """
        Get a connection and a flag indicating if the caller owns it.
        
        Returns:
            (connection, owns_connection)
        """
        if self._session_conn is not None:
            logger.debug("Reusing shared Snowflake session connection")
            return self._session_conn, False

        logger.info(f"Connecting to Snowflake: {self.config.account}")
        kwargs = get_snowflake_connect_kwargs(self.config)
        kwargs["session_parameters"] = {
            "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_Connector"
        }
        conn = snowflake.connector.connect(**kwargs)
        self._ensure_session_context(conn)
        return conn, True

    def authenticate(self) -> None:
        """Establish connection to Snowflake."""
        kwargs = get_snowflake_connect_kwargs(self.config)
        self._connection = snowflake.connector.connect(**kwargs)
        self._ensure_session_context(self._connection)

    def discover(self) -> Dict[str, Any]:
        """List tables and views in the schema."""
        if not self._connection:
            self.authenticate()
        
        cur = self._connection.cursor()
        db_name, schema_name = self._resolved_db_schema()
        self._execute_sql(
            cur,
            f"SHOW TABLES IN SCHEMA {self._quote_ident(db_name)}.{self._quote_ident(schema_name)}",
            context="SHOW TABLES",
        )
        tables = [row[1] for row in cur.fetchall()]
        
        self._execute_sql(
            cur,
            f"SHOW VIEWS IN SCHEMA {self._quote_ident(db_name)}.{self._quote_ident(schema_name)}",
            context="SHOW VIEWS",
        )
        views = [row[1] for row in cur.fetchall()]
        
        return {"tables": tables, "views": views}

    def validate_permissions(self) -> List[str]:
        """
        Validate Snowflake RBAC permissions.
        Required: USAGE on DB, USAGE on SCHEMA, CREATE SEMANTIC VIEW on SCHEMA.
        """
        warnings = []
        if not self._connection:
            self.authenticate()
            
        cur = self._connection.cursor()
        try:
            # Check USAGE on Schema
            self._execute_sql(cur, f"USE SCHEMA {self.config.database}.{self.config.schema_name}", context="USE SCHEMA")
            
            # Check CREATE SEMANTIC VIEW privilege
            self._execute_sql(cur, "SELECT current_role()", context="SELECT current_role()")
            role = cur.fetchone()[0]
            logger.info(f"Validating permissions for role: {role}")
            
        except Exception as e:
            logger.error(f"Snowflake RBAC check failed: {e}")
            raise ConnectorError(f"Snowflake RBAC validation failed: {e}")
            
        return warnings

    def validate_target(self) -> bool:
        """Check if Snowflake is reachable."""
        try:
            self.authenticate()
            return True
        except Exception:
            return False

    def _resolve_warehouse(self, operation: str = "default") -> str:
        """Mandate 5: Resolve warehouse name based on operation type."""
        mapping = getattr(self.sf_behavior, "warehouse_mapping", None) or {}
        return mapping.get(operation, self.config.warehouse)

    def open_session(self, operation: str = "default") -> None:
        """Open a shared Snowflake session for batch deployments."""
        if self._session_conn is not None:
            return  # already open

        logger.info(f"Opening Snowflake session for batch deployment: {self.config.account}")
        kwargs = get_snowflake_connect_kwargs(self.config)
        # Override warehouse if operation-specific mapping exists
        resolved_wh = self._resolve_warehouse(operation)
        if resolved_wh:
            kwargs["warehouse"] = resolved_wh
        kwargs["session_parameters"] = {
            "QUERY_TAG": self.sf_behavior.query_tag or "Semabridge_Connector"
        }
        self._session_conn = snowflake.connector.connect(**kwargs)
        self._ensure_session_context(self._session_conn)

    def close_session(self) -> None:
        """Close the shared Snowflake session and reset caches."""
        if self._session_conn is not None:
            try:
                self._session_conn.close()
            except Exception as exc:
                logger.warning(f"Error closing Snowflake session: {exc}")
            finally:
                self._session_conn = None
                self._verified_tables.clear()

    def _execute_sql(
        self,
        cursor,
        sql: str,
        params: Optional[tuple[Any, ...]] = None,
        *,
        context: str = "SQL",
    ) -> Any:
        """Log and execute a Snowflake statement in one place.

        This wrapper adds richer session context to logs and provides
        targeted hints for common failures (e.g. SHOW TABLES against a
        missing schema or insufficient RBAC privileges).
        """
        # Best-effort: collect basic session config to help debugging
        session_info = None
        try:
            session_info = {
                "account": getattr(self.config, "account", None),
                "database": getattr(self.config, "database", None) or getattr(self.config, "database_name", None) or getattr(self.config, "db", None),
                "schema": getattr(self.config, "schema_name", None) or getattr(self.config, "schema", None),
                "warehouse": getattr(self.config, "warehouse", None),
            }
        except Exception:
            session_info = None

        if session_info:
            logger.info("%s: session=%s\n%s", context, session_info, self._ddl_preview(sql))
        else:
            logger.info("%s:\n%s", context, self._ddl_preview(sql))

        try:
            if params is None:
                result = cursor.execute(sql)
            else:
                result = cursor.execute(sql, params)
            return result
        except Exception as exc:
            invalid_identifier = self._extract_invalid_identifier(exc)
            base_msg = f"{context} failed: {exc}"
            if invalid_identifier:
                base_msg = f"{base_msg} [invalid_identifier={invalid_identifier}]"

            # Add targeted hints for common SHOW TABLES/schema errors
            hint = None
            try:
                lower_sql = str(sql or "").lower()
                if "show tables" in lower_sql or "show views" in lower_sql:
                    hint = (
                        "Verify that the target database/schema exists and the active role has "
                        "USAGE on the database and schema. Also ensure the configured warehouse is set and running."
                    )
                elif "use schema" in lower_sql:
                    hint = "Verify the schema exists and current role has USAGE on the database and schema."
            except Exception:
                hint = None

            message = base_msg
            if hint:
                message = f"{message} -- HINT: {hint}"

            logger.error(message, exc_info=True)
            raise ConnectorError(message) from exc

    def _execute_with_retry(
        self,
        cursor,
        sql: str,
        *,
        max_retries: int = 3,
        base_delay: float = 2.0,
        retryable_codes: tuple = (),
    ) -> Any:
        """Execute a SQL statement with exponential-backoff retry."""
        # Snowflake error codes considered transient:
        _TRANSIENT_CODES = {
            "000625", "000707", "390114",
            *retryable_codes,
        }
        _TRANSIENT_PHRASES = (
            "timeout", "load shedding", "semaphore", "warehouse",
            "connection reset", "broken pipe",
        )

        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                if attempt == 1:
                    logger.info("Executing SQL via retry wrapper:\n%s", self._ddl_preview(sql))
                return cursor.execute(sql)
            except snowflake.connector.errors.ProgrammingError as e:
                sql_preview = "\n".join(sql.splitlines()[:40])
                logger.error(
                    "Snowflake ProgrammingError during SQL execution "
                    f"(errno={getattr(e, 'errno', 'n/a')}, sqlstate={getattr(e, 'sqlstate', 'n/a')}, "
                    f"sfqid={getattr(e, 'sfqid', 'n/a')}): {e}"
                )
                logger.error(f"Failing SQL preview (first 40 lines):\n{sql_preview}")
                raise
            except snowflake.connector.errors.DatabaseError as e:
                err_msg = str(e).lower()
                err_code = getattr(e, "errno", None) or getattr(e, "sfqid", "")
                is_transient = (
                    str(err_code) in _TRANSIENT_CODES
                    or any(p in err_msg for p in _TRANSIENT_PHRASES)
                )
                if not is_transient or attempt == max_retries:
                    raise
                last_exc = e
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"Transient Snowflake error (attempt {attempt}/{max_retries}), "
                    f"retrying in {delay:.0f}s: {e}"
                )
                time.sleep(delay)
            except Exception as e:
                err_msg = str(e).lower()
                if any(p in err_msg for p in _TRANSIENT_PHRASES) and attempt < max_retries:
                    last_exc = e
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"Transient error (attempt {attempt}/{max_retries}), "
                        f"retrying in {delay:.0f}s: {e}"
                    )
                    time.sleep(delay)
                else:
                    raise
        raise last_exc

    @staticmethod
    def _ddl_preview(sql: str, *, max_lines: int = 30) -> str:
        """Return a compact preview of a DDL statement for terminal tracing."""
        lines = sql.splitlines()
        if len(lines) <= max_lines:
            return "\n".join(lines)
        preview = "\n".join(lines[:max_lines])
        return f"{preview}\n... ({len(lines) - max_lines} more lines)"

    @staticmethod
    def _extract_invalid_identifier(exc: Exception) -> Optional[str]:
        """Extract invalid identifier token from Snowflake error text."""
        match = re.search(
            r"invalid identifier '([^']+)'",
            str(exc or ""),
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    @staticmethod
    def _quote_ident(value: str) -> str:
        raw = str(value or "").replace('"', '""')
        return f'"{raw}"'

    def _resolved_db_schema(self) -> tuple[str, str]:
        """Resolve DB/SCHEMA, handling accidental 'DB.SCHEMA' in schema_name."""
        db_name = str(getattr(self.config, "database", "") or "").strip()
        schema_name = str(getattr(self.config, "schema_name", "") or "").strip()
        if "." in schema_name and db_name:
            left, right = schema_name.split(".", 1)
            if left.strip().upper() == db_name.upper():
                schema_name = right.strip()
        return db_name, schema_name

    def _ensure_session_context(self, conn: Any) -> None:
        """Explicitly set warehouse/database/schema for this session."""
        db_name, schema_name = self._resolved_db_schema()
        warehouse = str(getattr(self.config, "warehouse", "") or "").strip()
        if not db_name or not schema_name:
            return

        cur = conn.cursor()
        try:
            if warehouse:
                self._execute_sql(
                    cur,
                    f"USE WAREHOUSE {self._quote_ident(warehouse)}",
                    context="USE WAREHOUSE",
                )
            
            # Try to use the database - if it fails, provide helpful diagnostics
            try:
                self._execute_sql(
                    cur,
                    f"USE DATABASE {self._quote_ident(db_name)}",
                    context="USE DATABASE",
                )
                logger.info(f"✓ Successfully connected to database: {db_name}")
            except Exception as db_error:
                error_msg = str(db_error).lower()
                
                # Check if database exists
                if "object does not exist" in error_msg or "does not exist" in error_msg:
                    logger.error(f"✗ Cannot USE DATABASE {db_name}")
                    logger.error(f"  Error: {db_error}")
                    
                    # Try to list available databases for debugging
                    try:
                        self._execute_sql(cur, "SHOW DATABASES", context="SHOW DATABASES")
                        available_dbs = [row[1] for row in cur.fetchall()]
                        logger.error(f"  Available databases: {', '.join(available_dbs)}")
                    except:
                        pass
                    
                    raise Exception(
                        f"Database '{db_name}' not found or not accessible. "
                        f"Ensure it exists and user '{self.config.user}' has USAGE privilege."
                    ) from db_error
                
                # Check if it's a permissions issue
                elif "permission" in error_msg or "insufficient" in error_msg or "denied" in error_msg:
                    logger.error(f"✗ Permission denied to USE DATABASE {db_name}")
                    logger.error(f"  User: {self.config.user}")
                    logger.error(f"  Error: {db_error}")
                    
                    raise Exception(
                        f"Permission denied accessing database '{db_name}'. "
                        f"User '{self.config.user}' needs USAGE privilege. "
                        f"Run in Snowflake:\n"
                        f"  GRANT USAGE ON DATABASE {db_name} TO USER {self.config.user};\n"
                        f"  GRANT USAGE ON SCHEMA {db_name}.{schema_name} TO USER {self.config.user};"
                    ) from db_error
                
                else:
                    raise
            
            self._execute_sql(
                cur,
                f"USE SCHEMA {self._quote_ident(db_name)}.{self._quote_ident(schema_name)}",
                context="USE SCHEMA",
            )
            logger.info(f"✓ Successfully set schema: {db_name}.{schema_name}")
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _database_exists(self, cur: Any, db_name: str) -> bool:
        """Check if a database exists in Snowflake."""
        try:
            self._execute_sql(
                cur,
                f"SHOW DATABASES LIKE '{db_name}'",
                context="SHOW DATABASES",
            )
            result = cur.fetchall()
            exists = len(result) > 0
            
            if exists:
                logger.info(f"✓ Database '{db_name}' found in Snowflake")
            else:
                logger.warning(f"✗ Database '{db_name}' NOT found in Snowflake")
                logger.info("Available databases:")
                self._execute_sql(cur, "SHOW DATABASES", context="SHOW DATABASES")
                for row in cur.fetchall():
                    logger.info(f"  - {row[1]}")  # Database name is typically in column 1
            
            return exists
        except Exception as e:
            logger.error(f"Error checking database existence: {e}")
            return False

    def test_connection(self) -> Dict[str, Any]:
        """
        Test Snowflake connection and permissions.
        Returns diagnostic information.
        """
        logger.info("=" * 60)
        logger.info("Testing Snowflake Connection & Permissions")
        logger.info("=" * 60)
        
        result = {
            "status": "unknown",
            "connection": False,
            "warehouse": False,
            "database": False,
            "schema": False,
            "errors": []
        }
        
        try:
            # Test basic connection
            logger.info(f"1. Testing connection to {self.config.account}...")
            kwargs = get_snowflake_connect_kwargs(self.config)
            conn = snowflake.connector.connect(**kwargs)
            result["connection"] = True
            logger.info("   ✓ Connection successful")
            
            cur = conn.cursor()
            
            # Test warehouse access
            warehouse = str(getattr(self.config, "warehouse", "") or "").strip()
            if warehouse:
                logger.info(f"2. Testing warehouse access: {warehouse}")
                try:
                    self._execute_sql(cur, f"USE WAREHOUSE {self._quote_ident(warehouse)}", context="USE WAREHOUSE")
                    result["warehouse"] = True
                    logger.info(f"   ✓ Warehouse '{warehouse}' accessible")
                except Exception as e:
                    result["warehouse"] = False
                    result["errors"].append(f"Warehouse error: {e}")
                    logger.error(f"   ✗ Warehouse error: {e}")
            
            # Test database access
            db_name, schema_name = self._resolved_db_schema()
            logger.info(f"3. Testing database access: {db_name}")
            try:
                self._execute_sql(cur, f"USE DATABASE {self._quote_ident(db_name)}", context="USE DATABASE")
                result["database"] = True
                logger.info(f"   ✓ Database '{db_name}' accessible")
            except Exception as e:
                result["database"] = False
                result["errors"].append(f"Database error: {e}")
                logger.error(f"   ✗ Database error: {e}")
                logger.info("   Attempting to list available databases...")
                try:
                    self._execute_sql(cur, "SHOW DATABASES", context="SHOW DATABASES")
                    dbs = [row[1] for row in cur.fetchall()]
                    logger.info(f"   Available: {', '.join(dbs)}")
                except:
                    pass
            
            # Test schema access
            logger.info(f"4. Testing schema access: {schema_name}")
            try:
                self._execute_sql(cur, f"USE SCHEMA {self._quote_ident(db_name)}.{self._quote_ident(schema_name)}", context="USE SCHEMA")
                result["schema"] = True
                logger.info(f"   ✓ Schema '{db_name}.{schema_name}' accessible")
            except Exception as e:
                result["schema"] = False
                result["errors"].append(f"Schema error: {e}")
                logger.error(f"   ✗ Schema error: {e}")
            
            cur.close()
            conn.close()
            
            # Determine overall status
            if all([result["connection"], result["database"], result["schema"]]):
                result["status"] = "success"
                logger.info("\n✓ All tests passed! Ready to deploy.")
            else:
                result["status"] = "failed"
                logger.error("\n✗ Some tests failed. See errors above.")
            
        except Exception as e:
            result["status"] = "error"
            result["errors"].append(f"Connection test failed: {e}")
            logger.error(f"Connection test error: {e}")
        
        logger.info("=" * 60)
        return result

    def _create_database_if_missing(self, cur: Any, db_name: str) -> None:
        """Auto-create database if it doesn't exist."""
        try:
            self._execute_sql(
                cur,
                f"CREATE DATABASE IF NOT EXISTS {self._quote_ident(db_name)}",
                context="CREATE DATABASE",
            )
            logger.info(f"Successfully created database: {db_name}")
        except Exception as e:
            raise Exception(
                f"Failed to auto-create database '{db_name}': {e}. "
                f"Ensure your Snowflake user has CREATE DATABASE permission."
            ) from e
