"""Databricks Truth Executor.

Connects to Databricks using stored warehouse configuration via SQL Statement Execution REST API.
Validates all required parameters in open(), applies timeout, and maps rows/columns to QueryResult.
Normalizes numeric types (DECIMAL or string numbers) to float/int.
"""
from __future__ import annotations

from decimal import Decimal
import logging
import time
from typing import Any, Dict, List, Optional
import requests

from semabridge.ai.executors.base import QueryResult, TruthExecutor

logger = logging.getLogger("semabridge.ai.executors.databricks")


def _normalize_databricks_cell(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        s = val.strip()
        try:
            if "." in s or "e" in s.lower():
                return float(s)
            return int(s)
        except ValueError:
            return val
    if hasattr(val, "isoformat") and callable(getattr(val, "isoformat", None)):
        return val.isoformat()
    if isinstance(val, (list, tuple, set)):
        return [_normalize_databricks_cell(x) for x in val]
    if isinstance(val, dict):
        return {str(k): _normalize_databricks_cell(v) for k, v in val.items()}
    return val


class DatabricksTruthExecutor(TruthExecutor):
    platform: str = "DATABRICKS"

    def __init__(self, project_id: str, config: Any):
        super().__init__(project_id, config)
        self.host = None
        self.token = None
        self.warehouse_id = None
        self.catalog = None
        self.schema = None
        self._endpoint = None
        self._headers = None

    def open(self) -> None:
        cfg = self.config
        host = getattr(cfg, "host", None) or getattr(cfg, "server_hostname", None)
        token = getattr(cfg, "token", None) or getattr(cfg, "access_token", None)
        if hasattr(token, "get_secret_value"):
            token = token.get_secret_value()
        warehouse_id = getattr(cfg, "warehouse_id", None) or getattr(cfg, "http_path", None)
        if warehouse_id and "/" in str(warehouse_id):
            warehouse_id = str(warehouse_id).split("/")[-1]
        catalog = getattr(cfg, "catalog", None)
        schema = getattr(cfg, "schema_name", None) or getattr(cfg, "schema", None)

        missing = []
        if not host:
            missing.append("host")
        if not token:
            missing.append("token")
        if not warehouse_id:
            missing.append("warehouse_id")
        if not catalog:
            missing.append("catalog")
        if not schema:
            missing.append("schema")
        if missing:
            raise ValueError(f"MISSING_WAREHOUSE_CONFIG: Databricks {', '.join(missing)} credentials are required")

        self.host = str(host)
        self.token = str(token)
        self.warehouse_id = str(warehouse_id)
        self.catalog = str(catalog)
        self.schema = str(schema)

        clean_host = self.host.replace("https://", "").replace("http://", "").rstrip("/")
        self._endpoint = f"https://{clean_host}/api/2.0/sql/statements"
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def execute(self, sql: str, timeout: Optional[int] = None) -> QueryResult:
        if self._endpoint is None:
            self.open()

        to = timeout or 15
        payload = {
            "statement": sql,
            "wait_timeout": f"{min(to, 30)}s",
            "warehouse_id": self.warehouse_id,
            "catalog": self.catalog,
            "schema": self.schema,
        }

        try:
            resp = requests.post(self._endpoint, json=payload, headers=self._headers, timeout=to + 5)
        except requests.exceptions.Timeout as t_err:
            return QueryResult(rows=[], row_count=0, columns=[], error=f"Query timed out: {t_err}", executed=True)
        except Exception as net_err:
            return QueryResult(rows=[], row_count=0, columns=[], error=f"Network error: {net_err}", executed=False)

        if resp.status_code in (401, 403):
            return QueryResult(rows=[], row_count=0, columns=[], error="Warehouse authentication failed (401/403)", executed=True)
        if resp.status_code == 404:
            return QueryResult(rows=[], row_count=0, columns=[], error=f"Endpoint or resource not found (404): {resp.text[:200]}", executed=True)
        if resp.status_code >= 400:
            return QueryResult(rows=[], row_count=0, columns=[], error=f"HTTP {resp.status_code}: {resp.text[:300]}", executed=True)

        data = resp.json()
        statement_id = data.get("statement_id")
        status_info = data.get("status") or {}
        state = status_info.get("state")  # hardcoding-ok: Databricks statement execution API status field

        start_t = time.time()
        while state in ("PENDING", "RUNNING") and statement_id and (time.time() - start_t < to):
            time.sleep(1)
            try:
                poll_resp = requests.get(f"{self._endpoint}/{statement_id}", headers=self._headers, timeout=10)
                if poll_resp.status_code >= 400:
                    break
                data = poll_resp.json()
                state = (data.get("status") or {}).get("state")  # hardcoding-ok: Databricks statement execution API status field
            except Exception:
                break

        if state not in ("SUCCEEDED",):
            err_desc = (data.get("status") or {}).get("error", {}).get("message") or state or "Execution failed"
            return QueryResult(rows=[], row_count=0, columns=[], error=str(err_desc), executed=True)

        manifest = data.get("manifest") or {}
        schema_info = manifest.get("schema") or {}
        cols = [c["name"].upper() for c in schema_info.get("columns", [])]
        raw_rows = (data.get("result") or {}).get("data_array") or []

        res_rows = []
        for row in raw_rows:
            row_dict = {}
            for idx, val in enumerate(row):
                cname = cols[idx] if idx < len(cols) else f"COL_{idx}"
                row_dict[cname] = _normalize_databricks_cell(val)
            res_rows.append(row_dict)

        return QueryResult(
            rows=res_rows,
            row_count=len(res_rows),
            columns=cols,
            error=None,
            executed=True,
        )

    def close(self) -> None:
        self._endpoint = None
        self._headers = None
