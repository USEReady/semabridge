"""Backward-compatible shim — all classes have moved to semabridge.repository.orm.*

Existing imports like ``from semabridge.repository.orm.models import User``
continue to work unchanged.
"""

from semabridge.repository.orm import *  # noqa: F401, F403
