"""Startup steps that failed, for the readiness probe and strict startup.

Locally a failed schema step is logged and the server still starts, so a
developer can work against a half-set-up database. On a hosted profile
(``is_production()``) or with ``SEMABRIDGE_STRICT_STARTUP=true`` the server
refuses to start instead of serving traffic on a stale or partial schema;
``SEMABRIDGE_STRICT_STARTUP=false`` opts a hosted deployment out.
"""
from __future__ import annotations

import os
import threading
from typing import Dict, List

_lock = threading.Lock()
_failures: List[Dict[str, str]] = []


class StartupFailedError(RuntimeError):
    """A required startup step failed on a deployment that must not start degraded."""


def record_failure(step: str, exc: BaseException) -> None:
    with _lock:
        _failures.append({"step": step, "error": f"{type(exc).__name__}: {exc}"[:500]})


def failures() -> List[Dict[str, str]]:
    with _lock:
        return [dict(f) for f in _failures]


def reset() -> None:
    with _lock:
        _failures.clear()


def strict_startup() -> bool:
    raw = str(os.environ.get("SEMABRIDGE_STRICT_STARTUP", "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    from semabridge.core.deployment_profile import is_production

    return is_production()


def raise_if_startup_failed() -> None:
    """Abort startup when strict and any required step failed."""
    current = failures()
    if current and strict_startup():
        steps = "; ".join(f"{f['step']}: {f['error']}" for f in current)
        raise StartupFailedError(f"SemaBridge did not start because required setup failed: {steps}")
