"""A disabled schedule never fires: no APScheduler job on save or restore,
and a job that fires after the schedule was disabled does nothing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from semabridge.api.services.scheduler_service import SchedulerService

CRON = {"schedule_type": "cron", "cron": "0 9 * * 1", "timezone": "UTC"}


def test_disabled_schedule_is_kept_but_registers_no_job():
    async def _test():
        service = SchedulerService()
        service.start()
        try:
            saved = service.save_project_schedule("proj-off", {**CRON, "enabled": False})
            assert saved["enabled"] is False and saved["next_run_at"] is None
            assert service._scheduler.get_job(service._job_id("proj-off")) is None
            assert service.get_project_schedule("proj-off")["enabled"] is False

            # Re-enabling registers the job; disabling again removes it.
            service.save_project_schedule("proj-off", {**CRON, "enabled": True})
            assert service._scheduler.get_job(service._job_id("proj-off")) is not None
            service.save_project_schedule("proj-off", {**CRON, "enabled": False})
            assert service._scheduler.get_job(service._job_id("proj-off")) is None

            # Omitted "enabled" keeps the previous default (enabled).
            assert service.save_project_schedule("proj-default", dict(CRON))["enabled"] is True
        finally:
            service.shutdown()

    asyncio.run(_test())


def test_run_of_a_disabled_schedule_does_not_call_the_sync():
    async def _test():
        service = SchedulerService()
        service.start()
        callback = AsyncMock()
        service.configure(callback)
        try:
            service.save_project_schedule("proj-off", {**CRON, "enabled": False})
            await service._run_scheduled_project("proj-off", "cron")
            callback.assert_not_called()
            # Removed altogether: nothing to run either.
            await service._run_scheduled_project("proj-unknown", "cron")
            callback.assert_not_called()
        finally:
            service.shutdown()

    asyncio.run(_test())
