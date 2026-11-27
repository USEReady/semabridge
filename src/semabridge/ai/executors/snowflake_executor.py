"""Snowflake Truth Executor.

Connects to Snowflake using stored warehouse config (key-pair auth), opens one cursor
with session timeout for the run, and returns normalized QueryResult rows.
"""
from __future__ import annotations

import datetime as _dt
import decimal as _dec
import logging
import uuid
from typing import Any, Dict, List, Optional

from semabridge.ai.executors.base import QueryResult, TruthExecutor

logger = logging.getLogger("semabridge.ai.executors.snowflake")


def _sanitize_snowflake_cell(val: Any) -> Any:
    """Recursively convert date, datetime, and Decimal values to JSON-serializable primitives."""
    if val is None:
        return None
    if isinstance(val, (_dt.date, _dt.datetime)) or (hasattr(val, "isoformat") and callable(getattr(val, "isoformat", None))):
        return val.isoformat()
    if isinstance(val, (uuid.UUID,)):
        return str(val)
    if isinstance(val, _dec.Decimal):
        if val.is_nan():
            return "NaN"
        if val.is_infinite():
            return "Infinity" if val > 0 else "-Infinity"
        if val % 1 == 0:
            return int(val)
        try:
            f = float(val)
            if _dec.Decimal(str(f)) == val:
                return f
        except Exception:
            pass
        return str(val)
    if isinstance(val, (list, tuple, set)):
        return [_sanitize_snowflake_cell(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _sanitize_snowflake_cell(v) for k, v in val.items()}
    return val


class SnowflakeTruthExecutor(TruthExecutor):
    platform: str = "SNOWFLAKE"

    def __init__(self, project_id: str, config: Any):
        super().__init__(project_id, config)
        self._conn = None
        self._cur = None

    def open(self) -> None:
        if self._conn is not None:
            return
        from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
        import snowflake.connector

        kw = get_snowflake_connect_kwargs(self.config)
        if not (kw.get("account") and kw.get("user")):
            raise ValueError("MISSING_WAREHOUSE_CONFIG: Snowflake account and user credentials are required")

        self._conn = snowflake.connector.connect(**kw)
        self._cur = self._conn.cursor()
        try:
            from semabridge.governance.drift.adapters.snowflake import get_snowflake_statement_timeout
            timeout = get_snowflake_statement_timeout()
        except Exception:
            timeout = 15
        try:
            self._cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout}")
        except Exception as ex:
            logger.warning("Failed setting Snowflake statement timeout: %s", ex)

    def execute(self, sql: str, timeout: Optional[int] = None) -> QueryResult:
        if self._conn is None or self._cur is None:
            self.open()

        if timeout and self._cur is not None:
            try:
                self._cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {timeout}")
            except Exception as ex:
                logger.warning("Failed setting Snowflake dynamic statement timeout: %s", ex)

        try:
            self._cur.execute(sql)
            cols = [d[0].upper() for d in self._cur.description] if self._cur.description else []
            raw_rows = self._cur.fetchall()
            res_rows = [
                {cols[i]: _sanitize_snowflake_cell(val) for i, val in enumerate(row)}
                for row in raw_rows
            ]
            return QueryResult(
                rows=res_rows,
                row_count=len(res_rows),
                columns=cols,
                error=None,
                executed=True,
            )
        except Exception as exc:
            return QueryResult(
                rows=[],
                row_count=0,
                columns=[],
                error=str(exc),
                executed=True,
            )

    def close(self) -> None:
        if self._cur is not None:
            try:
                self._cur.close()
            except Exception:
                pass
            self._cur = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
