"""Regression tests for the scheduled-run concurrency cap in scheduler_service.py.

Context: APScheduler fires every project's own independent cron/time trigger
with no concurrency limit of its own. When several projects share the same
(or an overlapping) schedule -- true today for many of the projects sharing
one Snowflake target schema, see incident_shared_schema_cross_project_
contamination -- they'd otherwise all fire as fully concurrent threads,
which is exactly the precondition for the destructive CTAS+SWAP
cross-project table race already confirmed for that incident. The
semaphore added to SchedulerService caps how many scheduled runs execute at
once; these tests confirm it actually serializes/limits concurrency AND
that a run delayed behind the cap still eventually fires rather than being
dropped.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from semabridge.api.services.scheduler_service import SchedulerService


def _make_tracking_callback(max_observed: list[int], completed: list[str]):
    """A fake run_project_callback that records peak concurrency and which
    project_ids actually completed, safely across the real OS threads
    asyncio.to_thread spins up for each call."""
    lock = threading.Lock()
    state = {"current": 0}

    async def _callback(project_id: str, schedule_label: str) -> dict:
        with lock:
            state["current"] += 1
            max_observed[0] = max(max_observed[0], state["current"])
        try:
            # Force a real overlap window so a broken cap would be caught.
            await asyncio.sleep(0.05)
        finally:
            with lock:
                state["current"] -= 1
        with lock:
            completed.append(project_id)
        return {"status": "success"}

    return _callback


@pytest.mark.asyncio
async def test_concurrent_scheduled_runs_never_exceed_the_cap(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_SCHEDULER_MAX_CONCURRENT_RUNS", "2")
    service = SchedulerService()
    assert service._max_concurrent_runs == 2

    max_observed = [0]
    completed: list[str] = []
    service.configure(_make_tracking_callback(max_observed, completed), None)

    project_ids = [f"proj-{i}" for i in range(5)]
    await asyncio.gather(*(
        service._run_scheduled_project(pid, "cron") for pid in project_ids
    ))

    assert max_observed[0] <= 2, "cap of 2 was exceeded -- runs fired fully concurrently"


@pytest.mark.asyncio
async def test_a_run_queued_behind_the_cap_still_eventually_fires(monkeypatch):
    """A run that arrives while the cap is reached must be delayed, never
    dropped -- every project_id passed in must show up in `completed`."""
    monkeypatch.setenv("SEMABRIDGE_SCHEDULER_MAX_CONCURRENT_RUNS", "1")
    service = SchedulerService()

    max_observed = [0]
    completed: list[str] = []
    service.configure(_make_tracking_callback(max_observed, completed), None)

    project_ids = [f"proj-{i}" for i in range(4)]
    await asyncio.gather(*(
        service._run_scheduled_project(pid, "cron") for pid in project_ids
    ))

    assert max_observed[0] == 1, "cap of 1 should fully serialize runs"
    assert sorted(completed) == sorted(project_ids), (
        "every scheduled run must eventually complete, none dropped by the cap"
    )


@pytest.mark.asyncio
async def test_default_cap_is_two_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_SCHEDULER_MAX_CONCURRENT_RUNS", raising=False)
    service = SchedulerService()
    assert service._max_concurrent_runs == 2


@pytest.mark.asyncio
async def test_time_type_schedule_is_still_cleared_after_a_queued_run(monkeypatch):
    """The one-time-schedule cleanup (delete_project_schedule) must still
    happen after a run that was delayed behind the concurrency cap."""
    monkeypatch.setenv("SEMABRIDGE_SCHEDULER_MAX_CONCURRENT_RUNS", "1")
    service = SchedulerService()

    cleared: list[str] = []
    monkeypatch.setattr(service, "delete_project_schedule", lambda pid: cleared.append(pid))

    max_observed = [0]
    completed: list[str] = []
    service.configure(_make_tracking_callback(max_observed, completed), None)

    await asyncio.gather(
        service._run_scheduled_project("proj-a", "cron"),
        service._run_scheduled_project("proj-b", "time"),
    )

    assert cleared == ["proj-b"]
