"""Databricks Genie REST API Client."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional
import httpx

from semabridge.connectors.genie.models import (
    GenieHealthCheck,
    GenieSpace,
)

logger = logging.getLogger(__name__)


class GenieClient:
    """Direct HTTP client for Databricks Genie Space REST APIs (/api/2.0/genie/*)."""

    def __init__(
        self,
        host: str,
        token: str,
        timeout_seconds: int = 30,
        verify_ssl: bool = True,
    ) -> None:
        self.host = str(host or "").strip().rstrip("/")
        if self.host and not self.host.startswith("http://") and not self.host.startswith("https://"):
            self.host = f"https://{self.host}"
        self.token = str(token or "").strip()
        self.timeout = timeout_seconds
        self.verify_ssl = verify_ssl

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "SemaBridge-Genie-Connector/1.0",
        }

    def health_check(self) -> GenieHealthCheck:
        """Validate connectivity to Databricks Genie APIs and permissions."""
        if not self.host or not self.token:
            return GenieHealthCheck(
                connected=False,
                host=self.host,
                error="Host or Databricks personal access token missing.",
            )

        url = f"{self.host}/api/2.0/genie/spaces"
        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                resp = client.get(url, headers=self._headers())

            if resp.status_code == 200:
                data = resp.json()
                spaces = data.get("spaces") or data.get("genie_spaces") or []
                return GenieHealthCheck(
                    connected=True,
                    host=self.host,
                    spaces_count=len(spaces),
                    has_write_scope=True,
                    details={"spaces_returned": len(spaces)},
                )
            elif resp.status_code in (401, 403):
                return GenieHealthCheck(
                    connected=False,
                    host=self.host,
                    error=f"Authentication/Authorization error (HTTP {resp.status_code}): Token lacks Genie scopes.",
                )
            elif resp.status_code == 404:
                return GenieHealthCheck(
                    connected=False,
                    host=self.host,
                    error="Genie API endpoint not found (/api/2.0/genie/spaces). Verify Genie is enabled on this workspace.",
                )
            else:
                return GenieHealthCheck(
                    connected=False,
                    host=self.host,
                    error=f"Databricks Genie API error HTTP {resp.status_code}: {resp.text[:200]}",
                )
        except Exception as exc:
            return GenieHealthCheck(
                connected=False,
                host=self.host,
                error=f"Connection failed: {str(exc)}",
            )

    def list_spaces(self) -> List[Dict[str, Any]]:
        """List all Genie Spaces in the workspace."""
        url = f"{self.host}/api/2.0/genie/spaces"
        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
            resp = client.get(url, headers=self._headers())

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list Genie Spaces (HTTP {resp.status_code}): {resp.text[:250]}")

        data = resp.json()
        return data.get("spaces") or data.get("genie_spaces") or []

    def get_space(self, space_id: str) -> Dict[str, Any]:
        """Retrieve details and configuration of a specific Genie Space."""
        if not space_id:
            raise ValueError("space_id cannot be empty")
        url = f"{self.host}/api/2.0/genie/spaces/{space_id}"
        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
            resp = client.get(url, headers=self._headers())

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to get Genie Space {space_id} (HTTP {resp.status_code}): {resp.text[:250]}")

        return resp.json()

    def create_space(
        self,
        title: str,
        warehouse_id: str,
        description: str = "",
        tables: Optional[List[str]] = None,
        metric_views: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new Genie Space bound to a SQL warehouse and metric views."""
        url = f"{self.host}/api/2.0/genie/spaces"
        payload: Dict[str, Any] = {
            "title": title,
            "warehouse_id": warehouse_id,
            "description": description,
        }
        if tables:
            payload["tables"] = [{"name": t} for t in tables]
        if metric_views:
            payload["metric_views"] = [{"name": mv} for mv in metric_views]

        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
            resp = client.post(url, json=payload, headers=self._headers())

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create Genie Space (HTTP {resp.status_code}): {resp.text[:250]}")

        return resp.json()

    def publish_benchmarks(
        self,
        space_id: str,
        benchmarks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Publish benchmark question-SQL pairs (curated queries) to a Genie Space."""
        if not space_id:
            raise ValueError("space_id cannot be empty")
        
        # Databricks Genie benchmark / sample questions endpoint
        # POST /api/2.0/genie/spaces/{space_id}/benchmarks or curated-queries
        url = f"{self.host}/api/2.0/genie/spaces/{space_id}/benchmarks"
        payload = {
            "benchmarks": benchmarks,
        }
        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
            resp = client.post(url, json=payload, headers=self._headers())

        # If benchmarks endpoint not supported, fallback to curated queries or instructions
        if resp.status_code == 404:
            alt_url = f"{self.host}/api/2.0/genie/spaces/{space_id}/curated-queries"
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                resp = client.post(alt_url, json={"queries": benchmarks}, headers=self._headers())

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to publish benchmarks to Genie Space {space_id} (HTTP {resp.status_code}): {resp.text[:250]}")

        return resp.json()

    def start_evaluation(
        self,
        space_id: str,
        benchmark_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Trigger an evaluation run of benchmark questions in a Genie Space."""
        if not space_id:
            raise ValueError("space_id cannot be empty")
        url = f"{self.host}/api/2.0/genie/spaces/{space_id}/evaluations"
        payload = {}
        if benchmark_ids:
            payload["benchmark_ids"] = benchmark_ids

        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
            resp = client.post(url, json=payload, headers=self._headers())

        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Failed to trigger Genie evaluation (HTTP {resp.status_code}): {resp.text[:250]}")

        return resp.json()

    def get_evaluation(self, space_id: str, evaluation_id: str) -> Dict[str, Any]:
        """Retrieve the status and results of a Genie Space evaluation."""
        url = f"{self.host}/api/2.0/genie/spaces/{space_id}/evaluations/{evaluation_id}"
        with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
            resp = client.get(url, headers=self._headers())

        if resp.status_code != 200:
            raise RuntimeError(f"Failed to get Genie evaluation {evaluation_id} (HTTP {resp.status_code}): {resp.text[:250]}")

        return resp.json()
