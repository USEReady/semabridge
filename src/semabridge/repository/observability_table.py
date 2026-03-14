"""
Snowflake Observability Table
==============================

Manages the ``SEMABRIDGE_RUNS`` table in Snowflake, which stores a JSON
snapshot of every ``RunSummary`` produced by the pipeline.  This powers
cross-run dashboards and SLA reporting directly inside the customer's
Snowflake account.

Only active when ``SnowflakeConfig.push_run_summary_to_snowflake = True``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, Optional

from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_ENSURE_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {fq_table} (
    run_id          VARCHAR(64)   NOT NULL,
    project_id      VARCHAR(256),
    source_type     VARCHAR(64),
    target_type     VARCHAR(64),
    status          VARCHAR(32),
    started_at      TIMESTAMP_TZ,
    finished_at     TIMESTAMP_TZ,
    duration_s      FLOAT,
    steps_json      VARIANT,
    errors_json     VARIANT,
    summary_json    VARIANT,
    semabridge_ver  VARCHAR(32),
    inserted_at     TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP()
)
"""

_INSERT_SQL = """
INSERT INTO {fq_table} (
    run_id, project_id, source_type, target_type, status,
    started_at, finished_at, duration_s,
    steps_json, errors_json, summary_json, semabridge_ver
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    PARSE_JSON(%s), PARSE_JSON(%s), PARSE_JSON(%s), %s
)
"""

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _semabridge_version() -> str:
    try:
        import importlib.metadata
        return importlib.metadata.version("semabridge")
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ObservabilityTable:
    """
    Manages the ``SEMABRIDGE_RUNS`` Snowflake observability table.

    Usage::

        obs = ObservabilityTable(snowflake_config)
        obs.ensure_table()
        obs.insert_run_summary(run_summary)
    """

    TABLE_NAME = "SEMABRIDGE_RUNS"

    def __init__(self, snowflake_config: Any) -> None:
        """
        Args:
            snowflake_config: A ``SnowflakeConfig`` instance.
        """
        self._cfg = snowflake_config

    # -------------------------------------------------------------------------
    @property
    def _fq_table(self) -> str:
        return (
            f"{self._cfg.database}.{self._cfg.schema_name}.{self.TABLE_NAME}"
        )

    # -------------------------------------------------------------------------
    def _connect(self):
        """Open a fresh Snowflake connection (caller must close)."""
        import snowflake.connector  # deferred — optional dependency

        return snowflake.connector.connect(
            user=self._cfg.user,
            password=self._cfg.password.get_secret_value(),
            account=self._cfg.account,
            warehouse=self._cfg.warehouse,
            database=self._cfg.database,
            schema=self._cfg.schema_name,
            role=self._cfg.role,
            session_parameters={"QUERY_TAG": "SemaBridge_Observability"},
        )

    # -------------------------------------------------------------------------
    def ensure_table(self) -> None:
        """
        Create ``SEMABRIDGE_RUNS`` if it does not already exist.

        Idempotent — safe to call on every run.
        """
        ddl = _ENSURE_TABLE_DDL.format(fq_table=self._fq_table)
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(ddl)
                logger.info(
                    f"Observability table ready: {self._fq_table}"
                )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning(
                f"Could not ensure observability table {self._fq_table}: {exc}"
            )

    # -------------------------------------------------------------------------
    def insert_run_summary(self, run_summary: Any) -> bool:
        """
        Insert a ``RunSummary`` record into ``SEMABRIDGE_RUNS``.

        Args:
            run_summary: A ``RunSummary`` (or dict-serialisable object).

        Returns:
            True on success, False on error (non-fatal).
        """
        try:
            summary_dict = _to_dict(run_summary)
            row = _extract_row(summary_dict)

            conn = self._connect()
            try:
                cur = conn.cursor()
                # Ensure table exists (idempotent)
                cur.execute(
                    _ENSURE_TABLE_DDL.format(fq_table=self._fq_table)
                )
                cur.execute(
                    _INSERT_SQL.format(fq_table=self._fq_table),
                    row,
                )
                logger.info(
                    f"Run summary inserted into {self._fq_table}: "
                    f"run_id={row[0]}, status={row[4]}"
                )
            finally:
                conn.close()
            return True

        except ImportError:
            logger.debug(
                "snowflake-connector-python not installed — "
                "skipping Snowflake observability insert."
            )
            return False
        except Exception as exc:
            logger.warning(
                f"Failed to insert run summary into {self._fq_table}: {exc}"
            )
            return False


# ---------------------------------------------------------------------------
# Internal serialisation helpers
# ---------------------------------------------------------------------------

def _to_dict(obj: Any) -> Dict[str, Any]:
    """Convert RunSummary (Pydantic or dataclass) to plain dict."""
    if isinstance(obj, dict):
        return obj
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    # Pydantic v1
    if hasattr(obj, "dict"):
        return obj.dict()
    # Dataclass / namedtuple fallback
    try:
        import dataclasses
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
    except Exception:
        pass
    # Last resort
    return vars(obj) if hasattr(obj, "__dict__") else {}


def _extract_row(d: Dict[str, Any]) -> tuple:
    """Extract INSERT parameter tuple from a RunSummary dict."""
    run_id = d.get("run_id", "")
    project_id = d.get("project_id", "")
    source_type = d.get("source_type", "")
    target_type = d.get("target_type", "")
    status = _status_str(d.get("status"))
    started_at = _ts(d.get("started_at"))
    finished_at = _ts(d.get("finished_at"))
    duration_s = d.get("duration_seconds") or d.get("duration_s")
    steps_json = json.dumps(d.get("steps_completed") or d.get("steps") or [])
    errors_json = json.dumps(d.get("errors") or [])
    summary_json = json.dumps(d)

    return (
        run_id,
        project_id,
        source_type,
        target_type,
        status,
        started_at,
        finished_at,
        duration_s,
        steps_json,
        errors_json,
        summary_json,
        _semabridge_version(),
    )


def _ts(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.astimezone(timezone.utc).isoformat()
    return str(val)


def _status_str(val: Any) -> str:
    if val is None:
        return "UNKNOWN"
    if hasattr(val, "value"):
        return str(val.value).upper()
    return str(val).upper()
