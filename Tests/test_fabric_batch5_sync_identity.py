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


# ── item 2: orchestrator adapter, local and Temporal paths ───────────────────
@pytest.fixture
def project_config(monkeypatch):
    import semabridge.sources.fabric.dax_target as dt

    monkeypatch.setattr(dt, "load_project_config", lambda pid: {
        "source": {"type": "fabric", "identity_id": "acct-proj", "workspace_id": "ws-proj", "models": ["Sales"]}})


def test_adapter_local_path_sets_identity_from_project_config(monkeypatch, project_config):
    import semabridge.core.engine as ce
    from semabridge.orchestration import orchestrator_adapter as oa
    from semabridge.sources.base import SourceConfigError

    from semabridge.core.run_summary import RunStatus

    seen = {}

    class _Engine:  # the local path uses ExecutionEngine().execute(...), like the main sync
        def execute(self, **kw):
            seen.update(kw)
            return SimpleNamespace(status=RunStatus.SUCCESS, errors=[])

    monkeypatch.setattr(ce, "ExecutionEngine", _Engine)
    res = asyncio.run(oa.run_sync("t", "Sales", "DB", "SCH", mode="local", project_id="proj-1"))
    assert res.status == "completed" and res.deployed is True
    src = seen["config_dict"]["source"]
    assert (src["identity_id"], src["workspace_id"], seen["workspace_id"]) == ("acct-proj", "ws-proj", "ws-proj")

    with pytest.raises(SourceConfigError, match="fabric_identity_id"):
        asyncio.run(oa.run_sync("t", "Sales", "DB", "SCH", mode="local", fabric_workspace_id="ws-1"))


def _fake_temporalio(monkeypatch, captured):
    pkg = types.ModuleType("temporalio")
    wf = types.ModuleType("temporalio.workflow")
    wf.unsafe = SimpleNamespace(imports_passed_through=contextlib.nullcontext)
    wf.defn = lambda *a, **k: (lambda cls: cls)
    wf.run = lambda fn: fn

    def _passthrough_decorator(name):  # workflow.signal / query / update ... used as decorators
        return lambda *a, **k: a[0] if (len(a) == 1 and callable(a[0]) and not k) else (lambda fn: fn)

    wf.__getattr__ = _passthrough_decorator
    common = types.ModuleType("temporalio.common")
    common.RetryPolicy = lambda **k: SimpleNamespace(**k)
    act = types.ModuleType("temporalio.activity")
    act.defn = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
    act.heartbeat = lambda *a, **k: None
    client_mod = types.ModuleType("temporalio.client")

    class _Client:
        @staticmethod
        async def connect(host, namespace=None):
            return _Client()

        async def execute_workflow(self, name, inp, id=None, task_queue=None):
            captured["inp"] = inp
            return {"run_id": inp.run_id, "tenant_id": inp.tenant_id, "model_id": inp.model_id, "phase": "completed"}

    client_mod.Client = _Client
    pkg.workflow, pkg.common, pkg.activity, pkg.client = wf, common, act, client_mod
    for name, mod in {"temporalio": pkg, "temporalio.workflow": wf, "temporalio.common": common,
                      "temporalio.activity": act, "temporalio.client": client_mod}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for name in ("semabridge.orchestration.temporal.workflows", "semabridge.orchestration.temporal.activities"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def test_adapter_temporal_path_sends_identity_and_emit_uses_it(monkeypatch, project_config):
    from semabridge.orchestration import orchestrator_adapter as oa

    captured = {}
    _fake_temporalio(monkeypatch, captured)
    res = asyncio.run(oa.run_sync("t", "Sales", "DB", "SCH", mode="temporal", project_id="proj-1"))
    assert res.status == "completed"
    assert (captured["inp"].fabric_identity_id, captured["inp"].fabric_workspace_id) == ("acct-proj", "ws-proj")

    # the emit step publishes with the job's identity, not the server FABRIC_* env
    import semabridge.orchestration.temporal.activities as acts

    used = {}
    monkeypatch.setattr("semabridge.sources.fabric.legacy_sync.fabric_publisher_for_sync",
                        lambda ws, ident: used.update(ws=ws, ident=ident) or SimpleNamespace(
                            publish=lambda *a, **k: SimpleNamespace(model_id="m1", success=True)))
    asyncio.run(acts.emit_to_fabric(acts.EmitInput(tenant_id="t", model_id="Sales", sml_json="{}",
                                                   fabric_workspace_id="ws-proj", fabric_identity_id="acct-proj")))
    assert used == {"ws": "ws-proj", "ident": "acct-proj"}
