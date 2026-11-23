"""Tests verifying identical workspace resolution between dry run and real sync.

Requirements:
- Dry run resolves workspace exactly the same way as real sync.
- No fallback to a "default" workspace or server default.
- A project without a workspace fails dry run and sync with the exact same error.
- A project with a workspace resolves it identically in dry run and sync.
"""

import pytest
import yaml
from unittest.mock import patch

from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.domain.exceptions import ValidationError


def test_project_without_workspace_fails_dry_run_and_sync_with_same_error():
    config = {
        "project_id": "test_fabric_no_ws",
        "source": {"type": "fabric", "models": ["Sales"]},
        "target": {"type": "snowflake"},
        "behavior": {"features": {"fabric_strict_workspace": True}},
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

    dry_run_err = None
    try:
        execute_sync_request(payload_dry_run, lambda x: x)
    except ValidationError as exc:
        dry_run_err = str(exc)

    sync_err = None
    try:
        execute_sync_request(payload_sync, lambda x: x)
    except ValidationError as exc:
        sync_err = str(exc)

    assert dry_run_err is not None, "Dry run must fail when workspace is missing"
    assert sync_err is not None, "Real sync must fail when workspace is missing"
    assert dry_run_err == sync_err
    assert "Fabric source has no workspace" in dry_run_err


def test_project_with_workspace_resolves_identically_in_dry_run_and_sync():
    config = {
        "project_id": "test_fabric_with_ws",
        "source": {"type": "fabric", "workspace_id": "my-explicit-workspace-uuid", "models": ["Sales"]},
        "target": {"type": "snowflake"},
        "behavior": {"features": {"fabric_strict_workspace": True}},
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
