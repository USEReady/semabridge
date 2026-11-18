from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.context import RunContext
from semabridge.core.engine.deployment.snowflake import target_snowflake_config
from semabridge.core.engine.engine import ExecutionEngine
from semabridge.core.run_summary import RunStatus, create_run_summary


def test_target_snowflake_config_warns_and_records_ambient_origin():
    """Verify that a Snowflake target without a linked account sets ambient warning and origin."""
    context = RunContext(
        project_id="Test Ambient",
        run_id="run-amb-1",
        config=SimpleNamespace(
            snowflake=SimpleNamespace(
                account="ambient-acct",
                warehouse="wh",
                database="db",
                schema_name="public",
            ),
            targets=[{"type": "snowflake"}],  # no identity_id
        ),
        start_time=0.0,
        source_type="fabric",
        target_type="snowflake",
        behavior=ConnectorBehavior(),
    )

    sf_cfg = target_snowflake_config(context)
    assert sf_cfg.account == "ambient-acct"

    expected_warning = "Snowflake target credentials: ambient config (no linked account)"
    assert expected_warning in context.target_warnings
    assert expected_warning in context.source_warnings
    assert context.target_credential_origin == "ambient config (no linked account)"

    engine = ExecutionEngine()
    engine._summary = create_run_summary(
        project_id="Test Ambient",
        run_id="run-amb-1",
        source_type="fabric",
        target_type="snowflake",
    )
    summary = engine._step10_finalize(context, RunStatus.SUCCESS)
    assert summary.target_credential_origin == "ambient config (no linked account)"
    assert expected_warning in summary.target_warnings
    assert expected_warning in summary.source_warnings


@pytest.mark.asyncio
async def test_dry_run_pipeline_surfaces_ambient_warning(monkeypatch):
    """Verify that _run_dry_run_pipeline surfaces the ambient warning and origin."""
    from semabridge.api.controllers import mappings_controller as mc

    async def fake_sync_models(payload):
        return {
            "status": "success",
            "results": [
                {
                    "model": "TestModel",
                    "status": "success",
                    "summary": {
                        "sml_snapshot_id": "snap-123",
                        "status": "SUCCESS",
                        "target_credential_origin": "ambient config (no linked account)",
                        "target_warnings": ["Snowflake target credentials: ambient config (no linked account)"],
                        "source_warnings": ["Snowflake target credentials: ambient config (no linked account)"],
                    },
                }
            ],
        }

    from semabridge.api.services import core_domain_service
    monkeypatch.setattr(core_domain_service, "sync_models", fake_sync_models)
    monkeypatch.setattr(mc, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)

    # Mock snapshot loading
    mock_snap = SimpleNamespace(
        sml_blob={
            "unique_name": "TestModel",
            "datasets": [],
            "metrics": [],
        }
    )
    from semabridge.api.services.project_shared import db_manager
    monkeypatch.setattr(db_manager, "get_snapshot", lambda sid: mock_snap)

    res = await mc._run_dry_run_pipeline(
        project_id="test-proj",
        request_user_id=1,
        source_config={"type": "fabric", "identity_id": "fab-1"},
        target_config={"type": "snowflake"},  # no identity_id
        selected_sources=["TestModel"],
    )

    expected_warning = "Snowflake target credentials: ambient config (no linked account)"
    assert expected_warning in res["warnings"]
    assert res["target_credential_origin"] == "ambient config (no linked account)"
    assert expected_warning in res["summary"]["warnings"]
    assert res["summary"]["target_credential_origin"] == "ambient config (no linked account)"

