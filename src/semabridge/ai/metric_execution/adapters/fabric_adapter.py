"""Microsoft Fabric Source Adapter for Metric Execution."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import httpx

from semabridge.ai.metric_execution.adapters.base import SourceAdapter, SourceResult
from semabridge.ai.metric_execution.adapters.dax_shared import (
    build_dax_query,
    extract_single_value,
    normalize_dax_columns,
)
from semabridge.reconciliation.dax_executor import _acquire_powerbi_token, _open_adomd_connection
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class FabricSourceAdapter(SourceAdapter):
    """Executes DAX queries against Microsoft Fabric semantic models."""

    platform: str = "FABRIC"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.workspace_id = config.get("workspace_id") or os.getenv("FABRIC_WORKSPACE_ID") or os.getenv("PBI_WORKSPACE_ID")
        self.dataset_id = config.get("dataset_id") or config.get("semantic_model_id") or os.getenv("FABRIC_DATASET_ID") or os.getenv("PBI_DATASET_ID")
        self.tenant_id = config.get("tenant_id") or os.getenv("FABRIC_TENANT_ID")
        self.client_id = config.get("client_id") or os.getenv("FABRIC_CLIENT_ID")
        self.client_secret = config.get("client_secret") or os.getenv("FABRIC_CLIENT_SECRET")
        self.xmla_endpoint = config.get("xmla_endpoint")

    def execute_metric(
        self,
        metric_name: str,
        slice_filters: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 60,
    ) -> SourceResult:
        t0 = time.perf_counter()
        query_text = build_dax_query(metric_name, slice_filters)

        # XMLA path if configured
        if self.xmla_endpoint:
            return self._execute_xmla(query_text, metric_name, t0, timeout_seconds)

        # REST executeQueries path (standard default)
        return self._execute_rest(query_text, metric_name, t0, timeout_seconds)

    def _execute_rest(
        self,
        query: str,
        metric_name: str,
        t0: float,
        timeout_seconds: int,
    ) -> SourceResult:
        if not self.dataset_id:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query,
                error="Fabric dataset_id is required.",
                execution_ms=(time.perf_counter() - t0) * 1000,
            )

        try:
            token = _acquire_powerbi_token(self.tenant_id, self.client_id, self.client_secret)
        except Exception as exc:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query,
                error=f"Authentication error: {str(exc)}",
                execution_ms=(time.perf_counter() - t0) * 1000,
            )

        if self.workspace_id:
            url = f"https://api.powerbi.com/v1.0/myorg/groups/{self.workspace_id}/datasets/{self.dataset_id}/executeQueries"
        else:
            url = f"https://api.powerbi.com/v1.0/myorg/datasets/{self.dataset_id}/executeQueries"

        payload = {
            "queries": [{"query": query}],
            "serializerSettings": {"includeNulls": True},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                resp = client.post(url, json=payload, headers=headers)

            duration_ms = (time.perf_counter() - t0) * 1000
            if not resp.is_success:
                return SourceResult(
                    success=False,
                    platform=self.platform,
                    query_text=query,
                    execution_ms=duration_ms,
                    error=f"Fabric executeQueries failed (HTTP {resp.status_code}): {resp.text[:200]}",
                )

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return SourceResult(
                    success=False,
                    platform=self.platform,
                    query_text=query,
                    execution_ms=duration_ms,
                    error="Empty results from Fabric API",
                )

            first_res = results[0]
            tables = first_res.get("tables", [])
            if not tables:
                error_msg = first_res.get("error") or "No tables returned by Fabric query"
                return SourceResult(
                    success=False,
                    platform=self.platform,
                    query_text=query,
                    execution_ms=duration_ms,
                    error=str(error_msg),
                )

            raw_rows = tables[0].get("rows", [])
            columns = normalize_dax_columns(list(raw_rows[0].keys()) if raw_rows else [])
            raw_val = extract_single_value(raw_rows, metric_name)

            return SourceResult(
                success=True,
                platform=self.platform,
                query_text=query,
                raw_value=raw_val,
                columns=columns,
                rows=raw_rows,
                execution_ms=duration_ms,
            )
        except Exception as exc:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query,
                execution_ms=(time.perf_counter() - t0) * 1000,
                error=f"Fabric execution error: {str(exc)}",
            )

    def _execute_xmla(
        self,
        query: str,
        metric_name: str,
        t0: float,
        timeout_seconds: int,
    ) -> SourceResult:
        try:
            conn_str = f"Provider=MSOLAP;Data Source={self.xmla_endpoint};Initial Catalog={self.dataset_id};"
            mode, driver = _open_adomd_connection(conn_str)
            if mode == "pyadomd":
                with driver as conn:
                    with conn.cursor() as cur:
                        cur.execute(query)
                        cols = normalize_dax_columns([desc[0] for desc in (cur.description or [])])
                        rows = [dict(zip(cols, list(r))) for r in cur.fetchall()]
            else:
                AdomdConnection, AdomdCommand, cstr = driver
                conn = AdomdConnection(cstr)
                conn.Open()
                try:
                    cmd = AdomdCommand(query, conn)
                    reader = cmd.ExecuteReader()
                    try:
                        cols = normalize_dax_columns([reader.GetName(i) for i in range(reader.FieldCount)])
                        rows = []
                        while reader.Read():
                            vals = [reader.GetValue(i) for i in range(reader.FieldCount)]
                            rows.append(dict(zip(cols, vals)))
                    finally:
                        reader.Close()
                finally:
                    conn.Close()

            val = extract_single_value(rows, metric_name)
            return SourceResult(
                success=True,
                platform=self.platform,
                query_text=query,
                raw_value=val,
                columns=cols,
                rows=rows,
                execution_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query,
                execution_ms=(time.perf_counter() - t0) * 1000,
                error=f"XMLA execution error: {str(exc)}",
            )
