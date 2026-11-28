"""Power BI Source Adapter supporting local Desktop and Power BI Service."""
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
from semabridge.reconciliation.dax_executor import (
    _acquire_powerbi_token,
    _execute_pbi_desktop,
    discover_local_pbi_ports,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class PowerBISourceAdapter(SourceAdapter):
    """Executes DAX queries against Power BI Desktop (local ADOMD) or Power BI Service (REST)."""

    platform: str = "POWER_BI"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.backend = config.get("backend")  # "pbi_desktop" or "rest_api"
        self.port = config.get("port")
        self.workspace_id = config.get("workspace_id") or os.getenv("PBI_WORKSPACE_ID")
        self.dataset_id = config.get("dataset_id") or os.getenv("PBI_DATASET_ID")
        self.tenant_id = config.get("tenant_id")
        self.client_id = config.get("client_id")
        self.client_secret = config.get("client_secret")

    def execute_metric(
        self,
        metric_name: str,
        slice_filters: Optional[Dict[str, Any]] = None,
        timeout_seconds: int = 60,
    ) -> SourceResult:
        t0 = time.perf_counter()
        query_text = build_dax_query(metric_name, slice_filters)

        # If desktop specified or port given, or if no dataset_id is present, attempt local desktop
        if self.backend == "pbi_desktop" or self.port or not self.dataset_id:
            try:
                cols, rows, meta = _execute_pbi_desktop(query_text, port=self.port)
                val = extract_single_value(rows, metric_name)
                return SourceResult(
                    success=True,
                    platform=self.platform,
                    query_text=query_text,
                    raw_value=val,
                    columns=cols,
                    rows=rows,
                    execution_ms=(time.perf_counter() - t0) * 1000,
                    metadata={"backend": "pbi_desktop", **meta},
                )
            except Exception as desktop_err:
                # If explicit desktop requested, fail with desktop reason
                if self.backend == "pbi_desktop" or not self.dataset_id:
                    return SourceResult(
                        success=False,
                        platform=self.platform,
                        query_text=query_text,
                        execution_ms=(time.perf_counter() - t0) * 1000,
                        error=f"Power BI Desktop error: {str(desktop_err)}",
                        metadata={"backend": "pbi_desktop"},
                    )

        # Fallback or explicit REST API backend
        return self._execute_rest_api(query_text, metric_name, t0, timeout_seconds)

    def _execute_rest_api(
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
                error="Power BI dataset_id is required for REST API execution.",
                execution_ms=(time.perf_counter() - t0) * 1000,
            )

        try:
            token = _acquire_powerbi_token(self.tenant_id, self.client_id, self.client_secret)
        except Exception as exc:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query,
                error=f"Power BI Authentication failed: {str(exc)}",
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
                    error=f"Power BI executeQueries failed (HTTP {resp.status_code}): {resp.text[:200]}",
                )

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return SourceResult(
                    success=False,
                    platform=self.platform,
                    query_text=query,
                    execution_ms=duration_ms,
                    error="Empty results from Power BI Service",
                )

            first_res = results[0]
            tables = first_res.get("tables", [])
            if not tables:
                err = first_res.get("error") or "No tables returned by Power BI query"
                return SourceResult(
                    success=False,
                    platform=self.platform,
                    query_text=query,
                    execution_ms=duration_ms,
                    error=str(err),
                )

            raw_rows = tables[0].get("rows", [])
            cols = normalize_dax_columns(list(raw_rows[0].keys()) if raw_rows else [])
            val = extract_single_value(raw_rows, metric_name)

            return SourceResult(
                success=True,
                platform=self.platform,
                query_text=query,
                raw_value=val,
                columns=cols,
                rows=raw_rows,
                execution_ms=duration_ms,
                metadata={"backend": "rest_api"},
            )
        except Exception as exc:
            return SourceResult(
                success=False,
                platform=self.platform,
                query_text=query,
                execution_ms=(time.perf_counter() - t0) * 1000,
                error=f"Power BI REST error: {str(exc)}",
            )
