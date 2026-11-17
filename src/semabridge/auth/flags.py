"""Single source of truth for auth-related environment flags.

Every module that needs to know whether authentication is enforced must call
:func:`auth_enabled` instead of parsing ``AUTH_ENABLED`` itself. Historically
the middleware treated a missing variable as *enabled* while several services
treated it as *disabled*, so an unset variable produced a half-secured app.

Parsing fails **closed**: auth is enabled unless ``AUTH_ENABLED`` is explicitly
set to a false-like value (``false``, ``0``, ``no``, ``off``).
"""

from __future__ import annotations

import os

_FALSE_VALUES = frozenset({"false", "0", "no", "off"})
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})


def auth_enabled() -> bool:
    """Return True unless ``AUTH_ENABLED`` is explicitly false-like."""
    raw = os.environ.get("AUTH_ENABLED")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in _FALSE_VALUES


def dev_auto_login_allowed() -> bool:
    """Whether ``POST /auth/auto-login`` may issue tokens.

    Always allowed when auth is disabled (single-user dev mode). When auth is
    enabled it is only available if ``SEMABRIDGE_DEV_AUTO_LOGIN`` is
    explicitly true -- and even then it never grants the admin role.
    """
    if not auth_enabled():
        return True
    raw = os.environ.get("SEMABRIDGE_DEV_AUTO_LOGIN", "")
    return raw.strip().lower() in _TRUE_VALUES
