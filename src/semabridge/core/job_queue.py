"""Durable job queue and workers for background project runs.

Execution modes (``SEMABRIDGE_EXECUTION_MODE``):

* ``inline`` -- the request starts a daemon thread (the historical
  behaviour; default on the ``local`` profile). A crash loses the run.
* ``queue``  -- the request writes a ``run_jobs`` row and returns. Workers
  claim rows atomically, heartbeat while executing, and a row whose worker
  stopped heartbeating is re-queued (``max_attempts``, default 2) by any
  process's maintenance loop. Default on hosted profiles.

Workers run embedded in the API process (``SEMABRIDGE_EMBEDDED_WORKERS``,
default 2 locally, 0 on hosted profiles where a separate worker app runs
``semabridge-worker``) or as their own process.

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` on PostgreSQL; other
databases use a compare-and-swap ``UPDATE ... WHERE status='queued'``, which
is safe across threads and processes sharing one database file.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

Handler = Callable[[Dict[str, Any], "JobContext"], None]
_handlers: Dict[str, Handler] = {}
_handlers_lock = threading.Lock()


class JobCancelled(RuntimeError):
    """Raised inside a handler (via JobContext.raise_if_cancelled) to stop work."""


@dataclass
class ClaimedJob:
    job_id: str
    kind: str
    project_id: Optional[str]
    payload: Dict[str, Any]
    attempts: int
    max_attempts: int


class JobContext:
    """What a handler can ask about its job while it runs."""

    def __init__(self, job: ClaimedJob) -> None:
        self.job = job

    @property
    def is_retry(self) -> bool:
        return self.job.attempts > 1

    def cancel_requested(self) -> bool:
        return is_cancel_requested(self.job.job_id)

    def raise_if_cancelled(self) -> None:
        if self.cancel_requested():
            raise JobCancelled(f"Job {self.job.job_id} was cancelled.")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def execution_mode() -> str:
    raw = str(os.environ.get("SEMABRIDGE_EXECUTION_MODE", "") or "").strip().lower()
    if raw in {"inline", "queue"}:
        return raw
    from semabridge.core.deployment_profile import is_production

    return "queue" if is_production() else "inline"


def queue_enabled() -> bool:
    return execution_mode() == "queue"


def embedded_worker_count() -> int:
    raw = os.environ.get("SEMABRIDGE_EMBEDDED_WORKERS")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    from semabridge.core.deployment_profile import is_production

    return 0 if is_production() else 2


def _poll_seconds() -> float:
    try:
        return max(0.2, float(os.environ.get("SEMABRIDGE_JOB_POLL_SECONDS", "2")))
    except ValueError:
        return 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _session():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager.get_session()


def ensure_table() -> None:
    from semabridge.repository.orm.job_models import RunJob
    from semabridge.repository.orm.session_factory import db_manager

    RunJob.__table__.create(bind=db_manager.get_engine(), checkfirst=True)


def register_handler(kind: str, handler: Handler) -> None:
    with _handlers_lock:
        _handlers[kind] = handler


def _handler_for(kind: str) -> Optional[Handler]:
    with _handlers_lock:
        handler = _handlers.get(kind)
    if handler is None:
        # Handlers register on import of the module that owns the work.
        try:
            import semabridge.api.services.run_service  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            logger.error("Could not load job handlers: %s", exc)
        with _handlers_lock:
            handler = _handlers.get(kind)
    return handler


# ---------------------------------------------------------------------------
# Queue operations
# ---------------------------------------------------------------------------


def enqueue(kind: str, payload: Dict[str, Any], *, job_id: Optional[str] = None,
            project_id: Optional[str] = None, max_attempts: int = 2) -> str:
    """Persist a job; returns its id. Raises when the database rejects it, so
    a request never reports "queued" for work that was not stored."""
    from semabridge.repository.orm.job_models import RunJob

    ensure_table()
    jid = str(job_id or uuid.uuid4())
    with _session() as session:
        session.add(RunJob(
            job_id=jid, kind=kind, project_id=project_id,
            payload_json=json.dumps(payload, default=str),
            status="queued", attempts=0, max_attempts=max(1, int(max_attempts)),
            created_at=_now(),
        ))
        session.commit()
    _wake.set()
    logger.info("Queued %s job %s (project %s)", kind, jid, project_id)
    return jid


def claim_next(worker_id: str) -> Optional[ClaimedJob]:
    """Atomically claim the oldest queued job, or None."""
    from sqlalchemy import select, update

    from semabridge.repository.orm.job_models import RunJob

    now = _now()
    with _session() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            row = session.execute(
                select(RunJob).where(RunJob.status == "queued", RunJob.cancel_requested.is_(False))
                .order_by(RunJob.created_at).limit(1).with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                session.rollback()
                return None
            row.status, row.claimed_by, row.claimed_at, row.heartbeat_at = "claimed", worker_id, now, now
            row.attempts = int(row.attempts or 0) + 1
            job = _to_claimed(row)
            session.commit()
            return job
        # Compare-and-swap: only the first claimer's UPDATE matches.
        for _ in range(5):
            candidate = session.execute(
                select(RunJob.job_id).where(RunJob.status == "queued", RunJob.cancel_requested.is_(False))
                .order_by(RunJob.created_at).limit(1)
            ).scalar_one_or_none()
            if candidate is None:
                return None
            result = session.execute(
                update(RunJob).where(RunJob.job_id == candidate, RunJob.status == "queued")
                .values(status="claimed", claimed_by=worker_id, claimed_at=now, heartbeat_at=now,
                        attempts=RunJob.attempts + 1)
                .execution_options(synchronize_session=False)
            )
            session.commit()
            if result.rowcount == 1:
                row = session.get(RunJob, candidate)
                session.refresh(row)
                return _to_claimed(row)
        return None


def _to_claimed(row: Any) -> ClaimedJob:
    try:
        payload = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):
        payload = {}
    return ClaimedJob(
        job_id=row.job_id, kind=row.kind, project_id=row.project_id, payload=payload,
        attempts=int(row.attempts or 0), max_attempts=int(row.max_attempts or 1),
    )


def heartbeat(job_ids: List[str], worker_id: str) -> None:
    from sqlalchemy import update

    from semabridge.repository.orm.job_models import RunJob

    if not job_ids:
        return
    with _session() as session:
        session.execute(
            update(RunJob).where(RunJob.job_id.in_(job_ids), RunJob.claimed_by == worker_id, RunJob.status == "claimed")
            .values(heartbeat_at=_now()).execution_options(synchronize_session=False)
        )
        session.commit()


def finish(job_id: str, *, status: str, error: Optional[str] = None) -> None:
    from semabridge.repository.orm.job_models import RunJob

    with _session() as session:
        row = session.get(RunJob, job_id)
        if row is None:
            return
        row.status = status
        row.finished_at = _now()
        if error:
            row.last_error = str(error)[:4000]
        session.commit()


def request_cancel(job_id: str) -> bool:
    """Ask a job to stop. A queued job is cancelled now; a running one stops
    at its next cancellation check. Returns False when no such job is open."""
    from semabridge.repository.orm.job_models import RunJob

    with _session() as session:
        row = session.get(RunJob, job_id)
        if row is None or row.status not in ("queued", "claimed"):
            return False
        row.cancel_requested = True
        if row.status == "queued":
            row.status = "cancelled"
            row.finished_at = _now()
        session.commit()
        return True


def is_cancel_requested(job_id: str) -> bool:
    from semabridge.repository.orm.job_models import RunJob

    try:
        with _session() as session:
            row = session.get(RunJob, job_id)
            return bool(row and row.cancel_requested)
    except Exception:  # noqa: BLE001
        return False


def job_status(job_id: str) -> Optional[Dict[str, Any]]:
    from semabridge.repository.orm.job_models import RunJob

    with _session() as session:
        row = session.get(RunJob, job_id)
        if row is None:
            return None
        return {
            "job_id": row.job_id, "kind": row.kind, "status": row.status, "attempts": row.attempts,
            "max_attempts": row.max_attempts, "claimed_by": row.claimed_by, "last_error": row.last_error,
        }


def open_job_ids() -> List[str]:
    """Queued jobs (their project leases must stay alive while they wait)."""
    from sqlalchemy import select

    from semabridge.repository.orm.job_models import RunJob

    with _session() as session:
        return [r for (r,) in session.execute(select(RunJob.job_id).where(RunJob.status == "queued")).all()]


def requeue_stale(now: Optional[datetime] = None) -> Dict[str, int]:
    """Re-queue claimed jobs whose worker stopped heartbeating; fail the ones
    out of attempts. Safe to run from every process."""
    from sqlalchemy import select

    from semabridge.core.run_coordination import lease_seconds
    from semabridge.repository.orm.job_models import RunJob

    now = now or _now()
    cutoff = now - timedelta(seconds=lease_seconds())
    counts = {"requeued": 0, "failed": 0}
    with _session() as session:
        rows = session.execute(select(RunJob).where(RunJob.status == "claimed")).scalars().all()
        for row in rows:
            seen = _aware(row.heartbeat_at) or _aware(row.claimed_at)
            if seen is not None and seen >= cutoff:
                continue
            if row.cancel_requested:
                row.status, row.finished_at = "cancelled", now
                continue
            if int(row.attempts or 0) < int(row.max_attempts or 1):
                row.status, row.claimed_by = "queued", None
                row.last_error = f"Worker stopped heartbeating; re-queued (attempt {row.attempts} of {row.max_attempts})."
                counts["requeued"] += 1
            else:
                row.status, row.finished_at = "failed", now
                row.last_error = f"Worker stopped heartbeating after {row.attempts} attempt(s)."
                counts["failed"] += 1
        session.commit()
    if counts["requeued"] or counts["failed"]:
        logger.warning("Job maintenance: %s", counts)
        if counts["requeued"]:
            _wake.set()
    return counts


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

_wake = threading.Event()


def run_one(worker_id: str) -> Optional[str]:
    """Claim and execute one job in this thread. Returns its final status."""
    job = claim_next(worker_id)
    if job is None:
        return None
    handler = _handler_for(job.kind)
    if handler is None:
        finish(job.job_id, status="failed", error=f"No handler registered for job kind {job.kind!r}.")
        return "failed"
    stop = threading.Event()

    def _beat() -> None:
        from semabridge.core.run_coordination import heartbeat_seconds

        while not stop.wait(heartbeat_seconds()):
            try:
                heartbeat([job.job_id], worker_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Job heartbeat failed: %s", exc)

    beater = threading.Thread(target=_beat, name=f"job-hb-{job.job_id[:8]}", daemon=True)
    beater.start()
    try:
        handler(job.payload, JobContext(job))
        finish(job.job_id, status="done")
        return "done"
    except JobCancelled as exc:
        finish(job.job_id, status="cancelled", error=str(exc))
        return "cancelled"
    except Exception as exc:  # noqa: BLE001 -- recorded on the job row
        logger.exception("Job %s (%s) failed", job.job_id, job.kind)
        finish(job.job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        return "failed"
    finally:
        stop.set()


class WorkerPool:
    """N worker threads plus one maintenance thread (stale re-queue, lease
    renewal for waiting jobs, stale-run sweep)."""

    def __init__(self, size: int, worker_id: Optional[str] = None) -> None:
        from semabridge.core.run_coordination import WORKER_ID

        self.size = max(0, int(size))
        self.worker_id = worker_id or WORKER_ID
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    def start(self) -> "WorkerPool":
        ensure_table()
        for i in range(self.size):
            t = threading.Thread(target=self._work, name=f"job-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        m = threading.Thread(target=self._maintain, name="job-maintenance", daemon=True)
        m.start()
        self._threads.append(m)
        logger.info("Started %d job worker(s) (%s)", self.size, self.worker_id)
        return self

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        _wake.set()
        for t in self._threads:
            t.join(timeout=timeout)

    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                status = run_one(self.worker_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Job worker error: %s", exc)
                status = None
            if status is None:
                _wake.wait(_poll_seconds())
                _wake.clear()

    def _maintain(self) -> None:
        from semabridge.core.run_coordination import heartbeat_once, heartbeat_seconds

        interval = max(5.0, float(heartbeat_seconds()))
        while not self._stop.wait(interval):
            run_maintenance(heartbeat_once)


def run_maintenance(heartbeat_fn: Optional[Callable[[List[str]], None]] = None) -> None:
    """One maintenance pass: re-queue stale jobs, keep waiting jobs' run rows
    and project leases alive, sweep stale runs."""
    try:
        requeue_stale()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Job re-queue pass failed: %s", exc)
    if heartbeat_fn is not None:
        try:
            waiting = open_job_ids()
            if waiting:
                heartbeat_fn(waiting)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Waiting-job heartbeat failed: %s", exc)
    try:
        from semabridge.api.services.run_service import _sweep_stale_runs

        _sweep_stale_runs(force=True)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Stale-run sweep failed: %s", exc)


_embedded: Optional[WorkerPool] = None


def start_embedded_workers() -> Optional[WorkerPool]:
    """Start in-process workers when the queue is enabled (API lifespan)."""
    global _embedded
    if not queue_enabled():
        return None
    if _embedded is None:
        _embedded = WorkerPool(embedded_worker_count()).start()
    return _embedded


def stop_embedded_workers() -> None:
    global _embedded
    if _embedded is not None:
        _embedded.stop()
        _embedded = None


def main() -> None:  # pragma: no cover - process entry point
    """``semabridge-worker``: a dedicated worker process (no HTTP)."""
    import signal

    from semabridge.utils.logger import setup_logging

    setup_logging(level=os.environ.get("SEMABRIDGE_LOG_LEVEL", "INFO"))
    size = int(os.environ.get("SEMABRIDGE_WORKER_THREADS", "4") or 4)
    pool = WorkerPool(size).start()
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    done.wait()
    logger.info("Worker shutting down; in-flight jobs are re-queued by the next maintenance pass if they do not finish.")
    pool.stop(timeout=30.0)


if __name__ == "__main__":  # pragma: no cover
    main()
