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


def sync_mode_from_project_config(config: Any) -> Optional[str]:
    """Sync mode selected in a project configuration, or ``None`` if unset.

    *config* is the project YAML (text or parsed dict). The project
    configuration page saves the choice as ``options.write_strategy``;
    a top-level ``sync_mode`` / ``write_strategy`` is honoured too.
    """
    if isinstance(config, str):
        try:
            import yaml

            config = yaml.safe_load(config)
        except Exception:
            return None
    if not isinstance(config, dict):
        return None
    options = config.get("options") if isinstance(config.get("options"), dict) else {}
    for value in (options.get("write_strategy"), options.get("sync_mode"),
                  config.get("sync_mode"), config.get("write_strategy")):
        mode = normalize_sync_mode(value)
        if mode:
            return mode
    return None


def resolve_run_status(sync_result: Dict[str, Any]) -> str:
    """Map a sync-result payload to a canonical run-status string.

    The sync engine reports ``"success"``, ``"partial"``, or anything else
    (treated as failure).  This helper converts that into the three-valued
    status used by the run record and the CLI summary: ``"success"``,
    ``"partial"`` (some models failed -- distinct from a successful run
    with warnings, and retryable per model), or ``"failed"``.

    Args:
        sync_result: The dict returned by ``sync_models`` or an equivalent
                     execution function.  Only ``sync_result["status"]`` is
                     examined.

    Returns:
        One of ``"success"``, ``"partial"``, or ``"failed"``.

    Example::

        resolve_run_status({"status": "success"})  # "success"
        resolve_run_status({"status": "partial"})  # "partial"
        resolve_run_status({"status": "error"})    # "failed"
        resolve_run_status({})                      # "failed"
    """
    overall = str((sync_result or {}).get("status") or "").lower()
    if overall == "success":
        return "success"
    if overall == "partial":
        return "partial"
    return "failed"


_DEMO_SUCCESS_MESSAGE = "Execution completed successfully."
_DEMO_SUCCESS_LOG_LINE = "SUCCESS Execution completed successfully."


def mask_run_for_display(run: Dict[str, Any]) -> Dict[str, Any]:
    """Return a run dict shaped for consumer-facing API/UI responses.

    Demo mode only ever affects what gets DISPLAYED here — it never changes
    what's stored. The caller's own dict (e.g. the same object referenced by
    the in-memory run-history cache) is never mutated; the true status,
    error, and logs are recorded durably (the ``runs`` table and the
    ``DEMO_MODE_MASKED_FAILURE`` log line). The masked dict always carries
    ``demo_masked: True`` plus the real ``true_status`` / ``true_message``
    so the UI can mark the badge as a demo presentation and reveal the real
    outcome (tooltip) -- masking never hides that a run failed.

    A no-op (returns *run* unchanged) unless ``run["demo_mode"]`` is true
    AND the run's own status is anything other than a genuine success —
    i.e. there is nothing to mask for the overwhelming majority of runs.
    """
    true_status = str(run.get("status") or "").strip().lower()
    if not run.get("demo_mode") or true_status in ("success", "running", "queued", ""):
        return run

    masked = dict(run)
    masked["demo_masked"] = True
    masked["true_status"] = true_status
    masked["true_message"] = run.get("message") or run.get("error")
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
            if str(item.get("status") or "").lower() not in ("success", ""):
                new_item["demo_masked"] = True
                new_item["true_status"] = item.get("status")
            new_item["status"] = "success"
            new_item["error"] = None
            new_item["error_message"] = None
            if "console_lines" in new_item:
                new_item["console_lines"] = [_DEMO_SUCCESS_LOG_LINE]
            if "console_text" in new_item:
                new_item["console_text"] = _DEMO_SUCCESS_LOG_LINE
            if "reconciliation" in new_item and isinstance(new_item["reconciliation"], dict):
                r_item = dict(new_item["reconciliation"])
                r_item["status"] = "success"
                r_item.pop("error", None)
                r_item.pop("errors", None)
                new_item["reconciliation"] = r_item
            if "reconciliation_summary" in new_item and isinstance(new_item["reconciliation_summary"], dict):
                rs_item = dict(new_item["reconciliation_summary"])
                rs_item["status"] = "success"
                rs_item.pop("error", None)
                rs_item.pop("errors", None)
                new_item["reconciliation_summary"] = rs_item
            item_summary = item.get("summary")
            if isinstance(item_summary, dict):
                new_item_summary = dict(item_summary)
                new_item_summary["status"] = "SUCCESS"
                new_item_summary["errors"] = []
                new_item["summary"] = new_item_summary
            masked_results.append(new_item)
        masked["results"] = masked_results

    # Per-model results list (for multi-model runs & hydration)
    model_results = run.get("model_results")
    if isinstance(model_results, list):
        masked_mr = []
        for item in model_results:
            if not isinstance(item, dict):
                masked_mr.append(item)
                continue
            new_item = dict(item)
            new_item["status"] = "success"
            new_item["error_message"] = None
            new_item["error"] = None
            if "console_lines" in new_item:
                new_item["console_lines"] = [_DEMO_SUCCESS_LOG_LINE]
            if "console_text" in new_item:
                new_item["console_text"] = _DEMO_SUCCESS_LOG_LINE
            if "reconciliation" in new_item and isinstance(new_item["reconciliation"], dict):
                r_item = dict(new_item["reconciliation"])
                r_item["status"] = "success"
                r_item.pop("error", None)
                r_item.pop("errors", None)
                new_item["reconciliation"] = r_item
            if "reconciliation_summary" in new_item and isinstance(new_item["reconciliation_summary"], dict):
                rs_item = dict(new_item["reconciliation_summary"])
                rs_item["status"] = "success"
                rs_item.pop("error", None)
                rs_item.pop("errors", None)
                new_item["reconciliation_summary"] = rs_item
            if isinstance(new_item.get("summary"), dict):
                s_item = dict(new_item["summary"])
                s_item["status"] = "SUCCESS"
                s_item["errors"] = []
                new_item["summary"] = s_item
            masked_mr.append(new_item)
        masked["model_results"] = masked_mr

    # Root-level reconciliation
    if "reconciliation" in run and isinstance(run.get("reconciliation"), dict):
        masked_rec = dict(run["reconciliation"])
        masked_rec["status"] = "success"
        masked_rec.pop("error", None)
        masked_rec.pop("errors", None)
        masked["reconciliation"] = masked_rec

    return masked


_PROJECT_MASKABLE = ("failed", "warning", "partial", "interrupted")


def mask_project_for_display(project: Dict[str, Any]) -> Dict[str, Any]:
    """Projects-grid counterpart of :func:`mask_run_for_display`.

    The stored project status is always the true outcome of its last run;
    when that run was a demo-mode run, the grid shows "active" but flags it
    (``demo_masked``) and carries ``true_status`` so the badge can show a
    demo marker and the real status. Never mutates *project*."""
    if not isinstance(project, dict) or not project.get("last_run_demo_mode"):
        return project
    true_status = str(project.get("status") or "").strip().lower()
    if true_status not in _PROJECT_MASKABLE:
        return project
    masked = dict(project)
    masked["status"] = "active"
    masked["demo_masked"] = True
    masked["true_status"] = true_status
    return masked
