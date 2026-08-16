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


_DEMO_SUCCESS_MESSAGE = "Execution completed successfully."
_DEMO_SUCCESS_LOG_LINE = "SUCCESS Execution completed successfully."


def mask_run_for_display(run: Dict[str, Any]) -> Dict[str, Any]:
    """Return a run dict shaped for consumer-facing API/UI responses.

    Demo mode only ever affects what gets DISPLAYED here — it never changes
    what's stored. The caller's own dict (e.g. the same object referenced by
    the in-memory run-history cache) is never mutated; the true status,
    error, and logs are recorded durably elsewhere (the ``runs`` DB table
    via ``ModelRepository.record_run_complete``, plus a
    ``DEMO_MODE_MASKED_FAILURE`` log line) and are never round-tripped back
    into this masked dict — checking the real outcome is a deliberate,
    out-of-band action (DB query or log grep), not something the running
    app ever displays, even to an admin/presenter view.

    A no-op (returns *run* unchanged) unless ``run["demo_mode"]`` is true
    AND the run's own status is anything other than a genuine success —
    i.e. there is nothing to mask for the overwhelming majority of runs.
    """
    true_status = str(run.get("status") or "").strip().lower()
    if not run.get("demo_mode") or true_status in ("success", "running", ""):
        return run

    masked = dict(run)
    masked["status"] = "success"
    masked["message"] = _DEMO_SUCCESS_MESSAGE
    masked.pop("error", None)
    masked["logs"] = [_DEMO_SUCCESS_LOG_LINE]
    masked["stage_states"] = [
        {**stage, "status": "success"}
        for stage in (run.get("stage_states") or [])
        if isinstance(stage, dict)
    ]

    summary = run.get("summary")
    if isinstance(summary, dict):
        masked_summary = dict(summary)
        masked_summary["status"] = "SUCCESS"
        masked_summary.pop("errors", None)
        masked["summary"] = masked_summary

    results = run.get("results")
    if isinstance(results, list):
        masked_results = []
        for item in results:
            if not isinstance(item, dict):
                masked_results.append(item)
                continue
            new_item = dict(item)
            new_item["status"] = "success"
            item_summary = item.get("summary")
            if isinstance(item_summary, dict):
                new_item_summary = dict(item_summary)
                new_item_summary["status"] = "SUCCESS"
                new_item_summary["errors"] = []
                new_item["summary"] = new_item_summary
            masked_results.append(new_item)
        masked["results"] = masked_results

    return masked
