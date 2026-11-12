"""Scheduled Match & Drift Detection Service with Approval Workflow (APScheduler)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.governance.drift_service")

_SCHEDULER = None


def _state_path() -> Path:
    p = Path.home() / ".semabridge" / "drift_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {"schedules": [], "events": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {
            "schedules": list(data.get("schedules") or []),
            "events": list(data.get("events") or []),
        }
    except Exception:
        return {"schedules": [], "events": []}


def _save_state(data: Dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _send_webhook(url: str, payload: Dict[str, Any]) -> None:
    if not url:
        return
    try:
        import requests

        requests.post(url, json=payload, timeout=5)
    except Exception as exc:
        logger.warning("Drift webhook notification failed: %s", exc)


def run_drift_check(
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    webhook_url: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare BI/ontology metric definitions against Snowflake & record pending drift events."""
    from semabridge.ai.project_connection import resolve_project_warehouse_config
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
    from semabridge.governance.blast_radius import BlastRadiusSimulator
    from semabridge.ontology.store import ontology_store

    fed = ontology_store.get_federated_ontology()
    target_domains = [
        d for d in fed.domains.values()
        if (not domain or d.domain == domain)
        and (not project_id or getattr(d, "source_project_id", None) == project_id)
    ]

    sf_metrics: Dict[str, Dict[str, str]] = {}
    sf_connected = False
    try:
        sf = resolve_project_warehouse_config(project_id, "SNOWFLAKE", owner_id=owner_id)
        if sf is not None:
            kw = get_snowflake_connect_kwargs(sf)
            if kw.get("account") and kw.get("user"):
                import snowflake.connector

                conn = snowflake.connector.connect(**kw)
                try:
                    cur = conn.cursor()
                    for dom in target_domains:
                        sv = getattr(dom, "semantic_view_name", None)
                        if not sv:
                            continue
                        try:
                            cur.execute(f"DESCRIBE SEMANTIC VIEW {sv}")
                            cols = [c[0].lower() for c in cur.description] if cur.description else []
                            for row in cur.fetchall():
                                rec = dict(zip(cols, row))
                                kind = str(rec.get("object_kind") or rec.get("kind") or "").upper()
                                name = str(rec.get("object_name") or rec.get("name") or "").strip()
                                prop = str(rec.get("property") or "").upper()
                                val = str(rec.get("property_value") or "").strip()
                                if kind in ("METRIC", "METRICS", "") and name:
                                    entry = sf_metrics.setdefault(name.upper(), {"name": name, "view": sv, "expression": ""})
                                    if prop == "EXPRESSION":
                                        entry["expression"] = val
                        except Exception:
                            pass
                    sf_connected = True
                finally:
                    conn.close()
    except Exception as exc:
        logger.info("Drift check Snowflake inspection skipped: %s", exc)

    sim = BlastRadiusSimulator()
    new_events: List[Dict[str, Any]] = []
    checked = 0
    now = datetime.now(timezone.utc).isoformat()

    for dom in target_domains:
        for m in dom.metrics:
            checked += 1
            m_key = m.name.upper()
            sf_entry = sf_metrics.get(m_key)
            bi_expr = (m.sql_expression or m.dax_expression or "").strip()
            drift_reason = None
            sf_expr = ""
            if sf_connected and sf_entry is None and getattr(dom, "semantic_view_name", None):
                drift_reason = "MISSING_IN_SNOWFLAKE"
            elif sf_entry is not None:
                sf_expr = (sf_entry.get("expression") or "").strip()
                if sf_expr and bi_expr and sf_expr.lower() != bi_expr.lower():
                    drift_reason = "EXPRESSION_MISMATCH"

            if drift_reason:
                impact = sim.simulate(
                    target_object=m.name,
                    object_type="METRIC",
                    change_type="MODIFY_EXPRESSION",
                    project_id=project_id,
                )
                affected = [
                    *(x.get("name") if isinstance(x, dict) else str(x) for x in impact.affected_views[:5]),
                    *(x.get("name") if isinstance(x, dict) else str(x) for x in impact.affected_metrics[:5]),
                ]
                ev = {
                    "id": f"drift-{uuid.uuid4().hex[:10]}",
                    "project_id": project_id or getattr(dom, "source_project_id", None),
                    "domain": dom.domain,
                    "metric_name": m.name,
                    "drift_type": drift_reason,
                    "bi_definition": bi_expr,
                    "snowflake_definition": sf_expr or "(not present in semantic view)",
                    "affected_objects": affected,
                    "status": "pending",
                    "resolution_reason": "",
                    "detected_at": now,
                }
                new_events.append(ev)

    state = _load_state()
    state["events"] = (new_events + state["events"])[:200]
    _save_state(state)

    if new_events and webhook_url:
        _send_webhook(
            webhook_url,
            {
                "text": f"SemaBridge Drift Alert: {len(new_events)} drift item(s) detected.",
                "project_id": project_id,
                "events": new_events[:5],
            },
        )

    return {
        "checked_metrics": checked,
        "snowflake_connected": sf_connected,
        "drift_detected": len(new_events),
        "events": new_events,
        "checked_at": now,
    }


def list_drift_events(project_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    events = _load_state()["events"]
    if project_id:
        events = [e for e in events if e.get("project_id") == project_id]
    if status:
        events = [e for e in events if e.get("status") == status]
    return events


def resolve_drift_event(event_id: str, action: str, reason: str = "") -> Optional[Dict[str, Any]]:
    state = _load_state()
    for ev in state["events"]:
        if ev["id"] == event_id:
            if action == "approve":
                ev["status"] = "approved"
                ev["resolution_reason"] = reason or "Approved for implementation"
            elif action == "ignore":
                ev["status"] = "ignored"
                ev["resolution_reason"] = reason or "Ignored by reviewer"
            else:
                ev["status"] = "resolved"
                ev["resolution_reason"] = reason
            ev["resolved_at"] = datetime.now(timezone.utc).isoformat()
            _save_state(state)
            return ev
    return None


def _get_scheduler():
    global _SCHEDULER
    if _SCHEDULER is None:
        from apscheduler.schedulers.background import BackgroundScheduler

        _SCHEDULER = BackgroundScheduler(daemon=True)
        _SCHEDULER.start()
    return _SCHEDULER


def create_schedule(
    *,
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    interval_minutes: int = 60,
    webhook_url: Optional[str] = None,
    job_type: str = "drift_and_parity",
) -> Dict[str, Any]:
    sched_id = f"sched-{uuid.uuid4().hex[:8]}"
    entry = {
        "id": sched_id,
        "project_id": project_id,
        "domain": domain,
        "interval_minutes": max(5, int(interval_minutes)),
        "webhook_url": webhook_url or "",
        "job_type": job_type,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state = _load_state()
    state["schedules"].append(entry)
    _save_state(state)

    try:
        scheduler = _get_scheduler()
        scheduler.add_job(
            run_drift_check,
            "interval",
            minutes=entry["interval_minutes"],
            id=sched_id,
            replace_existing=True,
            kwargs={"project_id": project_id, "domain": domain, "webhook_url": webhook_url},
        )
    except Exception as exc:
        logger.warning("Could not register APScheduler job %s: %s", sched_id, exc)

    return entry


def list_schedules(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    scheds = _load_state()["schedules"]
    if project_id:
        scheds = [s for s in scheds if s.get("project_id") == project_id]
    return scheds


def delete_schedule(schedule_id: str) -> bool:
    state = _load_state()
    before = len(state["schedules"])
    state["schedules"] = [s for s in state["schedules"] if s["id"] != schedule_id]
    _save_state(state)
    try:
        if _SCHEDULER is not None:
            _SCHEDULER.remove_job(schedule_id)
    except Exception:
        pass
    return len(state["schedules"]) < before

