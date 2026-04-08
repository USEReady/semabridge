"""Shared in-memory state for interactive connection auth flows."""

from typing import Any, Dict
import threading as _threading
import time as _time
import uuid as _uuid

from starlette.requests import Request

_poll_sessions: Dict[str, Dict[str, Any]] = {}
_poll_sessions_lock = _threading.Lock()
_last_poll_time: Dict[str, float] = {}
_POLL_SESSION_TTL = 900


def _get_client_ip(request: Request) -> str:
    """Extract client IP, preferring X-Forwarded-For to handle reverse proxies."""
    x_forward = request.headers.get("X-Forwarded-For")
    if x_forward:
        return x_forward.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _cleanup_stale_poll_sessions() -> None:
    """Remove poll sessions older than their explicit expires_at timestamp."""
    now = _time.time()
    with _poll_sessions_lock:
        stale = [fid for fid, session in _poll_sessions.items() if session.get("expires_at", 0) < now]
        for flow_id in stale:
            del _poll_sessions[flow_id]
            _last_poll_time.pop(flow_id, None)
