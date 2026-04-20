from types import SimpleNamespace
from unittest.mock import patch

import pytest

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.execution_engine import DeploymentError, ExecutionEngine, RunContext


class _PublisherStub:
    def __init__(self, *_args, **_kwargs):
        self._summary = {
            "routing_summary": {"generated_artifact_count": 1},
            "sql_fallback_state": "FALLBACK_FAILED",
            "sql_fallback_reason": "failed_views=1;total_views=1",
        }

    def publish(self, _model):
        return "databricks://main/public/TestModel"

    def get_last_publish_summary(self):
        return self._summary


def test_deploy_to_databricks_raises_when_sql_fallback_failed():
    engine = ExecutionEngine()
    context = RunContext(
        project_id="TestModel",
        run_id="run-1",
        config=SimpleNamespace(databricks=object(), targets=[], target=None),
        start_time=0.0,
        source_type="fabric",
        target_type="databricks",
        behavior=ConnectorBehavior(),
    )
    context.sml_model = object()

    with patch("semabridge.connectors.databricks_publisher.DatabricksPublisher", _PublisherStub):
        with pytest.raises(DeploymentError, match="Databricks SQL fallback failed"):
            engine._deploy_to_databricks(context)
