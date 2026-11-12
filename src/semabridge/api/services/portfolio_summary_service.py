"""Migration summary across projects, for the Projects page summary band.

Adds up the report summary of each project's LATEST finished run (the same
data the run report view shows: tables, columns, measures, relationships and
how each measure converted) and run health over a recent window.

A finished run's report never changes, so it is cached per (project, run).
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_FINISHED = {"success", "failed", "partial", "completed", "error", "cancelled"}
_OK = {"success", "completed", "partial"}
_report_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _run_id(run: Dict[str, Any]) -> str:
    return str(run.get("run_id") or run.get("id") or "")


def _is_preview(run: Dict[str, Any]) -> bool:
    return str(run.get("run_type") or "").upper() == "DRY_RUN"


async def _report_for(project_id: str, run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from semabridge.api.services.run_service import get_run_report_data_compat

    rid = _run_id(run)
    if not rid:
        return None
    key = f"{project_id}:{rid}"
    with _cache_lock:
        if key in _report_cache:
            return _report_cache[key]
    try:
        data = await get_run_report_data_compat(project_id, rid)
    except Exception as exc:
        logger.warning("Portfolio summary: report for %s/%s unavailable: %s", project_id, rid, exc)
        data = None
    slim = None
    if isinstance(data, dict) and not data.get("crashed"):
        confidences = [
            float(item["confidence"])
            for item in ((data.get("sections") or {}).get("ai_assisted") or [])
            if isinstance(item, dict) and isinstance(item.get("confidence"), (int, float))
        ]
        slim = {
            "counts": dict(data.get("counts") or {}),
            "models": len(data.get("source_files") or []) or 1,
            "ai_confidence_sum": sum(confidences),
            "ai_confidence_n": len(confidences),
        }
    if slim is not None:
        with _cache_lock:
            _report_cache[key] = slim
    return slim


def _conversion_pct(counts: Dict[str, Any]) -> Optional[float]:
    converted = int(counts.get("standard") or 0) + int(counts.get("ai_assisted") or 0)
    total = converted + int(counts.get("needs_review") or 0) + int(counts.get("dropped") or 0)
    return round(100.0 * converted / total, 1) if total else None


async def build_portfolio_summary(projects: List[Dict[str, Any]], days: int = 30) -> Dict[str, Any]:
    from semabridge.api.services.run_service import get_project_runs_compat

    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    keys = ("tables", "columns", "calculations", "relationships", "standard", "ai_assisted", "needs_review", "dropped", "excluded_by_design")
    totals: Dict[str, int] = {k: 0 for k in keys}
    totals["models"] = 0
    with_data = 0
    conf_sum, conf_n = 0.0, 0
    window_runs = window_ok = window_failed = 0
    daily: Dict[str, List[int]] = {}
    per_project: Dict[str, Any] = {}
    attention: List[Dict[str, Any]] = []
    routes: Dict[str, int] = {}
    status_counts = {"active": 0, "draft": 0, "failing": 0}

    for project in projects:
        pid = str(project.get("id") or project.get("project_id") or "").strip()
        if not pid:
            continue
        name = project.get("name") or pid
        src = str(project.get("source") or project.get("source_type") or "").strip()
        tgt = str(project.get("target_type") or "").strip()
        if src or tgt:
            route = f"{src or '?'} → {tgt or '?'}"
            routes[route] = routes.get(route, 0) + 1

        try:
            runs = [r for r in (await get_project_runs_compat(pid)) or [] if isinstance(r, dict) and not _is_preview(r)]
        except Exception as exc:
            logger.warning("Portfolio summary: runs for %s unavailable: %s", pid, exc)
            runs = []

        for r in runs:
            started = _parse_ts(r.get("started_at"))
            status = str(r.get("status") or "").lower()
            if started and started >= since and status in _FINISHED:
                window_runs += 1
                day = daily.setdefault(started.date().isoformat(), [0, 0])
                day[0] += 1
                if status in _OK:
                    window_ok += 1
                    day[1] += 1
                else:
                    window_failed += 1

        finished = [r for r in runs if str(r.get("status") or "").lower() in _FINISHED]
        latest = finished[0] if finished else None
        latest_ok = next((r for r in finished if str(r.get("status") or "").lower() in _OK), None)
        report = await _report_for(pid, latest_ok) if latest_ok else None

        row: Dict[str, Any] = {
            "project_id": pid,
            "last_run_at": (latest.get("completed_at") or latest.get("started_at")) if latest else None,
            "last_run_status": str(latest.get("status") or "").lower() if latest else None,
            "last_run_id": _run_id(latest) if latest else None,
            "last_run_message": (latest.get("message") if latest else None),
            "counts": None,
            "conversion_pct": None,
            "models": None,
        }
        if report:
            with_data += 1
            counts = report["counts"]
            for k in keys:
                totals[k] += int(counts.get(k) or 0)
            totals["models"] += int(report["models"] or 0)
            conf_sum += report["ai_confidence_sum"]
            conf_n += report["ai_confidence_n"]
            row["counts"] = {k: int(counts.get(k) or 0) for k in keys}
            row["conversion_pct"] = _conversion_pct(counts)
            row["models"] = report["models"]
        per_project[pid] = row

        if latest is None:
            status_counts["draft"] += 1
            created = _parse_ts(project.get("created_at"))
            if created and (now - created) > timedelta(days=7):
                attention.append({
                    "project_id": pid, "project_name": name, "kind": "draft", "severity": "warning",
                    "message": f"Draft for {(now - created).days} days, never run",
                })
        elif row["last_run_status"] not in _OK:
            status_counts["failing"] += 1
            attention.append({
                "project_id": pid, "project_name": name, "kind": "failed", "severity": "error",
                "run_id": row["last_run_id"],
                "message": f"Last run failed: {row['last_run_message'] or 'see run details'}",
            })
        else:
            status_counts["active"] += 1
        if row["counts"] and row["counts"]["needs_review"]:
            attention.append({
                "project_id": pid, "project_name": name, "kind": "review", "severity": "warning",
                "count": row["counts"]["needs_review"],
                "message": f"{row['counts']['needs_review']} measures need review",
            })

    attention.sort(key=lambda a: (0 if a["severity"] == "error" else 1, -(a.get("count") or 0)))
    trend = [
        {"date": (since + timedelta(days=i + 1)).date().isoformat(),
         "runs": daily.get((since + timedelta(days=i + 1)).date().isoformat(), [0, 0])[0],
         "succeeded": daily.get((since + timedelta(days=i + 1)).date().isoformat(), [0, 0])[1]}
        for i in range(days)
    ]
    converted = totals["standard"] + totals["ai_assisted"]
    measured = converted + totals["needs_review"] + totals["dropped"]
    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "projects": {"total": len(per_project), **status_counts},
        "migrated": {
            **totals,
            "measures_total": measured,
            "measures_converted": converted,
            # Projects whose latest successful run still has report data (a run
            # from before a server restart may not), so the UI can say so.
            "projects_with_data": with_data,
            "projects_with_runs": sum(1 for r in per_project.values() if r["last_run_status"]),
            "conversion_pct": round(100.0 * converted / measured, 1) if measured else None,
        },
        "ai": {
            "measures_ai_assisted": totals["ai_assisted"],
            "avg_confidence": round(conf_sum / conf_n, 2) if conf_n else None,
            # Ask AI questions are not recorded yet.
            "ask_ai": None,
        },
        "runs": {
            "total": window_runs,
            "succeeded": window_ok,
            "failed": window_failed,
            "success_pct": round(100.0 * window_ok / window_runs, 1) if window_runs else None,
            "trend": trend,
        },
        "routes": sorted(({"route": k, "projects": v} for k, v in routes.items()), key=lambda r: -r["projects"]),
        "attention": attention[:8],
        "per_project": per_project,
    }
