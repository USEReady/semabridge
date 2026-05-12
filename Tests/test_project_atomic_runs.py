from __future__ import annotations

import asyncio
from pathlib import Path

from semabridge.api.services import project_runs_impl as pri


def _reset_runs_state() -> None:
    pri._compat_projects.clear()
    pri._compat_project_configs.clear()
    pri._compat_project_runs.clear()
    pri._compat_project_snapshots.clear()
    pri._compat_snapshot_groups.clear()
    pri._compat_run_snapshots.clear()
    pri._compat_folders.clear()
    pri._compat_mappings.clear()
    pri._compat_project_schedules.clear()
    pri._compat_store_loaded = False


def _seed_projects() -> None:
    pri._compat_projects["proj-a"] = {"id": "proj-a", "project_id": "proj-a", "name": "A", "source": "fabric"}
    pri._compat_projects["proj-b"] = {"id": "proj-b", "project_id": "proj-b", "name": "B", "source": "fabric"}
    pri._compat_projects["proj-c"] = {"id": "proj-c", "project_id": "proj-c", "name": "C", "source": "fabric"}


def test_atomic_runs_dry_run_orders_by_dependencies(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_runs_state()
    _seed_projects()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies:\n    - proj-b\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies: []\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        pri.run_projects_atomic_compat(
            {"project_ids": ["proj-a", "proj-b"], "dry_run": True}
        )
    )

    assert result["status"] == "success"
    assert result["dry_run"] is True
    assert result["ordered_project_ids"] == ["proj-b", "proj-a"]
    assert [r["status"] for r in result["results"]] == ["planned", "planned"]


def test_atomic_runs_fail_fast_and_sets_rollback_intent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _reset_runs_state()
    _seed_projects()

    async def _fake_perform(run, project_cfg, started):
        pid = str(run.get("project_id") or "")
        if pid == "proj-b":
            run["status"] = "failed"
            run["message"] = "synthetic failure"
        else:
            run["status"] = "success"
            run["message"] = "ok"
        return run

    monkeypatch.setattr(pri, "_perform_project_run", _fake_perform)

    result = asyncio.run(
        pri.run_projects_atomic_compat(
            {"project_ids": ["proj-a", "proj-b", "proj-c"], "fail_fast": True}
        )
    )

    statuses = {item["project_id"]: item["status"] for item in result["results"]}
    assert result["status"] == "failed"
    assert result["rollback_intent"] is True
    assert statuses["proj-a"] == "success"
    assert statuses["proj-b"] == "failed"
    assert statuses["proj-c"] == "skipped"


def test_atomic_runs_execute_rollback_in_reverse_order(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _reset_runs_state()
    _seed_projects()

    calls: list[tuple[str, str]] = []

    async def _fake_perform(run, project_cfg, started):
        pid = str(run.get("project_id") or "")
        run_type = str(run.get("run_type") or "SYNC")
        calls.append((run_type, pid))
        if run_type == "SYNC" and pid == "proj-c":
            run["status"] = "failed"
            run["message"] = "synthetic failure"
        else:
            run["status"] = "success"
            run["message"] = "ok"
        return run

    monkeypatch.setattr(pri, "_perform_project_run", _fake_perform)

    result = asyncio.run(
        pri.run_projects_atomic_compat(
            {"project_ids": ["proj-a", "proj-b", "proj-c"], "fail_fast": True, "execute_rollback_on_failure": True}
        )
    )

    assert result["status"] == "failed"
    assert result["rollback_intent"] is True
    assert result["rollback_executed"] is True
    rollback_projects = [r["project_id"] for r in result["rollback_results"]]
    assert rollback_projects == ["proj-b", "proj-a"]
    assert calls == [
        ("SYNC", "proj-a"),
        ("SYNC", "proj-b"),
        ("SYNC", "proj-c"),
        ("RESTORE", "proj-b"),
        ("RESTORE", "proj-a"),
    ]


def test_atomic_runs_can_disable_rollback_execution(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _reset_runs_state()
    _seed_projects()

    async def _fake_perform(run, project_cfg, started):
        pid = str(run.get("project_id") or "")
        if pid == "proj-b":
            run["status"] = "failed"
        else:
            run["status"] = "success"
        run["message"] = "synthetic"
        return run

    monkeypatch.setattr(pri, "_perform_project_run", _fake_perform)

    result = asyncio.run(
        pri.run_projects_atomic_compat(
            {"project_ids": ["proj-a", "proj-b"], "execute_rollback_on_failure": False}
        )
    )
    assert result["rollback_intent"] is True
    assert result["rollback_executed"] is False
    assert result["rollback_results"] == []
