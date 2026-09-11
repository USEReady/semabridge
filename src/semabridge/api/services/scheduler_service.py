from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone as dt_timezone
from threading import RLock
from typing import Any, Awaitable, Callable, Dict, Optional
from zoneinfo import ZoneInfo

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger
    _APSCHEDULER_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    AsyncIOScheduler = None  # type: ignore[assignment]
    CronTrigger = None  # type: ignore[assignment]
    DateTrigger = None  # type: ignore[assignment]
    _APSCHEDULER_IMPORT_ERROR = exc

logger = logging.getLogger("semabridge.api.scheduler")

ProjectRunCallback = Callable[[str, str], Awaitable[Dict[str, Any]]]
ScheduleClearedCallback = Callable[[str], None]


class SchedulerService:
    """In-memory project scheduler built on top of APScheduler."""

    def __init__(self) -> None:
        self._available = AsyncIOScheduler is not None
        self._scheduler = AsyncIOScheduler(timezone="UTC") if AsyncIOScheduler is not None else None
        self._run_project_callback: Optional[ProjectRunCallback] = None
        self._schedule_cleared_callback: Optional[ScheduleClearedCallback] = None
        self._schedules: Dict[str, Dict[str, Any]] = {}
        self._lock = RLock()
        # Cap how many scheduled project syncs run at once. APScheduler fires
        # every project's own independent cron/time trigger with no
        # concurrency limit of its own -- when several projects share the
        # same (or a coincidentally overlapping) schedule, as many of the
        # existing projects sharing one Snowflake target schema do today,
        # they'd otherwise all fire as fully concurrent threads. That
        # concurrency is exactly the precondition for the destructive
        # CTAS+SWAP cross-project table race confirmed in
        # incident_shared_schema_cross_project_contamination (project
        # memory) -- capping concurrency meaningfully reduces collision risk
        # for the existing projects still sharing a schema, without touching
        # any per-project schedule or requiring the bigger migration.
        # A run that arrives while the cap is already reached queues behind
        # the ones in flight and fires as soon as a slot frees up -- see
        # _run_scheduled_project below -- it is delayed, never dropped:
        # APScheduler's own trigger has already fired by the time this
        # semaphore is touched, so queuing here cannot cause a missed run,
        # only a later start time for that run.
        self._max_concurrent_runs = max(1, int(os.getenv("SEMABRIDGE_SCHEDULER_MAX_CONCURRENT_RUNS", "2")))
        self._run_semaphore = asyncio.Semaphore(self._max_concurrent_runs)

    def start(self) -> None:
        if not self._available or self._scheduler is None:
            logger.warning("Project scheduler disabled because APScheduler is not installed")
            return
        if self._scheduler.running:
            return
        self._scheduler.start()
        logger.info("Project scheduler started")

    def shutdown(self) -> None:
        if not self._available or self._scheduler is None:
            return
        if not self._scheduler.running:
            return
        self._scheduler.shutdown(wait=False)
        logger.info("Project scheduler stopped")

    def configure(
        self,
        run_project_callback: ProjectRunCallback,
        schedule_cleared_callback: Optional[ScheduleClearedCallback] = None,
    ) -> None:
        self._run_project_callback = run_project_callback
        self._schedule_cleared_callback = schedule_cleared_callback

    def get_project_schedule(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            schedule = self._schedules.get(str(project_id))
            if not schedule:
                return None
            return self._with_live_next_run(dict(schedule))

    def list_schedules(self) -> list[Dict[str, Any]]:
        with self._lock:
            return [self._with_live_next_run(dict(item)) for item in self._schedules.values()]

    def delete_project_schedule(self, project_id: str) -> Optional[Dict[str, Any]]:
        job_id = self._job_id(project_id)
        with self._lock:
            removed = self._schedules.pop(str(project_id), None)
        if self._scheduler is not None and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        if removed and self._schedule_cleared_callback:
            self._schedule_cleared_callback(str(project_id))
        return dict(removed) if removed else None

    def save_project_schedule(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._available or self._scheduler is None or CronTrigger is None or DateTrigger is None:
            raise ValueError(
                "Scheduler backend is unavailable because APScheduler is not installed. "
                "Install apscheduler>=3.10.4 and restart the API."
            )

        project_id = str(project_id)
        schedule_type = str((payload or {}).get("schedule_type") or "").strip().lower() or "manual"
        timezone_name = str((payload or {}).get("timezone") or "UTC").strip() or "UTC"
        cron = str((payload or {}).get("cron") or "").strip()
        scheduled_time_value = str((payload or {}).get("scheduled_time") or "").strip()
        date_value = str((payload or {}).get("date") or "").strip()
        time_value = str((payload or {}).get("time") or "").strip()
        enabled = bool((payload or {}).get("enabled", True))
        timezone = self._parse_timezone(timezone_name)

        if schedule_type == "manual":
            self.delete_project_schedule(project_id)
            return {
                "project_id": project_id,
                "schedule_type": "manual",
                "enabled": False,
                "message": "Manual trigger selected. No background schedule is active.",
            }

        job_id = self._job_id(project_id)
        trigger: Any
        next_run_at: Optional[datetime] = None

        if schedule_type == "cron":
            if not cron:
                raise ValueError("Cron expression is required.")
            try:
                trigger = CronTrigger.from_crontab(cron, timezone=timezone)
            except ValueError as exc:
                raise ValueError(f"Invalid cron expression: {exc}") from exc
            next_run_at = trigger.get_next_fire_time(None, datetime.now(timezone))
        elif schedule_type == "time":
            if scheduled_time_value:
                run_date = self._parse_utc_datetime(scheduled_time_value)
            else:
                if not date_value or not time_value:
                    raise ValueError("Schedule date and time are required.")
                run_date = self._parse_run_date(date_value, time_value, timezone).astimezone(dt_timezone.utc)

            if run_date <= datetime.now(dt_timezone.utc):
                raise ValueError("Scheduled time must be in the future.")
            trigger = DateTrigger(run_date=run_date, timezone=dt_timezone.utc)
            next_run_at = run_date
        else:
            raise ValueError("Unsupported schedule type. Use manual, cron, or time.")

        job = self._scheduler.add_job(
            self._run_scheduled_project,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            kwargs={
                "project_id": project_id,
                "schedule_type": schedule_type,
            },
        )

        schedule = {
            "id": job.id,
            "project_id": project_id,
            "schedule_type": schedule_type,
            "cron": cron if schedule_type == "cron" else "",
            "date": date_value if schedule_type == "time" else "",
            "time": time_value if schedule_type == "time" else "",
            "timezone": timezone_name,
            "enabled": enabled,
            "scheduled_time": self._to_utc_iso(next_run_at) if next_run_at else "",
            "next_run_at": self._to_utc_iso(next_run_at) if next_run_at else None,
            "created_at": self._to_utc_iso(datetime.now(dt_timezone.utc)),
        }

        with self._lock:
            self._schedules[project_id] = schedule

        return dict(schedule)

    async def _run_scheduled_project(self, project_id: str, schedule_type: str) -> None:
        if not self._run_project_callback:
            logger.warning("Scheduled run skipped because no run callback is configured")
            return

        schedule_label = "Scheduled" if schedule_type == "time" else "Cron"
        try:
            # Project sync performs blocking connector/LLM/warehouse work. Keep it
            # off the Uvicorn event loop so lightweight API requests stay responsive.
            def _run_callback_in_thread() -> None:
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(self._run_project_callback(project_id, schedule_label))
                finally:
                    loop.close()

            if self._run_semaphore.locked():
                logger.info(
                    "Scheduled run for project %s is queued (%d scheduled run(s) already "
                    "in progress, cap=%d) -- will start as soon as a slot frees up",
                    project_id, self._max_concurrent_runs, self._max_concurrent_runs,
                )
            async with self._run_semaphore:
                await asyncio.to_thread(_run_callback_in_thread)
        finally:
            if schedule_type == "time":
                self.delete_project_schedule(project_id)

    @staticmethod
    def _job_id(project_id: str) -> str:
        return f"project-schedule:{project_id}"

    @staticmethod
    def _parse_timezone(timezone_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError(f"Unsupported timezone: {timezone_name}") from exc

    @staticmethod
    def _parse_utc_datetime(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Invalid UTC scheduled_time.") from exc

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed.astimezone(dt_timezone.utc)

    @staticmethod
    def _parse_run_date(date_value: str, time_value: str, timezone: ZoneInfo) -> datetime:
        try:
            return datetime.fromisoformat(f"{date_value}T{time_value}:00").replace(tzinfo=timezone)
        except ValueError as exc:
            raise ValueError("Invalid schedule date or time.") from exc

    @staticmethod
    def _to_utc_iso(value: Optional[datetime]) -> Optional[str]:
        if not value:
            return None
        utc_value = value.astimezone(dt_timezone.utc)
        return utc_value.isoformat().replace("+00:00", "Z")

    def _with_live_next_run(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        project_id = str(schedule.get("project_id") or "")
        if not project_id or self._scheduler is None:
            return schedule
        job = self._scheduler.get_job(self._job_id(project_id))
        if job and getattr(job, "next_run_time", None):
            schedule["next_run_at"] = self._to_utc_iso(job.next_run_time)
            if schedule.get("schedule_type") == "time":
                schedule["scheduled_time"] = schedule["next_run_at"]
        elif schedule.get("schedule_type") == "time":
            schedule["next_run_at"] = None
        return schedule
