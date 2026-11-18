"""Cross-process run coordination: liveness, leases, capacity, scheduler leader.

Everything here is backed by the database so it holds across worker
processes, ``executor: "process"`` model workers and server restarts.

Liveness (heartbeats)
    A run registers itself while it executes (``register_live_run``). One
    daemon thread per process refreshes ``runs.heartbeat_at`` for every
    registered run (and for the model rows linked to it by
    ``parent_run_id``) every ``SEMABRIDGE_RUN_HEARTBEAT_SECONDS`` (15 s), and
    renews the run's leases. ``sweep_stale_runs`` marks a running/queued run
    "interrupted" only when its heartbeat is older than
    ``SEMABRIDGE_RUN_LEASE_SECONDS`` (120 s) -- a run that is still working
    is never timed out, however long it takes, and a run orphaned by a crash
    or restart is detected by any process.

Leases (mutual exclusion)
    ``run_leases`` rows keyed ``project:<id>`` (one sync of a project at a
    time) or ``target:<db>.<schema>.<object>`` (objects a deploy writes). A
    lease is held by a ``holder`` on behalf of a run and expires unless the
    run keeps heartbeating, so a crashed holder blocks nobody for longer than
    the lease. Dry runs never take leases.

Capacity (global, across processes)
    ``SEMABRIDGE_MAX_CONCURRENT_MODELS`` (4) model executions shared by syncs
    and dry runs (``model_slot``) and ``SEMABRIDGE_MAX_CONCURRENT_RUNS`` (2)
    project syncs (run_service). Each cap is a ``SlotPool``: N slot leases
    ``slot:<pool>:<i>`` in ``run_leases``, taken and heartbeated like any
    other lease, so N server processes together never exceed the cap. An
    in-process semaphore of the same size gates each process first and is
    the only gate when the lease table is unavailable. A full pool queues
    the caller (it waits; it never fails).

Scheduler leadership
    ``SchedulerLeadership`` holds a PostgreSQL session advisory lock on a
    dedicated connection; only the holder fires scheduled jobs.
"""
from __future__ import annotations

import contextvars
import logging
import os
import socket
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def lease_seconds() -> int:
    return _env_int("SEMABRIDGE_RUN_LEASE_SECONDS", 120, minimum=5)


def heartbeat_seconds() -> int:
    return _env_int("SEMABRIDGE_RUN_HEARTBEAT_SECONDS", 15, minimum=1)


WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"

ACTIVE_STATUSES = ("running", "queued")
INTERRUPTED_MESSAGE = (
    "Run interrupted: the server process executing it stopped (no heartbeat for {seconds}s). "
    "Nothing further ran after that point; start the run again."
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _session():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager.get_session()


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------

_live_lock = threading.Lock()
_live_runs: Dict[str, int] = {}
_hb_thread: Optional[threading.Thread] = None
_hb_stop = threading.Event()


def register_live_run(run_id: Optional[str]) -> None:
    """Mark ``run_id`` as executing in this process (re-entrant)."""
    if not run_id:
        return
    with _live_lock:
        _live_runs[str(run_id)] = _live_runs.get(str(run_id), 0) + 1
    _ensure_heartbeat_thread()


def unregister_live_run(run_id: Optional[str]) -> None:
    if not run_id:
        return
    with _live_lock:
        remaining = _live_runs.get(str(run_id), 0) - 1
        if remaining > 0:
            _live_runs[str(run_id)] = remaining
        else:
            _live_runs.pop(str(run_id), None)


def live_run_ids() -> List[str]:
    with _live_lock:
        return list(_live_runs)


def is_live_here(run_id: Optional[str]) -> bool:
    with _live_lock:
        return str(run_id or "") in _live_runs


def heartbeat_once(run_ids: Optional[Iterable[str]] = None) -> None:
    """Refresh heartbeat_at of the given (default: all live) runs, their
    linked model rows, and the leases they hold."""
    ids = [str(r) for r in (run_ids if run_ids is not None else live_run_ids()) if r]
    if not ids:
        return
    from sqlalchemy import or_, update

    from semabridge.repository.orm.models import Run, RunLease

    now = utcnow()
    with _session() as session:
        session.execute(
            update(Run)
            .where(or_(Run.run_id.in_(ids), Run.parent_run_id.in_(ids)))
            .where(Run.status.in_(ACTIVE_STATUSES))
            .values(heartbeat_at=now)
            .execution_options(synchronize_session=False)
        )
        session.execute(
            update(RunLease)
            .where(RunLease.run_id.in_(ids))
            .values(heartbeat_at=now, expires_at=now + timedelta(seconds=lease_seconds()))
            .execution_options(synchronize_session=False)
        )
        session.commit()


def _heartbeat_loop() -> None:
    while not _hb_stop.wait(heartbeat_seconds()):
        try:
            heartbeat_once()
        except Exception as exc:  # noqa: BLE001 -- never let the thread die
            logger.debug("Run heartbeat failed: %s", exc)


def _ensure_heartbeat_thread() -> None:
    global _hb_thread
    with _live_lock:
        if _hb_thread is not None and _hb_thread.is_alive():
            return
        _hb_stop.clear()
        _hb_thread = threading.Thread(target=_heartbeat_loop, name="run-heartbeat", daemon=True)
        _hb_thread.start()


@contextmanager
def live_run(run_id: Optional[str]) -> Iterator[None]:
    """Heartbeat ``run_id`` for the duration of the block (first beat now)."""
    register_live_run(run_id)
    try:
        try:
            heartbeat_once([run_id] if run_id else [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Initial heartbeat failed for %s: %s", run_id, exc)
        yield
    finally:
        unregister_live_run(run_id)


def sweep_stale_runs(now: Optional[datetime] = None) -> int:
    """Mark running/queued runs whose heartbeat (or start, if they never beat)
    is older than the lease as "interrupted". Never touches a run executing
    in this process. Returns the number of rows changed."""
    from sqlalchemy import select

    from semabridge.repository.orm.models import Run

    now = now or utcnow()
    cutoff = now - timedelta(seconds=lease_seconds())
    live = set(live_run_ids())
    changed = 0
    with _session() as session:
        rows = session.execute(select(Run).where(Run.status.in_(ACTIVE_STATUSES))).scalars().all()
        for row in rows:
            if row.run_id in live or (row.parent_run_id and row.parent_run_id in live):
                continue
            last_seen = _aware(row.heartbeat_at) or _aware(row.started_at)
            if last_seen is not None and last_seen >= cutoff:
                continue
            row.status = "interrupted"
            message = INTERRUPTED_MESSAGE.format(seconds=lease_seconds())
            row.error_message = row.error_message or message
            if row.run_kind == "project":
                row.message = message
            row.completed_at = row.completed_at or now
            changed += 1
        session.commit()
    if changed:
        logger.warning("Marked %d run row(s) interrupted (heartbeat older than %ss)", changed, lease_seconds())
    return changed


# ---------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------


@dataclass
class LeaseResult:
    acquired: bool
    key: str
    holder: Optional[str] = None
    run_id: Optional[str] = None
    project_id: Optional[str] = None

    def __bool__(self) -> bool:
        return self.acquired


class LeaseBusyError(RuntimeError):
    def __init__(self, result: LeaseResult, message: Optional[str] = None):
        self.result = result
        super().__init__(message or f"'{result.key}' is held by run {result.run_id}")


# In-process fallback, used only when the database is unreachable.
_mem_leases: Dict[str, Dict[str, Any]] = {}
_mem_lock = threading.Lock()


def _mem_acquire(key: str, run_id: str, holder: str, project_id: Optional[str]) -> LeaseResult:
    now = utcnow()
    with _mem_lock:
        cur = _mem_leases.get(key)
        if cur and cur["holder"] != holder and cur["expires_at"] > now:
            return LeaseResult(False, key, cur["holder"], cur["run_id"], cur.get("project_id"))
        _mem_leases[key] = {
            "holder": holder, "run_id": run_id, "project_id": project_id,
            "expires_at": now + timedelta(seconds=lease_seconds()),
        }
    return LeaseResult(True, key, holder, run_id, project_id)


_lease_tables_ready: set = set()
_lease_tables_lock = threading.Lock()


def _ensure_lease_table() -> None:
    """Create ``run_leases`` on the current engine if missing (fresh SQLite
    test DBs); Alembic owns it on PostgreSQL."""
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import RunLease
    from semabridge.repository.orm.session_factory import db_manager

    engine = db_manager.get_engine()
    key = id(engine)
    if key in _lease_tables_ready:
        return
    with _lease_tables_lock:
        if key not in _lease_tables_ready:
            Base.metadata.create_all(bind=engine, tables=[RunLease.__table__], checkfirst=True)
            _lease_tables_ready.add(key)


def _db_acquire_lease(
    key: str, *, run_id: str, holder: str, lease_type: str, project_id: Optional[str],
) -> LeaseResult:
    """Take the lease in the database; raises when the table is unavailable."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from semabridge.repository.orm.models import RunLease

    now = utcnow()
    expires = now + timedelta(seconds=lease_seconds())
    _ensure_lease_table()
    with _session() as session:
        row = session.execute(
            select(RunLease).where(RunLease.lease_key == key).with_for_update()
        ).scalar_one_or_none()
        if row is not None:
            if row.holder != holder and (_aware(row.expires_at) or now) > now:
                return LeaseResult(False, key, row.holder, row.run_id, row.project_id)
            if row.holder != holder:
                # Take over an expired lease with a compare-and-swap on the
                # row we read: FOR UPDATE already serializes this on
                # PostgreSQL, the condition also covers databases without
                # row locks (SQLite) -- two takers can never both win.
                from sqlalchemy import update

                old_holder, old_expires = row.holder, row.expires_at
                session.expunge(row)
                taken = session.execute(
                    update(RunLease)
                    .where(RunLease.lease_key == key)
                    .where(RunLease.holder == old_holder)
                    .where(RunLease.expires_at == old_expires)
                    .values(
                        holder=holder, run_id=str(run_id), lease_type=lease_type, project_id=project_id,
                        acquired_at=now, heartbeat_at=now, expires_at=expires,
                    )
                    .execution_options(synchronize_session=False)
                ).rowcount
                session.commit()
                if taken != 1:
                    current = session.get(RunLease, key)
                    return LeaseResult(False, key, getattr(current, "holder", None), getattr(current, "run_id", None))
                return LeaseResult(True, key, holder, str(run_id), project_id)
            row.holder, row.run_id, row.lease_type, row.project_id = holder, str(run_id), lease_type, project_id
            row.heartbeat_at, row.expires_at = now, expires
        else:
            session.add(RunLease(
                lease_key=key, lease_type=lease_type, run_id=str(run_id), holder=holder,
                project_id=project_id, acquired_at=now, heartbeat_at=now, expires_at=expires,
            ))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            current = session.get(RunLease, key)
            return LeaseResult(False, key, getattr(current, "holder", None), getattr(current, "run_id", None))
    return LeaseResult(True, key, holder, str(run_id), project_id)


def acquire_lease(
    key: str, *, run_id: str, holder: Optional[str] = None, lease_type: str = "target",
    project_id: Optional[str] = None,
) -> LeaseResult:
    """Take (or re-take, same holder) the lease ``key``. Never blocks."""
    holder = str(holder or run_id)
    try:
        return _db_acquire_lease(key, run_id=str(run_id), holder=holder, lease_type=lease_type, project_id=project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Lease table unavailable (%s); using in-process lease for %s", exc, key)
        return _mem_acquire(key, str(run_id), holder, project_id)


def lease_holder(key: str) -> Optional[LeaseResult]:
    """The current, unexpired holder of ``key`` (None when free)."""
    from semabridge.repository.orm.models import RunLease

    now = utcnow()
    try:
        with _session() as session:
            row = session.get(RunLease, key)
            if row is None or (_aware(row.expires_at) or now) <= now:
                return None
            return LeaseResult(False, key, row.holder, row.run_id, row.project_id)
    except Exception:  # noqa: BLE001
        with _mem_lock:
            cur = _mem_leases.get(key)
            if cur and cur["expires_at"] > now:
                return LeaseResult(False, key, cur["holder"], cur["run_id"], cur.get("project_id"))
        return None


def release_leases(*, holder: Optional[str] = None, run_id: Optional[str] = None, key: Optional[str] = None) -> None:
    """Release leases by holder, by run, or one key (expires them now; the
    row is reused by the next acquirer)."""
    from sqlalchemy import update

    from semabridge.repository.orm.models import RunLease

    if not (holder or run_id or key):
        return
    now = utcnow()
    with _mem_lock:
        for k, cur in list(_mem_leases.items()):
            if (holder and cur["holder"] == holder) or (run_id and cur["run_id"] == run_id) or (key and k == key):
                _mem_leases.pop(k, None)
    try:
        with _session() as session:
            stmt = update(RunLease)
            if holder:
                stmt = stmt.where(RunLease.holder == str(holder))
            if run_id:
                stmt = stmt.where(RunLease.run_id == str(run_id))
            if key:
                stmt = stmt.where(RunLease.lease_key == key)
            session.execute(stmt.values(expires_at=now).execution_options(synchronize_session=False))
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Lease release failed (%s); it expires on its own", exc)


def project_lease_key(project_id: str) -> str:
    return f"project:{project_id}"


def target_lease_key(database: Any, schema: Any, obj: Any) -> str:
    parts = [str(p or "").strip().strip('"').upper() for p in (database, schema, obj)]
    return "target:" + ".".join(p for p in parts if p)


# Who is deploying in this context: (project run id, holder, project id).
# Set by the sync job around one model's engine run; read by the emitter to
# lease every object it is about to write.
_deploy_holder: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "semabridge_deploy_holder", default=None
)


@contextmanager
def deploy_holder(run_id: str, holder: str, project_id: Optional[str]) -> Iterator[None]:
    token = _deploy_holder.set((str(run_id), str(holder), project_id))
    try:
        yield
    finally:
        _deploy_holder.reset(token)


def current_deploy_holder() -> Optional[tuple]:
    return _deploy_holder.get()


def acquire_target_objects(database: Any, schema: Any, objects: Iterable[str]) -> List[str]:
    """Lease every object (``db.schema.object``) for the current deploy
    holder; raises LeaseBusyError naming the first busy object. No-op when no
    deploy holder is set (e.g. a direct engine call outside a run)."""
    ctx = current_deploy_holder()
    if not ctx:
        return []
    run_id, holder, project_id = ctx
    keys = sorted({target_lease_key(database, schema, o) for o in objects if str(o or "").strip()})
    for key in keys:
        result = acquire_lease(key, run_id=run_id, holder=holder, lease_type="target", project_id=project_id)
        if not result:
            raise LeaseBusyError(
                result,
                f"CONFLICT: '{key.split(':', 1)[1]}' is being deployed by another active run "
                f"({result.run_id}); try again when it finishes.",
            )
    return keys


# ---------------------------------------------------------------------------
# Global model-execution capacity
# ---------------------------------------------------------------------------


def max_concurrent_models() -> int:
    return _env_int("SEMABRIDGE_MAX_CONCURRENT_MODELS", 4)


def slot_poll_seconds() -> float:
    try:
        return max(0.05, float(os.getenv("SEMABRIDGE_SLOT_POLL_SECONDS", "0.5")))
    except (TypeError, ValueError):
        return 0.5


def global_slots_enabled() -> bool:
    """Whether the capacity caps use database slot leases.

    ``SEMABRIDGE_GLOBAL_SLOTS``: ``on`` / ``off`` / ``auto`` (default). Auto
    means PostgreSQL only: SQLite and DuckDB are single-process databases
    here (see SchedulerLeadership), and SQLite's shared connection would
    make slot rows unreliable, so the in-process semaphore is the cap."""
    mode = str(os.getenv("SEMABRIDGE_GLOBAL_SLOTS", "auto")).strip().lower()
    if mode in {"on", "true", "1", "yes"}:
        return True
    if mode in {"off", "false", "0", "no"}:
        return False
    try:
        from semabridge.repository.orm.session_factory import db_manager

        return db_manager.get_engine().dialect.name == "postgresql"
    except Exception as exc:  # noqa: BLE001
        logger.debug("Database dialect unknown (%s); capacity caps are per process", exc)
        return False


def _is_write_contention(exc: BaseException) -> bool:
    """A lock / serialization conflict (retry) rather than a missing or
    unreachable lease table (fall back)."""
    text = str(exc).lower()
    return any(t in text for t in (
        "database is locked", "database table is locked", "busy", "deadlock",
        "could not serialize", "lock timeout", "lock not available",
    ))


@dataclass
class SlotToken:
    """One held slot of a ``SlotPool`` (``key`` is None when only the
    in-process semaphore was taken because the lease table is unavailable)."""

    pool: "SlotPool"
    holder: str
    key: Optional[str]
    local: Any


class SlotPool:
    """A capacity cap that holds across server processes.

    ``limit()`` slot leases ``slot:<name>:<i>`` in ``run_leases`` (heartbeated
    while held, so a crashed holder frees its slot after the lease time) plus
    an in-process semaphore of the same size, taken first. When the lease
    table cannot be used the in-process semaphore alone applies (the same
    fallback the other leases use). ``acquire`` waits until a slot is free;
    it never fails because the pool is full."""

    def __init__(self, name: str, limit: Callable[[], int], local: Callable[[], Any]):
        self.name = name
        self._limit = limit
        self._local = local
        self._cond = threading.Condition()

    def limit(self) -> int:
        return max(1, int(self._limit()))

    def keys(self) -> List[str]:
        return [f"slot:{self.name}:{i}" for i in range(self.limit())]

    def _take_db_slot(self, holder: str) -> Any:
        """The leased key, None when the lease table is unavailable (or
        global slots are off), False when every slot is held by someone else."""
        if not global_slots_enabled():
            return None
        for key in self.keys():
            try:
                result = _db_acquire_lease(key, run_id=holder, holder=holder, lease_type="slot", project_id=None)
            except Exception as exc:  # noqa: BLE001 -- fall back to the in-process gate
                if _is_write_contention(exc):
                    # Another process is writing the same slot row right
                    # now: treat as busy and retry, never as "no table".
                    logger.debug("Slot %s contended (%s); retrying", key, exc)
                    continue
                logger.warning(
                    "Lease table unavailable (%s); %s slots are capped per process only", exc, self.name,
                )
                return None
            if result:
                register_live_run(holder)  # heartbeat renews the slot lease
                return key
        return False

    def try_acquire(self) -> Optional[SlotToken]:
        """A slot now, or None when the pool is full (never blocks)."""
        import uuid

        local = self._local()
        if not local.acquire(blocking=False):
            return None
        holder = f"slot-{self.name}-{WORKER_ID}-{uuid.uuid4().hex[:12]}"
        try:
            key = self._take_db_slot(holder)
        except BaseException:
            local.release()
            raise
        if key is False:
            local.release()
            return None
        return SlotToken(self, holder, key, local)

    def acquire(self) -> SlotToken:
        """Wait for a slot (queued callers keep waiting; nothing fails)."""
        import uuid

        local = self._local()
        local.acquire()
        holder = f"slot-{self.name}-{WORKER_ID}-{uuid.uuid4().hex[:12]}"
        try:
            while True:
                key = self._take_db_slot(holder)
                if key is not False:
                    return SlotToken(self, holder, key, local)
                # Held by other processes: woken by a local release, else poll.
                with self._cond:
                    self._cond.wait(slot_poll_seconds())
        except BaseException:
            local.release()
            raise

    def release(self, token: Optional[SlotToken]) -> None:
        if token is None:
            return
        try:
            if token.key:
                release_leases(key=token.key, holder=token.holder)
                unregister_live_run(token.holder)
        finally:
            token.local.release()
            with self._cond:
                self._cond.notify_all()

    def held_globally(self) -> Optional[int]:
        """Unexpired slot leases of this pool in the database (None if unreadable)."""
        from sqlalchemy import func, select

        from semabridge.repository.orm.models import RunLease

        try:
            with _session() as session:
                return int(session.execute(
                    select(func.count()).select_from(RunLease)
                    .where(RunLease.lease_key.in_(self.keys()))
                    .where(RunLease.expires_at > utcnow())
                ).scalar() or 0)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Slot usage of %s unreadable: %s", self.name, exc)
            return None


_model_slots = threading.BoundedSemaphore(max_concurrent_models())
# Global model-execution slots; the in-process gate is read at call time so
# tests (and a reload) can swap ``_model_slots``.
model_slots = SlotPool("model", max_concurrent_models, lambda: _model_slots)
_slot_depth = threading.local()
_slot_stats_lock = threading.Lock()
_slot_stats = {"active": 0, "waiting": 0}
_child_slot_tokens: List[SlotToken] = []
_child_slot_lock = threading.Lock()

# Called (once per model) when a model execution obtains its slot -- the
# owning run flips from "queued" to "running" there.
model_started_callback: contextvars.ContextVar[Optional[Callable[[], None]]] = contextvars.ContextVar(
    "semabridge_model_started_callback", default=None
)


def capacity_snapshot() -> Dict[str, int]:
    with _slot_stats_lock:
        return {"limit": max_concurrent_models(), **_slot_stats}


def _acquire_model_slot() -> SlotToken:
    token = model_slots.try_acquire()
    if token is not None:
        return token
    with _slot_stats_lock:
        _slot_stats["waiting"] += 1
    logger.info("Model execution queued: all %d global model slots are busy", max_concurrent_models())
    try:
        return model_slots.acquire()
    finally:
        with _slot_stats_lock:
            _slot_stats["waiting"] -= 1


def _fire_model_started() -> None:
    callback = model_started_callback.get()
    if callback is not None:
        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            logger.debug("model-started callback failed: %s", exc)


@contextmanager
def model_slot() -> Iterator[None]:
    """Hold one of the global model-execution slots (re-entrant per thread:
    nested model work in a thread that already holds a slot never waits)."""
    depth = getattr(_slot_depth, "n", 0)
    if depth:
        _slot_depth.n = depth + 1
        try:
            yield
        finally:
            _slot_depth.n -= 1
        return
    token = _acquire_model_slot()
    with _slot_stats_lock:
        _slot_stats["active"] += 1
    _slot_depth.n = 1
    try:
        _fire_model_started()
        yield
    finally:
        _slot_depth.n = 0
        with _slot_stats_lock:
            _slot_stats["active"] -= 1
        model_slots.release(token)


def acquire_model_slot_for_child() -> None:
    """Parent-side slot for a model executed in another process (process
    executor); pair with ``release_model_slot_for_child``."""
    token = _acquire_model_slot()
    with _child_slot_lock:
        _child_slot_tokens.append(token)
    with _slot_stats_lock:
        _slot_stats["active"] += 1
    _fire_model_started()


def release_model_slot_for_child(*_: Any) -> None:
    with _child_slot_lock:
        token = _child_slot_tokens.pop() if _child_slot_tokens else None
    with _slot_stats_lock:
        _slot_stats["active"] -= 1
    model_slots.release(token)


# ---------------------------------------------------------------------------
# Scheduler leadership
# ---------------------------------------------------------------------------

SCHEDULER_LOCK_KEY = 72_910_332  # app-wide constant (alembic runner uses ..331)


class SchedulerLeadership:
    """Only the process holding a PostgreSQL session advisory lock fires
    scheduled jobs. The lock lives on a dedicated connection held for the
    process lifetime; when that process exits the lock is released and the
    next process to check takes over. Non-PostgreSQL databases (tests, local
    SQLite/DuckDB) are single-process: always leader."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn: Any = None
        self._leader = False

    def is_leader(self) -> bool:
        with self._lock:
            if self._leader and self._connection_alive():
                return True
            self._leader = False
            try:
                from sqlalchemy import text

                from semabridge.repository.orm.session_factory import db_manager

                engine = db_manager.get_engine()
                if engine.dialect.name != "postgresql":
                    self._leader = True
                    return True
                if self._conn is None:
                    self._conn = engine.connect()
                got = bool(self._conn.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": SCHEDULER_LOCK_KEY}
                ).scalar())
                self._conn.commit()
                self._leader = got
                if got:
                    logger.info("This process (%s) is the scheduler leader", WORKER_ID)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Scheduler leadership check failed: %s", exc)
                self._drop_connection()
            return self._leader

    def _connection_alive(self) -> bool:
        try:
            from sqlalchemy import text

            self._conn.execute(text("SELECT 1"))
            self._conn.commit()
            return True
        except Exception:  # noqa: BLE001
            self._drop_connection()
            return False

    def _drop_connection(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None
        self._leader = False

    def release(self) -> None:
        with self._lock:
            self._drop_connection()


scheduler_leadership = SchedulerLeadership()


# ---------------------------------------------------------------------------
# Bounded worker pool for blocking pipeline work called from async code
# ---------------------------------------------------------------------------

_pipeline_pool: Any = None
_pipeline_pool_lock = threading.Lock()
_in_pipeline_worker = threading.local()


def pipeline_workers() -> int:
    return _env_int("SEMABRIDGE_PIPELINE_WORKERS", 8)


def _pool() -> Any:
    global _pipeline_pool
    with _pipeline_pool_lock:
        if _pipeline_pool is None:
            from concurrent.futures import ThreadPoolExecutor

            _pipeline_pool = ThreadPoolExecutor(max_workers=pipeline_workers(), thread_name_prefix="pipeline")
        return _pipeline_pool


def _call_marked(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    _in_pipeline_worker.active = True
    try:
        return fn(*args, **kwargs)
    finally:
        _in_pipeline_worker.active = False


async def run_blocking(fn: Callable[..., Any], *args: Any, timeout: Optional[float] = None, **kwargs: Any) -> Any:
    """Run blocking pipeline work (sync engine, dry runs) in the bounded
    pipeline pool so the event loop stays responsive. The caller's
    contextvars (log scope, run callbacks) go with it. Called from a thread
    that is already a pipeline worker it runs inline (no nested pool
    waits, so the bounded pool can never deadlock on itself)."""
    import asyncio
    import functools

    if getattr(_in_pipeline_worker, "active", False):
        return fn(*args, **kwargs)
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    future = loop.run_in_executor(_pool(), functools.partial(ctx.run, _call_marked, fn, *args, **kwargs))
    if timeout is None:
        return await future
    return await asyncio.wait_for(future, timeout)


def run_coroutine_blocking(coro_factory: Callable[[], Any]) -> Any:
    """Run an async pipeline function to completion on a private event loop
    in the current (worker) thread."""
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro_factory())
    finally:
        loop.close()
