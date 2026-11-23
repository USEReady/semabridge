"""Deploy-side backends per target platform (Phase 3).

    from semabridge.target_backends import get_backend
    backend = get_backend("databricks")

base.py        the TargetBackend outline (one method per Phase 3 step)
registry.py    platforms register a backend factory; looked up by name
databricks.py  the Databricks (metric view) backend
"""

from __future__ import annotations

from semabridge.target_backends.base import TargetBackend, TargetBackendNotReady
from semabridge.target_backends.registry import (
    UnsupportedBackendError,
    get_backend,
    register_backend,
    registered_backends,
)

__all__ = [
    "TargetBackend",
    "TargetBackendNotReady",
    "UnsupportedBackendError",
    "get_backend",
    "register_backend",
    "registered_backends",
]
