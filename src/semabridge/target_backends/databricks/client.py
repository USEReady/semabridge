"""Guarded Databricks SQL client for the planner path (Phase 3.3).

Runs one statement at a time through the SQL Statement Execution API
(``/api/2.0/sql/statements``), following the official API reference
(checked 2026-11-23):

1. The guard (``validate_databricks_sql``) checks the statement first,
   before any network call or retry; a refused statement never leaves the
   machine.
2. POST with the warehouse, default catalog/schema, ``wait_timeout`` 10s,
   ``on_wait_timeout`` CONTINUE, INLINE JSON_ARRAY results and query tags
   (audit: app=semabridge, plus caller tags such as run_id).
3. While the state is PENDING/RUNNING, poll GET /statements/{id} with
   backoff. If the overall deadline passes, POST /statements/{id}/cancel
   and raise DatabricksTimeoutError.
4. The *state* decides success: a failed statement still comes back as
   HTTP 200 with state FAILED (status.error: error_code, message,
   sql_state). CANCELED / CLOSED are errors too.
5. Results: columns from the manifest; rows from ``data_array``, following
   ``next_chunk_internal_link`` until the last chunk. Values arrive as
   strings (or null) and are converted for common types.

Retries: only temporary conditions (HTTP 429/502/503/504, network errors,
TEMPORARILY_UNAVAILABLE-style error codes) with exponential backoff and
jitter, honouring Retry-After; never a real SQL error. A write statement is
not re-submitted after an ambiguous network failure (it may have run).
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Mapping, Optional

from semabridge.target_backends.databricks.auth import DatabricksConnectionInfo, TokenProvider
from semabridge.target_backends.databricks.guard import READ, DatabricksSqlGuard, GuardDecision
from semabridge.target_backends.databricks.http import HttpResponse, HttpTransport, RequestsTransport, TransportError
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

STATEMENTS_PATH = "/api/2.0/sql/statements"
_RETRY_HTTP = {429, 502, 503, 504}
_RETRY_ERROR_CODES = {"TEMPORARILY_UNAVAILABLE", "SERVICE_UNDER_MAINTENANCE", "WORKSPACE_TEMPORARILY_UNAVAILABLE",
                      "RESOURCE_EXHAUSTED"}
_RUNNING_STATES = {"PENDING", "RUNNING"}
_READ_KINDS = {"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "USE", "VALUES"}


class DatabricksSqlError(RuntimeError):
    """A request or statement failed. Never contains credentials."""

    def __init__(self, message: str, *, statement_id: Optional[str] = None, error_code: Optional[str] = None,
                 sql_state: Optional[str] = None, http_status: Optional[int] = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.statement_id = statement_id
        self.error_code = error_code
        self.sql_state = sql_state
        self.http_status = http_status
        self.retryable = retryable


class DatabricksStatementError(DatabricksSqlError):
    """The statement ran and Databricks reported FAILED / CANCELED / CLOSED."""


class DatabricksTimeoutError(DatabricksSqlError):
    """The overall deadline passed; the statement was cancelled."""


@dataclass(frozen=True)
class Column:
    name: str
    type_name: str
    position: int


@dataclass
class StatementResult:
    statement_id: str
    kind: str
    columns: List[Column] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    def scalar(self) -> Any:
        return next(iter(self.rows[0].values())) if self.rows and self.rows[0] else None


def _convert(value: Any, type_name: str) -> Any:
    if value is None:
        return None
    t = (type_name or "").upper()
    try:
        if t in ("BYTE", "SHORT", "INT", "LONG"):
            return int(value)
        if t in ("FLOAT", "DOUBLE"):
            return float(value)
        if t == "DECIMAL":
            return Decimal(value)
        if t == "BOOLEAN":
            return str(value).lower() == "true"
        if t == "DATE":
            return date.fromisoformat(str(value))
        if t == "TIMESTAMP":
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, InvalidOperation):
        return value
    return value


class DatabricksSqlClient:
    """One connection's guarded SQL client."""

    def __init__(
        self,
        info: DatabricksConnectionInfo,
        *,
        guard: Optional[DatabricksSqlGuard] = None,
        warehouse_id: Optional[str] = None,
        transport: Optional[HttpTransport] = None,
        tokens: Optional[TokenProvider] = None,
        query_tags: Optional[Mapping[str, str]] = None,
        deadline_seconds: float = 300.0,
        max_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.info = info
        self.guard = guard or DatabricksSqlGuard(READ)
        self.warehouse_id = warehouse_id or info.warehouse_id
        self.transport = transport or RequestsTransport()
        self.tokens = tokens or TokenProvider(info, self.transport)
        tags = {"app": "semabridge"}
        tags.update({str(k): str(v) for k, v in (query_tags or {}).items() if k})
        self.query_tags = [{"key": k, "value": v} for k, v in list(tags.items())[:20]]
        self.deadline_seconds = deadline_seconds
        self.max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._clock = clock

    # --- public --------------------------------------------------------------------

    def execute(self, sql: str, *, parameters: Optional[List[Dict[str, Any]]] = None,
                deadline_seconds: Optional[float] = None, row_limit: Optional[int] = None) -> StatementResult:
        decision = self.guard.check(sql)  # before anything touches the network
        if not self.warehouse_id:
            raise DatabricksSqlError("No SQL warehouse ID: set one or discover it first (see warehouses.py).")
        deadline = self._clock() + (deadline_seconds or self.deadline_seconds)
        body: Dict[str, Any] = {
            "statement": sql,
            "warehouse_id": self.warehouse_id,
            "wait_timeout": "10s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
            "query_tags": self.query_tags,
        }
        if self.info.catalog:
            body["catalog"] = self.info.catalog
        if self.info.schema:
            body["schema"] = self.info.schema
        if parameters:
            body["parameters"] = parameters
        if row_limit:
            body["row_limit"] = int(row_limit)

        is_write = decision.kind not in _READ_KINDS
        response = self._call("POST", STATEMENTS_PATH, json=body, deadline=deadline, resubmit_safe=not is_write)
        payload = response.body or {}
        statement_id = payload.get("statement_id")
        logger.info("Databricks statement %s submitted (%s%s)", statement_id, decision.kind,
                    f" {decision.object_name}" if decision.object_name else "")
        payload = self._wait(payload, deadline)
        return self._result(payload, decision)

    def get(self, path: str, *, params: Optional[Mapping[str, Any]] = None, deadline_seconds: float = 60.0) -> Any:
        """A read-only REST GET (warehouses, identity, Unity Catalog metadata)."""
        if not path.startswith("/api/"):
            raise ValueError("REST path must start with /api/")
        return self._call("GET", path, params=params, deadline=self._clock() + deadline_seconds,
                          resubmit_safe=True).body

    # --- internals -------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.token()}", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, *, json: Any = None, params: Optional[Mapping[str, Any]] = None,
              deadline: float, resubmit_safe: bool) -> HttpResponse:
        url = f"{self.info.base_url}{path}"
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self.transport.request(method, url, headers=self._headers(), json=json, params=params,
                                                  timeout=60)
            except TransportError as exc:
                retry = attempt < self.max_attempts and (resubmit_safe or not exc.sent)
                if not retry:
                    raise DatabricksSqlError(f"{method} {path} failed: {exc}", retryable=True) from None
                self._backoff(attempt, None, deadline, f"{method} {path}: {exc}")
                continue
            if response.status == 200:
                return response
            message = _error_message(response)
            if response.status in _RETRY_HTTP and attempt < self.max_attempts:
                self._backoff(attempt, response.retry_after, deadline, f"{method} {path}: HTTP {response.status}")
                continue
            raise DatabricksSqlError(
                f"{method} {path} failed (HTTP {response.status}): {message}",
                http_status=response.status,
                error_code=(response.body or {}).get("error_code") if isinstance(response.body, dict) else None,
                retryable=response.status in _RETRY_HTTP,
            )

    def _backoff(self, attempt: int, retry_after: Optional[float], deadline: float, what: str) -> None:
        delay = retry_after if retry_after is not None else min(30.0, 0.5 * (2 ** (attempt - 1)))
        delay = delay * (0.8 + 0.4 * random.random())
        if self._clock() + delay > deadline:
            raise DatabricksTimeoutError(f"Gave up retrying before the deadline: {what}", retryable=True)
        logger.info("Databricks request retry %d in %.1fs (%s)", attempt, delay, what)
        self._sleep(delay)

    def _wait(self, payload: Dict[str, Any], deadline: float) -> Dict[str, Any]:
        statement_id = payload.get("statement_id")
        poll = 0.5
        while _state(payload) in _RUNNING_STATES:
            if self._clock() >= deadline:
                self._cancel(statement_id)
                raise DatabricksTimeoutError(
                    f"Statement {statement_id} did not finish before the deadline; it was cancelled.",
                    statement_id=statement_id,
                )
            self._sleep(min(poll, max(0.0, deadline - self._clock())))
            poll = min(poll * 1.6, 5.0)
            payload = self._call("GET", f"{STATEMENTS_PATH}/{statement_id}", deadline=deadline,
                                 resubmit_safe=True).body or {}
        return payload

    def _cancel(self, statement_id: Optional[str]) -> None:
        if not statement_id:
            return
        try:
            self.transport.request("POST", f"{self.info.base_url}{STATEMENTS_PATH}/{statement_id}/cancel",
                                   headers=self._headers(), json={}, timeout=15)
        except Exception as exc:  # best effort; the timeout is what gets reported
            logger.warning("Could not cancel Databricks statement %s: %s", statement_id, type(exc).__name__)

    def _result(self, payload: Dict[str, Any], decision: GuardDecision) -> StatementResult:
        statement_id = payload.get("statement_id") or ""
        state = _state(payload)
        if state != "SUCCEEDED":
            error = (payload.get("status") or {}).get("error") or {}
            code = error.get("error_code")
            raise DatabricksStatementError(
                f"Statement {statement_id} {state or 'UNKNOWN'}: {error.get('message') or 'no message'}",
                statement_id=statement_id,
                error_code=code,
                sql_state=error.get("sql_state"),
                retryable=code in _RETRY_ERROR_CODES,
            )
        manifest = payload.get("manifest") or {}
        columns = [
            Column(name=c.get("name", f"col{i}"), type_name=c.get("type_name", ""), position=int(c.get("position", i)))
            for i, c in enumerate((manifest.get("schema") or {}).get("columns") or [])
        ]
        columns.sort(key=lambda c: c.position)
        rows: List[Dict[str, Any]] = []
        chunk = payload.get("result") or {}
        while True:
            for values in chunk.get("data_array") or []:
                rows.append({col.name: _convert(values[col.position] if col.position < len(values) else None,
                                                col.type_name) for col in columns})
            link = chunk.get("next_chunk_internal_link")
            if not link:
                break
            chunk = self._call("GET", link, deadline=self._clock() + 120, resubmit_safe=True).body or {}
        return StatementResult(statement_id=statement_id, kind=decision.kind, columns=columns, rows=rows,
                               truncated=bool(manifest.get("truncated")))


def _state(payload: Mapping[str, Any]) -> str:
    return str(((payload or {}).get("status") or {}).get("state") or "").upper()


def _error_message(response: HttpResponse) -> str:
    body = response.body
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body.get("error_code") or "")[:300]
    return (response.text or "")[:300]
