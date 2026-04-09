"""Connection management, session handling, and retry logic for Snowflake."""

import time
from typing import Any, Optional

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


def open_session(emitter, operation: str = "default") -> None:
    """Open a shared Snowflake session for batch deployments.

    When a session is active, ``deploy()`` reuses the same connection
    instead of opening (and closing) a new one per model.  Call
    ``close_session()`` when the batch is complete.
    """
    if emitter._session_conn is not None:
        return  # already open
    import snowflake.connector

    logger.info(f"Opening Snowflake session for batch deployment: {emitter.config.account}")
    emitter._session_conn = snowflake.connector.connect(
        user=emitter.config.user,
        password=emitter.config.password.get_secret_value(),
        account=emitter.config.account,
        warehouse=emitter._resolve_warehouse(operation),
        database=emitter.config.database,
        schema=emitter.config.schema_name,
        role=emitter.config.role,
        session_parameters={
            "QUERY_TAG": emitter.sf_behavior.query_tag or "Semabridge_Connector"
        },
    )


def close_session(emitter) -> None:
    """Close the shared Snowflake session and reset caches."""
    if emitter._session_conn is not None:
        try:
            emitter._session_conn.close()
        except Exception as exc:
            logger.warning(f"Error closing Snowflake session: {exc}")
        finally:
            emitter._session_conn = None
            emitter._verified_tables.clear()


def execute_with_retry(
    emitter,
    cursor,
    sql: str,
    *,
    max_retries: int = 3,
    base_delay: float = 2.0,
    retryable_codes: tuple = (),
) -> Any:
    """Execute a SQL statement with exponential-backoff retry.

    Retries on transient Snowflake errors such as timeout / load-shedding
    or warehouse-suspended states.  Non-transient errors (syntax,
    missing objects) are raised immediately.
    """
    import snowflake.connector

    # Snowflake error codes considered transient
    _TRANSIENT_CODES = {
        "000625", "000707", "390114",  # timeout, load shedding, auth token expired
        *retryable_codes,
    }
    _TRANSIENT_PHRASES = (
        "timeout",
        "load shedding",
        "semaphore",
        "warehouse",
        "connection reset",
        "broken pipe",
    )

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if attempt == 1:
                logger.info("Executing SQL via retry wrapper:\n%s", emitter._ddl_preview(sql))
            return cursor.execute(sql)
        except snowflake.connector.errors.ProgrammingError as e:
            sql_preview = "\\n".join(sql.splitlines()[:40])
            logger.error(
                "Snowflake ProgrammingError during SQL execution "
                f"(errno={getattr(e, 'errno', 'n/a')}, sqlstate={getattr(e, 'sqlstate', 'n/a')}, "
                f"sfqid={getattr(e, 'sfqid', 'n/a')}): {e}"
            )
            logger.error(f"Failing SQL preview (first 40 lines):\\n{sql_preview}")
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


def fetch_schema_metadata(emitter, cursor) -> dict[str, set]:
    """Query INFORMATION_SCHEMA for all table/column metadata in the schema.

    Returns a mapping suitable for validation:
        { "TABLE_NAME": {"COL_A", "COL_B", …}, … }

    Uses the *existing* cursor so no extra connection is opened.
    Errors are logged and swallowed — the caller falls back gracefully.
    """
    try:
        query = (
            "SELECT TABLE_NAME, COLUMN_NAME "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_CATALOG = %s AND TABLE_SCHEMA = %s "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        emitter._execute_sql(
            cursor,
            query,
            (
                emitter.config.database.upper(),
                emitter.config.schema_name.upper(),
            ),
            context="INFORMATION_SCHEMA metadata",
        )
        rows = cursor.fetchall()

        result: dict[str, set] = {}
        for table_name, col_name in rows:
            key = table_name.upper()
            if key not in result:
                result[key] = set()
            result[key].add(col_name.upper())

        logger.info(
            f"Fetched INFORMATION_SCHEMA metadata for "
            f"{len(result)} tables in "
            f"{emitter.config.database}.{emitter.config.schema_name}"
        )
        return result

    except Exception as exc:
        logger.warning(
            f"Could not fetch INFORMATION_SCHEMA metadata "
            f"(validation will be skipped): {exc}"
        )
        return {}


def check_semantic_view_exists(emitter, cursor, view_name: str) -> bool:
    """Check whether a Semantic View already exists in Snowflake.

    Uses ``SHOW SEMANTIC VIEWS LIKE '…'`` which is the only reliable
    discovery mechanism for Semantic Views.

    Returns ``True`` if the view exists, ``False`` otherwise.
    """
    try:
        import re
        check_name = view_name.strip('"').upper()
        safe_name = re.sub(r"[^A-Za-z0-9_]", "_", check_name).upper()
        
        patterns = [
            safe_name,
            safe_name + "_SEMANTIC" if not safe_name.upper().endswith("_SEMANTIC") else safe_name,
            safe_name + "_semantic" if not safe_name.lower().endswith("_semantic") else safe_name,
        ]
        
        for pattern in patterns:
            emitter._execute_sql(
                cursor,
                f"SHOW SEMANTIC VIEWS LIKE '{pattern}'",
                context="SHOW SEMANTIC VIEWS"
            )
            rows = cursor.fetchall()
            if len(rows) > 0:
                return True
        return False
    except Exception as exc:
        logger.debug(f"SHOW SEMANTIC VIEWS check failed for '{view_name}': {exc}")
        return False
