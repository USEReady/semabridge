"""Regression tests for schedule_service.trigger_job_compat's "no project_id"
fallback.

Context: this used to fall back to running EVERY project in the compat
store (not just scheduled ones) whenever nothing was explicitly scheduled --
a single "Run Now" click with no project context produced 310+ concurrent
project runs in a real incident (see incident_run_now_fires_every_project
in project memory). The fix removes that fallback entirely (empty schedule
list -> a clear no-op) and adds a per-project ownership check to the
"trigger every scheduled project" branch, which previously bypassed
ownership entirely since there was no single project_id for the controller
to check ahead of time.
"""
from __future__ import annotations

import pytest

from semabridge.api.services import schedule_service as ss
from semabridge.api.services import run_service
from semabridge.api.services import project_ownership_service as pos


class _FakeBackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, func, *args, **kwargs):
        self.calls.append((func, args, kwargs))


def _fake_create_project_run(project_id, schedule_label="Manual"):
    return {"id": f"run-{project_id}", "project_id": project_id}, "project_name: test\n", 0.0


@pytest.fixture(autouse=True)
def _wire_fakes(monkeypatch):
    monkeypatch.setattr(ss, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(run_service, "_create_project_run", _fake_create_project_run)
    monkeypatch.setattr(run_service, "_run_project_background", lambda run, cfg, started: None)


@pytest.mark.asyncio
async def test_no_project_id_and_nothing_scheduled_is_a_clear_noop(monkeypatch):
    monkeypatch.setattr(ss, "_compat_projects", {
        "proj-a": {"name": "A"},
        "proj-b": {"name": "B"},
    })
    background_tasks = _FakeBackgroundTasks()

    result = await ss.trigger_job_compat({}, background_tasks)

    assert result == {"status": "no_projects", "message": "No scheduled jobs to run.", "triggered": 0}
    # The old bug: falling back to list(_compat_projects.keys()) and
    # triggering everything. Confirm nothing was dispatched at all.
    assert background_tasks.calls == []


@pytest.mark.asyncio
async def test_no_project_id_triggers_only_scheduled_projects_the_user_owns(monkeypatch):
    monkeypatch.setattr(ss, "_compat_projects", {
        "proj-scheduled-owned": {"name": "A", "schedule_type": "cron"},
        "proj-scheduled-not-owned": {"name": "B", "schedule_type": "cron"},
        "proj-unscheduled": {"name": "C"},  # not scheduled -- must never be triggered
    })
    monkeypatch.setattr(
        pos, "is_project_owned_by_user",
        lambda project_id, user_id, **kwargs: project_id == "proj-scheduled-owned",
    )
    background_tasks = _FakeBackgroundTasks()

    result = await ss.trigger_job_compat({"user_id": "7"}, background_tasks)

    assert result["status"] == "triggered"
    assert result["triggered"] == 1
    triggered_ids = {r["project_id"] for r in result["runs"]}
    assert triggered_ids == {"proj-scheduled-owned"}
    assert len(background_tasks.calls) == 1


@pytest.mark.asyncio
async def test_no_project_id_triggers_all_scheduled_projects_when_all_owned(monkeypatch):
    monkeypatch.setattr(ss, "_compat_projects", {
        "proj-1": {"schedule_type": "cron"},
        "proj-2": {"schedule": {"cron": "* * * * *"}},
        "proj-3": {},  # unscheduled, must be excluded
    })
    monkeypatch.setattr(pos, "is_project_owned_by_user", lambda project_id, user_id, **kwargs: True)
    background_tasks = _FakeBackgroundTasks()

    result = await ss.trigger_job_compat({"user_id": "1"}, background_tasks)

    assert result["status"] == "triggered"
    assert result["triggered"] == 2
    triggered_ids = {r["project_id"] for r in result["runs"]}
    assert triggered_ids == {"proj-1", "proj-2"}


@pytest.mark.asyncio
async def test_explicit_project_id_is_unaffected_by_scheduled_project_logic(monkeypatch):
    monkeypatch.setattr(ss, "_compat_projects", {"proj-x": {}})
    background_tasks = _FakeBackgroundTasks()

    result = await ss.trigger_job_compat({"project_id": "proj-x"}, background_tasks)

    assert result["project_id"] == "proj-x"
    assert len(background_tasks.calls) == 1
