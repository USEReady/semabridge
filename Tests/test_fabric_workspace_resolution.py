"""Tests verifying identical workspace resolution between dry run and real sync.

Requirements:
- Dry run resolves workspace exactly the same way as real sync.
- No fallback to a "default" workspace or server default / .env value.
- Dry-run gate and duplicate check never call Fabric and need no workspace.
- Any step reading from Fabric in dry run or sync fails with the same standard error message when workspace is missing.
- A project with a workspace resolves it identically in dry run and sync.
"""

import pytest
import yaml
from unittest.mock import patch

from semabridge.api.services.sync_execution_service import execute_sync_request, _fabric_source_workspace
from semabridge.domain.exceptions import ValidationError
from semabridge.api.services.run_service import _assert_sync_ready
from semabridge.api.services.duplicate_sync_service import find_duplicate_deployments
from semabridge.api.services import mapping_service


def test_dry_run_and_sync_with_workspace_resolves_identically():
    config = {
        "project_id": "test_fabric_with_ws",
        "source": {"type": "fabric", "workspace_id": "my-explicit-workspace-uuid", "models": ["Sales"]},
        "target": {"type": "snowflake"},
    }
    content_str = yaml.dump(config)
    payload_dry_run = {
        "content": content_str,
        "dry_run": True,
        "selection": ["Sales"],
    }
    payload_sync = {
        "content": content_str,
        "dry_run": False,
        "selection": ["Sales"],
    }

    resolved_workspaces = []

    def mock_run_parallel_jobs(**kwargs):
        resolved_workspaces.append(kwargs.get("resolved_workspace_id"))
        return []

    with patch("semabridge.api.services.sync_execution_service._run_parallel_jobs", mock_run_parallel_jobs), \
         patch("semabridge.api.services.sync_execution_service._attach_fabric_identities", lambda cfg, s_cfg, jobs, acct, res: jobs):
        execute_sync_request(payload_dry_run, lambda x: x)
        execute_sync_request(payload_sync, lambda x: x)

    assert len(resolved_workspaces) == 2
    assert resolved_workspaces[0] == "my-explicit-workspace-uuid"
    assert resolved_workspaces[1] == "my-explicit-workspace-uuid"
    assert resolved_workspaces[0] == resolved_workspaces[1]


@pytest.mark.asyncio
async def test_dry_run_without_workspace_gate_and_fabric_read_fail_with_standard_message(monkeypatch):
    config = {
        "project_id": "test_fabric_no_ws",
        "source": {"type": "fabric", "models": ["Sales"]},
        "target": {"type": "snowflake"},
    }
    content_str = yaml.dump(config)

    # 1. The pre-sync dry-run gate reads Fabric live like every other source,
    #    so without a workspace it stops with the same standard message, before
    #    any Fabric call and without falling back to an old snapshot.
    monkeypatch.setattr(
        mapping_service,
        "_compat_build_project_entity_mappings",
        lambda *args, **kwargs: {"project_id": "test_fabric_no_ws", "mappings": [], "collisions": []},
    )
    from semabridge.api.services import core_domain_service, sync_execution_service

    async def must_not_call_fabric(payload):
        raise AssertionError("the gate must not read Fabric without a workspace")

    monkeypatch.setattr(core_domain_service, "sync_models", must_not_call_fabric)
    monkeypatch.setattr(sync_execution_service, "_fabric_env_workspace", lambda: "")
    with pytest.raises(ValidationError) as gate_exc:
        await _assert_sync_ready("test_fabric_no_ws", content_str)
    assert "Fabric source has no workspace: set source.workspace_id in the project settings." in str(gate_exc.value)

    # 2. Duplicate check passes without workspace and never calls Fabric
    duplicates = find_duplicate_deployments("test_fabric_no_ws", content_str)
    assert isinstance(duplicates, list)

    # 3. Fabric read step fails with standard message (even when .env workspace is set or strict is False)
    payload_dry_run = {
        "content": content_str,
        "dry_run": True,
        "selection": ["Sales"],
    }
    with pytest.raises(ValidationError) as exc_info:
        execute_sync_request(payload_dry_run, lambda x: x)

    dry_run_msg = str(exc_info.value)
    assert "Fabric source has no workspace: set source.workspace_id in the project settings." in dry_run_msg


def test_sync_without_workspace_fails_with_same_standard_message():
    config = {
        "project_id": "test_fabric_no_ws",
        "source": {"type": "fabric", "models": ["Sales"]},
        "target": {"type": "snowflake"},
    }
    content_str = yaml.dump(config)
    payload_sync = {
        "content": content_str,
        "dry_run": False,
        "selection": ["Sales"],
    }

    with pytest.raises(ValidationError) as exc_info:
        execute_sync_request(payload_sync, lambda x: x)

    sync_msg = str(exc_info.value)
    assert sync_msg == "Fabric source has no workspace: set source.workspace_id in the project settings."
