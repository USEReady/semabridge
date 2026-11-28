"""BI Truth Verified Pair Injection Engine (Criterion 2).

Filters qualifying cases, converts them to verified question/SQL pairs, and
delegates to platform adapters (Snowflake Cortex Analyst or Databricks Genie)
behind explicit user action and permissions.
"""

from __future__ import annotations

import datetime
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def filter_qualifying_cases(
    cases: List[Dict[str, Any]],
    latest_run: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Filter cases that qualify for injection into downstream AI semantic models.

    A case qualifies ONLY if:
    1. Status is 'approved' or 'certified'
    2. Expected benchmark value was captured (not None)
    3. The most recent run executed this case and recorded a PASS
    """
    qualifying: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    # Map case_id -> all result dicts from the latest run (supports multi-window cases)
    results_by_cid: Dict[str, List[Dict[str, Any]]] = {}
    if latest_run and isinstance(latest_run, dict):
        for r in latest_run.get("results") or []:
            cid = r.get("case_id")
            if cid:
                results_by_cid.setdefault(cid, []).append(r)

    from semabridge.ai.bi_truth_windows import parse_case_windows

    for c in cases:
        cid = c.get("id") or "unknown"
        status = str(c.get("status") or "draft").lower().strip()
        expected_val = c.get("expected_value")
        windows = parse_case_windows(c.get("windows"))
        has_window_benchmark = any(w.has_expected_value for w in windows)

        # Check 1: Status
        if status not in ("approved", "certified"):
            skipped.append({
                "case_id": cid,
                "question": c.get("question") or "",
                "status": status,
                "reason": f"Case status is '{status}' (must be 'approved' or 'certified')",
            })
            continue

        # Check 2: Captured expected value (case-level or at least one window benchmark)
        if expected_val is None and not has_window_benchmark:
            skipped.append({
                "case_id": cid,
                "question": c.get("question") or "",
                "status": status,
                "reason": "Expected benchmark value is not captured (null)",
            })
            continue

        # Check 3: Latest run result
        if not latest_run or not results_by_cid:
            skipped.append({
                "case_id": cid,
                "question": c.get("question") or "",
                "status": status,
                "reason": "No test runs recorded for project; suite must pass before injection",
            })
            continue

        cid_results = results_by_cid.get(cid) or []
        if not cid_results:
            skipped.append({
                "case_id": cid,
                "question": c.get("question") or "",
                "status": status,
                "reason": "Case was not evaluated in the latest run",
            })
            continue

        passing_res = [
            r for r in cid_results
            if bool(r.get("passed")) and str(r.get("verdict") or "PASS").upper() == "PASS"
        ]
        if not passing_res:
            last_verdict = str(cid_results[-1].get("verdict") or "FAIL").upper()
            skipped.append({
                "case_id": cid,
                "question": c.get("question") or "",
                "status": status,
                "reason": f"Case did not pass in latest run (verdict: {last_verdict})",
            })
            continue

        qualifying.append(c)

    return qualifying, skipped


def build_injection_pairs(
    cases: List[Dict[str, Any]],
    user: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Transform qualifying truth cases into clean verified question/SQL pairs."""
    pairs: List[Dict[str, Any]] = []
    now_epoch = int(time.time())
    steward = str(user or "steward").strip()

    for i, c in enumerate(cases):
        raw_name = c.get("metric_name") or c.get("id") or f"query_{i+1}"
        clean_name = re.sub(r"[^A-Za-z0-9_]", "_", str(raw_name)).strip("_") or f"query_{i+1}"
        question = str(c.get("question") or "").strip()
        sql = str(c.get("expected_sql") or "").strip()

        pairs.append({
            "case_id": c.get("id"),
            "name": clean_name,
            "question": question,
            "sql": sql,
            "verified_at": now_epoch,
            "verified_by": steward,
            "expected_value": c.get("expected_value"),
        })

    return pairs


def perform_injection(
    adapter: Any,
    project_id: str,
    view_name: str,
    connection_config: Optional[Dict[str, Any]],
    cases: List[Dict[str, Any]],
    latest_run: Optional[Dict[str, Any]],
    case_ids: Optional[List[str]] = None,
    dry_run: bool = False,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute end-to-end injection workflow with audit trail and per-case reporting."""
    # Filter by requested case_ids if specified
    if case_ids is not None:
        target_ids = set(case_ids)
        cases_to_eval = [c for c in cases if c.get("id") in target_ids]
    else:
        cases_to_eval = list(cases)

    qualifying, skipped = filter_qualifying_cases(cases_to_eval, latest_run)
    pairs = build_injection_pairs(qualifying, user=user)

    if not qualifying:
        return {
            "status": "skipped",
            "project_id": project_id,
            "view_name": view_name,
            "dry_run": dry_run,
            "target": None,
            "injected_count": 0,
            "injected": [],
            "skipped": skipped,
            "failed": [],
            "audit_record": {
                "project_id": project_id,
                "injected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "injected_by": user or "system",
                "dry_run": dry_run,
                "count": 0,
            },
            "message": "No cases qualified for injection.",
        }

    # Delegate to platform adapter
    adapter_result = adapter.inject_verified_queries(
        pairs=pairs,
        view_name=view_name,
        connection_config=connection_config,
        dry_run=dry_run,
    )

    supported = adapter_result.get("supported", False)
    injected_count = adapter_result.get("injected_count", 0)
    target = adapter_result.get("target")
    adapter_reason = adapter_result.get("reason") or ""

    if not supported:
        # Platform refused
        return {
            "status": "refused",
            "project_id": project_id,
            "view_name": view_name,
            "dry_run": dry_run,
            "target": target,
            "injected_count": 0,
            "injected": [],
            "skipped": skipped,
            "failed": [
                {
                    "case_id": c.get("id"),
                    "question": c.get("question"),
                    "reason": adapter_reason,
                }
                for c in qualifying
            ],
            "message": adapter_reason,
        }

    # If injection succeeded or dry run
    injected_list = [
        {
            "case_id": p.get("case_id"),
            "question": p.get("question"),
            "sql": p.get("sql"),
            "target": target,
        }
        for p in pairs[:injected_count]
    ]

    failed_list = [
        {
            "case_id": p.get("case_id"),
            "question": p.get("question"),
            "reason": adapter_reason if injected_count == 0 else "Partial write: not included in target model",
        }
        for p in pairs[injected_count:]
    ]

    status_str = "success"
    if dry_run:
        status_str = "dry_run"
    elif failed_list and injected_list:
        status_str = "partial"
    elif not injected_list and failed_list:
        status_str = "failed"

    audit_entry = {
        "project_id": project_id,
        "view_name": view_name,
        "target": target,
        "injected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "injected_by": user or "system",
        "dry_run": dry_run,
        "injected_count": len(injected_list),
        "skipped_count": len(skipped),
        "failed_count": len(failed_list),
    }

    return {
        "status": status_str,
        "project_id": project_id,
        "view_name": view_name,
        "dry_run": dry_run,
        "target": target,
        "injected_count": len(injected_list),
        "injected": injected_list,
        "skipped": skipped,
        "failed": failed_list,
        "message": adapter_reason or ("Dry run completed." if dry_run else f"Successfully injected {len(injected_list)} pairs."),
        "audit_record": audit_entry,
    }
