"""Per-model run logs, tagged at the source.

Every log record emitted while a model's sync job is running is tagged with
the project run id and the model (a context variable set by the job itself,
see :func:`model_log_scope`) and appended to that model's OWN log file.

Isolation comes from the context variable, not from parsing text: each job
of a parallel run executes in its own copied context (one thread per job), so
a line can only ever be written to the file of the job that produced it --
concurrent models' lines never mix. Records emitted outside any model scope
are ignored by this handler (they still reach every other handler).
"""

from __future__ import annotations

import contextvars
import logging
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

RUN_LOGS_ROOT = Path("output") / "runs"

_scope: contextvars.ContextVar[Optional[Tuple[str, str, str]]] = contextvars.ContextVar(
    "semabridge_run_model_log_scope", default=None
)
_file_locks: Dict[str, threading.Lock] = {}
_file_locks_guard = threading.Lock()
_install_lock = threading.Lock()


def _safe_part(value: str, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
    return text[:120] or fallback


def model_log_path(project_id: str, run_id: str, model: str) -> Path:
    """Where a model's log for one run lives (stable, restart-safe)."""
    return RUN_LOGS_ROOT / _safe_part(project_id, "project") / _safe_part(run_id, "run") / f"{_safe_part(model, 'model')}.log"


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _file_locks_guard:
        lock = _file_locks.get(key)
        if lock is None:
            lock = _file_locks[key] = threading.Lock()
        return lock


def append_model_log_line(project_id: str, run_id: str, model: str, line: str) -> None:
    path = model_log_path(project_id, run_id, model)
    with _lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")


class PerModelRunLogHandler(logging.Handler):
    """Writes records emitted inside a model scope to that model's log file,
    each line tagged with its run id and model."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        scope = _scope.get()
        if scope is None:
            return
        project_id, run_id, model = scope
        try:
            text = self.format(record)
            append_model_log_line(project_id, run_id, model, f"[run={run_id}] [model={model}] {text}")
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)


def install_per_model_log_handler() -> None:
    """Attach the handler to the root logger once (idempotent)."""
    root = logging.getLogger()
    with _install_lock:
        if any(isinstance(h, PerModelRunLogHandler) for h in root.handlers):
            return
        root.addHandler(PerModelRunLogHandler())


@contextmanager
def model_log_scope(project_id: Optional[str], run_id: Optional[str], model: str) -> Iterator[Optional[Path]]:
    """Tag every log record of this context with (run, model). No-op without
    a run id (e.g. dry runs and direct engine calls)."""
    if not run_id:
        yield None
        return
    install_per_model_log_handler()
    token = _scope.set((str(project_id or ""), str(run_id), str(model)))
    try:
        yield model_log_path(str(project_id or ""), str(run_id), str(model))
    finally:
        _scope.reset(token)


def current_scope() -> Optional[Tuple[str, str, str]]:
    return _scope.get()


def read_model_log(project_id: str, run_id: str, model: str, offset: int = 0, limit_bytes: int = 512_000) -> Dict[str, object]:
    """Read a model's log from a byte offset (for incremental polling)."""
    path = model_log_path(project_id, run_id, model)
    if not path.exists():
        return {"lines": [], "next_offset": int(offset or 0), "exists": False}
    size = path.stat().st_size
    start = max(0, min(int(offset or 0), size))
    with open(path, "rb") as handle:
        handle.seek(start)
        chunk = handle.read(limit_bytes)
    # Only return complete lines; a partially written last line is picked up
    # on the next poll.
    end = chunk.rfind(b"\n") + 1
    text = chunk[:end].decode("utf-8", errors="replace")
    return {
        "lines": [line for line in text.split("\n") if line],
        "next_offset": start + end,
        "exists": True,
    }
