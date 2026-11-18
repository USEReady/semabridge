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


_STARTED: list = []


def _fake_start_gated_project_run(project_id, schedule_label="Manual"):
    # Triggers go through run_service._start_gated_project_run (same
    # saved-name merge + dry-run gate as run-now), which starts the run.
    _STARTED.append(project_id)
    return {"id": f"run-{project_id}", "project_id": project_id}


_REQUESTS: list = []


async def _fake_start_project_run_request(project_id, background_tasks, payload, **kwargs):
    # A single-project trigger runs run-now's synchronous checks.
    _REQUESTS.append((project_id, dict(payload), kwargs))
    return {"run_id": f"run-{project_id}", "status": "queued", "run_type": "SYNC"}


@pytest.fixture(autouse=True)
def _wire_fakes(monkeypatch):
    monkeypatch.setattr(ss, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(ss, "_compat_project_schedules", {})
    monkeypatch.setattr(run_service, "_start_gated_project_run", _fake_start_gated_project_run)
    monkeypatch.setattr(run_service, "_start_project_run_request", _fake_start_project_run_request)
    _STARTED.clear()
    _REQUESTS.clear()


def _cron(enabled=True):
    return {"schedule_type": "cron", "cron": "0 * * * *", "enabled": enabled}


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
    assert _STARTED == []


@pytest.mark.asyncio
async def test_no_project_id_triggers_only_scheduled_projects_the_user_owns(monkeypatch):
    monkeypatch.setattr(ss, "_compat_projects", {
        "proj-scheduled-owned": {"name": "A"},
        "proj-scheduled-not-owned": {"name": "B"},
        "proj-unscheduled": {"name": "C"},  # not scheduled -- must never be triggered
    })
    monkeypatch.setattr(ss, "_compat_project_schedules", {
        "proj-scheduled-owned": _cron(), "proj-scheduled-not-owned": _cron(),
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
    assert len(_STARTED) == 1


@pytest.mark.asyncio
async def test_no_project_id_triggers_all_scheduled_projects_when_all_owned(monkeypatch):
    monkeypatch.setattr(ss, "_compat_projects", {"proj-1": {}, "proj-2": {}, "proj-3": {}, "proj-4": {}, "proj-5": {}})
    monkeypatch.setattr(ss, "_compat_project_schedules", {
        "proj-1": _cron(),
        "proj-2": {"schedule_type": "time", "date": "2099-01-01", "time": "10:00", "enabled": True},
        # proj-3: unscheduled; proj-4: disabled; proj-5: manual -- all excluded.
        "proj-4": _cron(enabled=False),
        "proj-5": {"schedule_type": "manual", "enabled": True},
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

    result = await ss.trigger_job_compat(
        {"project_id": "proj-x", "user_id": "9", "acknowledge_duplicates": True}, background_tasks,
    )

    assert result["project_id"] == "proj-x" and result["id"] == "run-proj-x"
    # The single-project trigger runs run-now's checks synchronously (a
    # refusal is a 409 for the UI dialog), not the background gated start.
    assert _STARTED == []
    assert len(_REQUESTS) == 1
    pid, body, kwargs = _REQUESTS[0]
    assert pid == "proj-x" and body["acknowledge_duplicates"] is True and body["user_id"] == "9"
    assert kwargs.get("run_type") == "SYNC"


@pytest.mark.asyncio
async def test_explicit_project_id_refusal_is_a_409_not_an_accepted_run(monkeypatch):
    from fastapi import HTTPException

    async def refuse(project_id, background_tasks, payload, **kwargs):
        raise HTTPException(status_code=409, detail={"status": "removed_fields", "models": [], "total": 3})

    monkeypatch.setattr(ss, "_compat_projects", {"proj-x": {}})
    monkeypatch.setattr(run_service, "_start_project_run_request", refuse)
    with pytest.raises(HTTPException) as exc:
        await ss.trigger_job_compat({"project_id": "proj-x"}, _FakeBackgroundTasks())
    assert exc.value.status_code == 409 and exc.value.detail["status"] == "removed_fields"
    assert _STARTED == []
