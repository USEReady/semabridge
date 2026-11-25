"""After a sync: do the certified measures still match Power BI?

For each certified measure of the project (latest certification, up to
SEMABRIDGE_VERIFY_MAX_METRICS, default 10) the stored Power BI (DAX) query
and the stored warehouse query are run again and compared. Results go on the
run; a mismatch becomes a run warning. Never fails the sync. Disabled with
SEMABRIDGE_VERIFY_AFTER_SYNC=false.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from semabridge.reconciliation import project_verify as pv

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return str(os.environ.get("SEMABRIDGE_VERIFY_AFTER_SYNC", "true")).strip().lower() not in {"0", "false", "no", "off"}


def _max_metrics() -> int:
    try:
        return max(1, int(os.environ.get("SEMABRIDGE_VERIFY_MAX_METRICS", "10")))
    except ValueError:
        return 10


def _positional_rows(rows: List[Dict[str, Any]], names: List[str]) -> List[Dict[str, Any]]:
    """Certification queries select the dimensions in order, then the measure,
    on both sides; align warehouse rows to the Power BI column names by position."""
    out = []
    for row in rows:
        values = list(row.values())
        if len(values) != len(names):
            raise pv.VerificationUnavailable(
                f"the warehouse query returned {len(values)} column(s), expected {len(names)}"
            )
        out.append(dict(zip(names, values)))
    return out


def _run_sql(project_id: str, sql: str, target: str, owner_id: Optional[str]) -> List[Dict[str, Any]]:
    from semabridge.api.routers.ai_router import ExecuteQueryRequest, execute_semantic_query

    resp = execute_semantic_query(
        ExecuteQueryRequest(sql=sql, target=target, project_id=project_id, limit=100000, allow_simulated=False),
        owner_id=owner_id,
    )
    if not resp.success or resp.is_simulated:
        raise pv.VerificationUnavailable(f"the {target.title()} query could not run: {resp.error or resp.message}")
    return [dict(zip(resp.columns, r)) for r in resp.rows]


def verify_certified_measures(project_id: str, owner_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Summary dict, or None when the project has no certified measures."""
    from semabridge.ai.bi_truth_service import _detect_project_target
    from semabridge.reconciliation.certification_service import get_latest_certifications

    certs = [
        c for c in get_latest_certifications(project_id)
        if str(c.get("status") or "").upper() == "CERTIFIED" and c.get("dax_query") and c.get("sql_query")
    ][: _max_metrics()]
    if not certs:
        return None
    target = _detect_project_target(project_id)
    results: List[Dict[str, Any]] = []
    for cert in certs:
        name = str(cert.get("metric_name") or "")
        dims = [str(d) for d in (cert.get("dimensions") or [])]
        try:
            dax_rows = pv.run_project_dax(project_id, cert["dax_query"])
            sql_rows = _positional_rows(_run_sql(project_id, cert["sql_query"], target, owner_id), dims + [name])
            verdict = pv.compare(name, dax_rows, sql_rows, dax_query=cert["dax_query"],
                                 sql_query=cert["sql_query"], dimension_keys=dims)
        except pv.VerificationUnavailable as exc:
            verdict = pv.Verdict(pv.UNVERIFIED, str(exc))
        except Exception as exc:  # noqa: BLE001 -- one measure never stops the rest
            verdict = pv.Verdict(pv.UNVERIFIED, f"comparison could not run: {exc}")
        results.append({"metric_name": name, "verdict": verdict.verdict, "detail": verdict.detail})
    summary = pv.summarize(results)
    return {"target": target, **summary, "results": results}


def warning_for(summary: Optional[Dict[str, Any]]) -> Optional[str]:
    if not summary or not summary.get("mismatch"):
        return None
    names = [r["metric_name"] for r in summary["results"] if r["verdict"] == pv.MISMATCH]
    return (
        f"Verify: {summary['mismatch']} of {summary['compared']} certified measure(s) no longer match Power BI "
        f"after this sync: {', '.join(names[:5])}{' …' if len(names) > 5 else ''}."
    )
