"""Shared pure-logic helpers for CLI and API run execution.

Functions in this module have no I/O, no DB calls, and no framework
dependencies.  Both ``semabridge.cli.main`` and
``semabridge.api.services.project_runs_impl`` previously duplicated these
utilities inline.  Centralising them here ensures consistent behaviour and
makes unit-testing trivial.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


def elapsed_ms(start: float) -> int:
    """Return the number of milliseconds elapsed since *start*.

    Args:
        start: A timestamp obtained from ``time.time()`` at the beginning of
               an operation.

    Returns:
        Elapsed wall-clock time in milliseconds, truncated to an integer.

    Example::

        start = time.time()
        # … do work …
        duration = elapsed_ms(start)   # e.g. 1234
    """
    return int((time.time() - start) * 1000)


def normalize_sync_mode(value: Any) -> Optional[str]:
    """Normalise and validate a sync-mode value.

    Args:
        value: Raw sync-mode string from user input or stored state.  May be
               ``None``, empty, or any casing of ``"copy"`` / ``"upsert"``.

    Returns:
        ``"copy"`` or ``"upsert"`` if *value* is valid; ``None`` otherwise.

    Example::

        normalize_sync_mode("COPY")    # "copy"
        normalize_sync_mode("merge")   # None
        normalize_sync_mode(None)      # None
    """
    mode = str(value or "").strip().lower()
    return mode if mode in {"copy", "upsert"} else None


def resolve_run_status(sync_result: Dict[str, Any]) -> str:
    """Map a sync-result payload to a canonical run-status string.

    The sync engine reports ``"success"``, ``"partial"``, or anything else
    (treated as failure).  This helper converts that into the three-valued
    status used by the run record and the CLI summary: ``"success"``,
    ``"warning"``, or ``"failed"``.

    Args:
        sync_result: The dict returned by ``sync_models`` or an equivalent
                     execution function.  Only ``sync_result["status"]`` is
                     examined.

    Returns:
        One of ``"success"``, ``"warning"``, or ``"failed"``.

    Example::

        resolve_run_status({"status": "success"})  # "success"
        resolve_run_status({"status": "partial"})  # "warning"
        resolve_run_status({"status": "error"})    # "failed"
        resolve_run_status({})                      # "failed"
    """
    overall = str((sync_result or {}).get("status") or "").lower()
    if overall == "success":
        return "success"
    if overall == "partial":
        return "warning"
    return "failed"
