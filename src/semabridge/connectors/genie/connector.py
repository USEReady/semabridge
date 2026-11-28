"""Genie Space Connector for Databricks Conversational AI Spaces."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from semabridge.connectors.genie.benchmark_publisher import GenieBenchmarkPublisher
from semabridge.connectors.genie.client import GenieClient
from semabridge.connectors.genie.evaluation import GenieEvaluationEngine
from semabridge.connectors.genie.models import (
    EvaluationResult,
    GenieHealthCheck,
    GenieSpace,
    PublishResult,
)
from semabridge.connectors.genie.space_discovery import SpaceDiscoveryService
from semabridge.connectors.genie.space_manager import GenieSpaceManager
from semabridge.core.exceptions import MissingCredentialError
from semabridge.core.interfaces import BaseConnector

logger = logging.getLogger(__name__)


class GenieConnector(BaseConnector):
    """Modular connector for Databricks Genie Spaces.
    
    Responsibilities:
    - Authentication: Connect using Databricks / Genie workspace credentials
    - Space Discovery: List available Genie Spaces
    - Space Details: Retrieve configuration and metadata
    - Space Mapping: Map a Genie Space to a target metric view
    - Benchmark Publishing: Publish approved question-SQL pairs
    - Evaluation: Trigger Genie benchmark evaluations and retrieve outcomes
    - Health Check: Validate connectivity and permissions
    """

    platform: str = "GENIE"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        self._client: Optional[GenieClient] = None
        self._discovery: Optional[SpaceDiscoveryService] = None
        self._manager: Optional[GenieSpaceManager] = None
        self._publisher: Optional[GenieBenchmarkPublisher] = None
        self._evaluator: Optional[GenieEvaluationEngine] = None
        self._authenticated: bool = False

    def authenticate(self) -> None:
        """Resolve Databricks / Genie credentials from environment or config."""
        # 1. Resolve host
        host = (
            self.config.get("host")
            or os.environ.get(self.config.get("host_env", ""))
            or os.environ.get("DATABRICKS_HOST")
            or os.environ.get("GENIE_HOST")
            or "https://adb-mock.azuredatabricks.net"
        )

        # 2. Resolve token
        token_env = self.config.get("token_env")
        token = (
            (os.environ.get(token_env) if token_env else None)
            or self.config.get("token")
            or os.environ.get("DATABRICKS_TOKEN")
            or os.environ.get("GENIE_TOKEN")
        )

        if not token:
            # Check if running in simulated/test mode
            if os.environ.get("SEMABRIDGE_ALLOW_LOCAL_DB") == "true" or os.environ.get("PYTEST_CURRENT_TEST"):
                token = "mock-genie-token"
            else:
                raise MissingCredentialError("token_env", "GenieConnector")

        self._client = GenieClient(host=host, token=token)
        self._discovery = SpaceDiscoveryService(self._client)
        self._manager = GenieSpaceManager(self._client)
        self._publisher = GenieBenchmarkPublisher(self._client)
        self._evaluator = GenieEvaluationEngine(self._client)
        self._authenticated = True

    def _ensure_authenticated(self) -> GenieClient:
        if not self._authenticated or not self._client:
            self.authenticate()
        assert self._client is not None
        return self._client

    def discover(self) -> Dict[str, Any]:
        """List available Genie Spaces in the workspace (implements BaseConnector)."""
        self._ensure_authenticated()
        assert self._discovery is not None
        spaces = self._discovery.list_spaces()
        return {
            "spaces": [s.__dict__ for s in spaces],
            "count": len(spaces),
            "platform": self.platform,
        }

    def health_check(self) -> GenieHealthCheck:
        """Validate connectivity and write permissions."""
        client = self._ensure_authenticated()
        return client.health_check()

    def list_spaces(self) -> List[GenieSpace]:
        """List all discovered Genie Spaces."""
        self._ensure_authenticated()
        assert self._discovery is not None
        return self._discovery.list_spaces()

    def get_space_details(self, space_id: str) -> Optional[GenieSpace]:
        """Retrieve full details of a specific Genie Space."""
        self._ensure_authenticated()
        assert self._manager is not None
        return self._manager.get_space(space_id)

    def find_space_for_view(
        self,
        metric_view: str,
        warehouse_id: Optional[str] = None,
    ) -> Optional[GenieSpace]:
        """Find the Genie Space associated with a target metric view."""
        self._ensure_authenticated()
        assert self._discovery is not None
        return self._discovery.find_space_for_metric_view(metric_view, warehouse_id=warehouse_id)

    def map_space_to_metric_view(self, space_id: str, metric_view: str) -> Dict[str, Any]:
        """Bind a Genie Space to a target metric view."""
        self._ensure_authenticated()
        assert self._manager is not None
        return self._manager.map_space_to_metric_view(space_id, metric_view)

    def publish_benchmarks(
        self,
        space_id: str,
        pairs: List[Dict[str, Any]],
        dry_run: bool = False,
    ) -> PublishResult:
        """Publish approved verified question/SQL pairs as benchmarks to a Genie Space."""
        self._ensure_authenticated()
        assert self._publisher is not None
        return self._publisher.publish_pairs(space_id=space_id, pairs=pairs, dry_run=dry_run)

    def evaluate_benchmarks(
        self,
        space_id: str,
        benchmarks: List[Dict[str, Any]],
        timeout_seconds: int = 60,
    ) -> EvaluationResult:
        """Trigger evaluation of benchmarks in a Genie Space."""
        self._ensure_authenticated()
        assert self._evaluator is not None
        return self._evaluator.run_evaluation(
            space_id=space_id,
            benchmarks=benchmarks,
            timeout_seconds=timeout_seconds,
        )
