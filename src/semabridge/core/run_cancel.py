"""Cancel a project run that is queued or already executing.

``request_cancel`` records the request durably (shared state + the job row),
so it reaches the run wherever it executes. Execution checks cooperatively:
the engine calls ``check()`` at every step boundary of every model, which
raises ``RunCancelled`` for a cancelled run; the run then ends with status
"cancelled". Work already applied by finished steps is not rolled back (a
deploy is staged, so a live view is never left half-written).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Dict, Iterator, Optional

_COLLECTION = "run_cancel"
_current_run: ContextVar[Optional[str]] = ContextVar("sb_current_run", default=None)
_local: set = set()
_lock = threading.Lock()
_checked: Dict[str, float] = {}
_CHECK_INTERVAL_S = 2.0


class RunCancelled(RuntimeError):
    """The run was cancelled by a user; raised at the next step boundary."""


@contextmanager
def scope(run_id: Optional[str]) -> Iterator[None]:
    token = _current_run.set(str(run_id) if run_id else None)
    try:
        yield
    finally:
        _current_run.reset(token)


def current_run_id() -> Optional[str]:
    return _current_run.get()


def request_cancel(run_id: str, *, by: Optional[str] = None) -> bool:
    """Record a cancel request. Returns False when it could not be stored."""
    rid = str(run_id or "").strip()
    if not rid:
        return False
    with _lock:
        _local.add(rid)
    from semabridge.repository import state_store

    stored = state_store.apply_changes([(_COLLECTION, rid, {
        "at": datetime.now(timezone.utc).isoformat(), "by": by,
    })]) is not None
    try:
        from semabridge.core import job_queue

        job_queue.request_cancel(rid)
    except Exception:  # noqa: BLE001 -- no job row in inline mode
        pass
    return stored


def is_cancel_requested(run_id: Optional[str]) -> bool:
    rid = str(run_id or "").strip()
    if not rid:
        return False
    with _lock:
        if rid in _local:
            return True
        last = _checked.get(rid, 0.0)
        now = time.monotonic()
        if now - last < _CHECK_INTERVAL_S:
            return False
        _checked[rid] = now
    from semabridge.repository import state_store

    if state_store.load_keys(_COLLECTION, [rid]).get(rid):
        with _lock:
            _local.add(rid)
        return True
    return False


def check() -> None:
    """Raise RunCancelled when the run executing in this context was cancelled."""
    rid = _current_run.get()
    if rid and is_cancel_requested(rid):
        raise RunCancelled("Cancelled by a user; remaining steps were not run.")


def forget(run_id: str) -> None:
    with _lock:
        _local.discard(str(run_id))
        _checked.pop(str(run_id), None)
