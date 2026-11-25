"""Tests for live Fabric validation isolation.

1. Dry-run gate and duplicate check must NOT call Fabric and must NOT fail for
   Fabric projects without source.workspace_id, even with no network access.
2. Project save and sync execution still validate against Fabric.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from semabridge.api.services.duplicate_sync_service import _planned_models, find_duplicate_deployments
from semabridge.api.services.dry_run_job_service import _gate_jobs
from semabridge.api.services.sync_execution_service import (
    ValidationError,
    validate_fabric_project,
)


@pytest.fixture
def block_network():
    """Mock socket connect to simulate an offline / isolated network environment."""
    def _fail_connect(*args, **kwargs):
        raise OSError("Network access is blocked in this test")

    with patch.object(socket.socket, "connect", side_effect=_fail_connect):
        yield


def test_dry_run_gate_no_network_no_workspace_id(block_network):
    """Dry-run gate must not call Fabric and must not fail without source.workspace_id."""
    cfg = {
        "source": {
            "type": "fabric",
            "models": ["CustomerInsights", "SalesModel"],
            # intentionally no workspace_id
        },
        "target": {
            "type": "snowflake",
            "database": "TEST_DB",
            "schema": "PUBLIC",
        },
    }
    jobs, target_type = _gate_jobs(cfg)
    assert target_type == "snowflake"
    assert len(jobs) == 2
    assert [j["model_label"] for j in jobs] == ["CustomerInsights", "SalesModel"]
    # No identity attached during dry-run gate
    assert all(j.get("identity") is None for j in jobs)


def test_duplicate_check_no_network_no_workspace_id(block_network):
    """Duplicate check must not call Fabric and must not fail without source.workspace_id."""
    cfg = {
        "source": {
            "type": "fabric",
            "models": ["SalesOverview"],
            # intentionally no workspace_id
        },
        "target": {
            "type": "snowflake",
            "database": "ANALYTICS_DB",
            "schema": "REPORTING",
        },
    }
    # 1. Planned models extracted cleanly with no network
    planned = _planned_models(cfg)
    assert len(planned) == 1
    assert planned[0]["model_label"] == "SalesOverview"

    # 2. find_duplicate_deployments runs cleanly with no network
    duplicates = find_duplicate_deployments("proj-fabric-1", cfg)
    assert isinstance(duplicates, list)


def test_save_validates_fabric_workspace_and_models():
    """Project save validation requires workspace_id and resolves models against workspace."""
    # Missing workspace_id -> ValidationError
    cfg_no_ws = {
        "source": {
            "type": "fabric",
            "models": ["Sales"],
        },
    }
    with patch("semabridge.api.services.sync_execution_service._fabric_env_workspace", return_value=""):
        with pytest.raises(ValidationError, match="Fabric source has no workspace"):
            validate_fabric_project(cfg_no_ws)

    # Valid workspace_id with resolver validating models
    cfg_with_ws = {
        "source": {
            "type": "fabric",
            "workspace_id": "ws-guid-1234",
            "models": ["Sales"],
        },
    }

    mock_check = SimpleNamespace(
        by_ref={
            "Sales": SimpleNamespace(
                model_id="model-guid-5678",
                display_name="Sales",
                workspace_id="ws-guid-1234",
            )
        }
    )

    def dummy_resolver(refs, workspace_id, identity_id):
        assert refs == ["Sales"]
        assert workspace_id == "ws-guid-1234"
        return mock_check

    validated = validate_fabric_project(cfg_with_ws, model_resolver=dummy_resolver)
    assert len(validated) == 1
    assert validated[0]["dataset_id"] == "model-guid-5678"
    assert validated[0]["identity"]["model_id"] == "model-guid-5678"

    # Model not found in workspace -> ValidationError
    def failing_resolver(refs, workspace_id, identity_id):
        return SimpleNamespace(by_ref={})

    with pytest.raises(ValidationError, match="was not resolved in workspace"):
        validate_fabric_project(cfg_with_ws, model_resolver=failing_resolver)


def test_sync_execution_validates_fabric_models():
    """Sync execution validates workspace and attaches identities."""
    from semabridge.api.services.sync_execution_service import _attach_fabric_identities, _build_sync_jobs

    cfg = {
        "source": {
            "type": "fabric",
            "workspace_id": "ws-sync-1111",
            "models": ["Inventory"],
        },
        "target": {
            "type": "snowflake",
            "database": "DB",
            "schema": "SCH",
        },
    }

    sync_jobs, _, _, source_cfg, _ = _build_sync_jobs(cfg)
    assert len(sync_jobs) == 1
    assert sync_jobs[0].get("identity") is None  # identity not attached in _build_sync_jobs

    # Attached in execution phase
    mock_check = SimpleNamespace(
        by_ref={
            "Inventory": SimpleNamespace(
                model_id="inv-guid-9999",
                display_name="Inventory",
                workspace_id="ws-sync-1111",
            )
        }
    )
    attached = _attach_fabric_identities(
        cfg,
        source_cfg,
        sync_jobs,
        account_id=None,
        model_resolver=lambda refs, ws, ident: mock_check,
    )
    assert len(attached) == 1
    assert attached[0]["dataset_id"] == "inv-guid-9999"
    assert attached[0]["identity"]["workspace_id"] == "ws-sync-1111"
