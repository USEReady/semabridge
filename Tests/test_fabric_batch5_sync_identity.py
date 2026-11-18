"""Batch 5 fix items 1-2: the CLI and the orchestrator adapter carry the job's Fabric identity/workspace.

No network: the orchestrator / engine / Temporal client are replaced at their
module boundary. temporalio's native bridge is blocked on some machines, so the
Temporal test installs minimal stand-in temporalio modules for the import.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import types
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner


# ── item 1: CLI ──────────────────────────────────────────────────────────────
@pytest.fixture
def cli(monkeypatch):
    import semabridge.cli.sync_commands as sc
    import semabridge.sync.orchestrator as so
    import semabridge.sync.repository as sr

    jobs = []

    class _Orch:
        def __init__(self, repository=None):
            pass

        def run(self, config, initiated_by):
            jobs.append(config)
            return SimpleNamespace(job_id="j1")

    monkeypatch.setattr(so, "SyncOrchestrator", _Orch)
    monkeypatch.setattr(sr, "SyncRepository", lambda *a, **k: None)
    monkeypatch.setattr(sc, "_print_job_summary", lambda job: None)
    import semabridge.core.config_loader as cl
    monkeypatch.setattr(cl, "get_default_config_path", lambda: None)
    return sc.sync_app, jobs


def test_cli_fabric_sync_builds_job_with_identity_and_names_missing_options(cli, monkeypatch):
    app, jobs = cli
    runner = CliRunner()
    ok = runner.invoke(app, ["run", "--direction", "fabric_to_snowflake",
                             "--fabric-identity-id", "acct-1", "--fabric-workspace-id", "ws-1"])
    assert ok.exit_code == 0, ok.output
    assert (jobs[-1].fabric_identity_id, jobs[-1].fabric_workspace_id) == ("acct-1", "ws-1")

    missing = runner.invoke(app, ["run", "--direction", "fabric_to_snowflake", "--fabric-workspace-id", "ws-1"])
    assert missing.exit_code == 1 and "--fabric-identity-id" in missing.output and len(jobs) == 1

    import semabridge.sources.fabric.dax_target as dt

    monkeypatch.setattr(dt, "load_project_config", lambda pid: {"source": {"type": "fabric", "identity_id": "acct-p", "workspace_id": "ws-p"}})
    from_project = runner.invoke(app, ["run", "--direction", "fabric_to_snowflake", "--project", "proj-x"])
    assert from_project.exit_code == 0, from_project.output
    assert (jobs[-1].fabric_identity_id, jobs[-1].fabric_workspace_id) == ("acct-p", "ws-p")

    pbix = runner.invoke(app, ["run", "--direction", "pbix_to_snowflake", "--pbix-folder", "x"])
    assert pbix.exit_code == 0 and jobs[-1].fabric_identity_id is None  # non-Fabric directions unchanged
