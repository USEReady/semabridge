"""Scheduled Match & Drift Detection Service with Approval Workflow (APScheduler)."""

from __future__ import annotations

import json
import logging
import re
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


def _normalize_metric_expr(expr: str) -> str:
    """Normalize expressions to compare semantic intent across BI and Snowflake dialects."""
    if not expr:
        return ""
    s = expr.replace('"', '')
    # Strip cast syntax e.g. ::FLOAT, ::DOUBLE
    s = re.sub(r'::(FLOAT|DOUBLE|INT|INTEGER|NUMBER|VARCHAR|BOOLEAN)\b', '', s, flags=re.I)
    # Strip CAST(X AS TYPE)
    s = re.sub(r'CAST\s*\(\s*(.*?)\s+AS\s+[A-Za-z0-9_]+\s*\)', r'\1', s, flags=re.I)
    # Strip column qualifiers e.g. SALESFACT.UNITS -> UNITS
    s = re.sub(r'\b[A-Za-z0-9_]+\.([A-Za-z0-9_]+)\b', r'\1', s)
    # Strip SQL/DAX comment annotations
    s = re.sub(r'/\*.*?\*/', '', s)
    s = re.sub(r'--.*$', '', s, flags=re.M)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


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

    def _matches_project(d, pid: Optional[str]) -> bool:
        if not pid:
            return True
        p_clean = pid.lower().removeprefix("proj-")
        spid = (getattr(d, "source_project_id", None) or "").lower()
        did = (getattr(d, "id", None) or "").lower()
        dname = (getattr(d, "domain", None) or "").lower()
        return (
            spid == pid.lower()
            or spid.removeprefix("proj-") == p_clean
            or did == pid.lower()
            or did.removeprefix("proj-") == p_clean
            or dname == pid.lower()
        )

    target_domains = [
        d for d in fed.domains.values()
        if (not domain or d.domain.lower() == domain.lower())
        and _matches_project(d, project_id)
    ]
    if not target_domains and fed.domains:
        target_domains = list(fed.domains.values())

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

    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

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
                # Ignore expected null placeholder for measures without SQL
                if not (m.sql_expression or "").strip() and ("null" in sf_expr.lower() or not sf_expr):
                    pass
                elif sf_expr and bi_expr and _normalize_metric_expr(sf_expr) != _normalize_metric_expr(bi_expr):
                    drift_reason = "EXPRESSION_MISMATCH"

            if drift_reason:
                affected = [getattr(dom, "semantic_view_name", "SEMANTIC_VIEW") or "SEMANTIC_VIEW", m.name]
                try:
                    impact = sim.simulate(
                        target_object=m.name,
                        object_type="METRIC",
                        change_type="MODIFY_EXPRESSION",
                        project_id=project_id,
                    )
                    impact_items = getattr(impact, "affected_metrics", [])[:5] + getattr(impact, "affected_bi_reports", [])[:5]
                    if impact_items:
                        affected = [
                            *(x.name if hasattr(x, "name") else (x.get("name") if isinstance(x, dict) else str(x)) for x in impact_items)
                        ]
                except Exception:
                    pass
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

    # When mock drift is enabled and no real Snowflake inspection is active, generate a realistic drift event
    if not sf_connected and is_mock_drift_enabled() and target_domains:
        state_curr = _load_state()
        has_pending = any(
            e.get("status") == "pending" and (not project_id or e.get("project_id") == project_id)
            for e in state_curr.get("events", [])
        )
        if not has_pending:
            dom = target_domains[0]
            m = dom.metrics[0] if getattr(dom, "metrics", None) else None
            m_name = (m.name if m else None) or "COUNT_OF_PRODUCT"
            bi_raw = (m.sql_expression or m.dax_expression if m else None) or "COUNT(PRODUCT.\"PRODUCT\")"
            bi_updated = f"{bi_raw} * 1.05"
            sf_expr = f"{bi_raw}"
            sv = getattr(dom, "semantic_view_name", "SEMANTIC_VIEWS.COMP_ANALYSIS") or "SEMANTIC_VIEWS.COMP_ANALYSIS"
            ev = {
                "id": f"drift-{uuid.uuid4().hex[:10]}",
                "project_id": project_id or getattr(dom, "source_project_id", None) or "tiral_comp",
                "domain": dom.domain,
                "metric_name": m_name,
                "drift_type": "EXPRESSION_MISMATCH",
                "bi_definition": bi_updated,
                "snowflake_definition": sf_expr,
                "affected_objects": [sv, f"METRICS.{m_name}"],
                "status": "pending",
                "resolution_reason": "",
                "detected_at": now,
            }
            new_events.append(ev)

    state = _load_state()
    # Deduplicate into state by (project_id_clean, metric_name_clean)
    event_map = {}
    for e in state.get("events") or []:
        key = (str(e.get("project_id") or "").lower().removeprefix("proj-").strip(), str(e.get("metric_name") or "").lower().strip())
        event_map[key] = e
    for ev in new_events:
        key = (str(ev.get("project_id") or "").lower().removeprefix("proj-").strip(), str(ev.get("metric_name") or "").lower().strip())
        prev = event_map.get(key)
        if prev and prev.get("bi_definition") == ev.get("bi_definition") and prev.get("status") in ("approved", "ignored"):
            continue
        event_map[key] = ev
    state["events"] = list(event_map.values())[:200]
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
        p_clean = project_id.lower().removeprefix("proj-").strip()
        events = [
            e for e in events
            if str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean
        ]
    if status:
        events = [e for e in events if e.get("status") == status]
    return events


def clear_drift_events(project_id: Optional[str] = None) -> int:
    """Clear all drift events for a project or all projects."""
    state = _load_state()
    orig = len(state.get("events", []))
    if project_id:
        p_clean = project_id.lower().removeprefix("proj-").strip()
        state["events"] = [
            e for e in state.get("events", [])
            if str(e.get("project_id") or "").lower().removeprefix("proj-").strip() != p_clean
        ]
    else:
        state["events"] = []
    _save_state(state)
    return orig - len(state.get("events", []))


def simulate_metric_drift(
    project_id: str,
    metric_name: Optional[str] = None,
    new_expression: Optional[str] = None,
) -> Dict[str, Any]:
    """Simulate changing a metric definition in the domain ontology so scheduled run reflects it as drift."""
    from semabridge.ontology.store import ontology_store
    fed = ontology_store.get_federated_ontology()
    p_clean = project_id.lower().removeprefix("proj-").strip()
    target_dom = None
    for d in fed.domains.values():
        spid = (getattr(d, "source_project_id", None) or "").lower().removeprefix("proj-").strip()
        did = (getattr(d, "id", None) or "").lower().removeprefix("proj-").strip()
        if spid == p_clean or did == p_clean or d.domain.lower() == p_clean:
            target_dom = d
            break
    if not target_dom and fed.domains:
        target_dom = list(fed.domains.values())[0]
    if not target_dom:
        raise ValueError(f"No domain found for project '{project_id}'")

    m_target = None
    if metric_name:
        for m in target_dom.metrics:
            if m.name.lower() == metric_name.lower():
                m_target = m
                break
    if not m_target:
        for m in target_dom.metrics:
            if m.name in ("TOTAL_UNITS", "SALES_DOL", "COUNT_OF_PRODUCT"):
                m_target = m
                break
        if not m_target and target_dom.metrics:
            m_target = target_dom.metrics[0]

    if not m_target:
        raise ValueError("No metrics found in domain to modify.")

    orig_expr = m_target.sql_expression or m_target.dax_expression or "COUNT(*)"
    if not new_expression:
        mod_expr = f"{orig_expr} * 1.05"
    else:
        mod_expr = new_expression

    updated_metrics = []
    for m in target_dom.metrics:
        if m.name.lower() == m_target.name.lower():
            updated_metrics.append(m.model_copy(update={"sql_expression": mod_expr}))
        else:
            updated_metrics.append(m)

    updated_dom = target_dom.model_copy(update={"metrics": updated_metrics})
    ontology_store.add_or_update_domain(updated_dom, original=target_dom.domain, enforce_bi_formula_readonly=False)

    return {
        "project_id": project_id,
        "domain": target_dom.domain,
        "metric_name": m_target.name,
        "original_expression": orig_expr,
        "modified_expression": mod_expr,
        "message": f"Successfully updated metric '{m_target.name}' to '{mod_expr}'. Run project schedule to see drift approval trigger.",
    }


def reset_simulated_metric_drift(
    project_id: str,
    metric_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Reset a metric formula back to standard baseline expression."""
    from semabridge.ontology.store import ontology_store
    fed = ontology_store.get_federated_ontology()
    p_clean = project_id.lower().removeprefix("proj-").strip()
    target_dom = None
    for d in fed.domains.values():
        spid = (getattr(d, "source_project_id", None) or "").lower().removeprefix("proj-").strip()
        did = (getattr(d, "id", None) or "").lower().removeprefix("proj-").strip()
        if spid == p_clean or did == p_clean or d.domain.lower() == p_clean:
            target_dom = d
            break
    if not target_dom and fed.domains:
        target_dom = list(fed.domains.values())[0]

    baselines = {
        "TOTAL_UNITS": 'SUM(SALESFACT."UNITS")',
        "SALES_DOL": 'SUM(SALESFACT."REVENUE")',
        "COUNT_OF_PRODUCT": 'COUNT(PRODUCT."PRODUCT")',
        "SENTIMENT": 'AVG(SENTIMENT."SCORE")',
    }
    m_name = (metric_name or "TOTAL_UNITS").upper()
    reset_expr = baselines.get(m_name)
    if not reset_expr and target_dom:
        for m in target_dom.metrics:
            if m.name.upper() == m_name:
                curr = m.sql_expression or ""
                reset_expr = curr.split(" * 1.05")[0].split(" -- DRIFT")[0]
                break

    if target_dom and reset_expr:
        updated_metrics = []
        for m in target_dom.metrics:
            if m.name.upper() == m_name:
                updated_metrics.append(m.model_copy(update={"sql_expression": reset_expr}))
            else:
                updated_metrics.append(m)
        updated_dom = target_dom.model_copy(update={"metrics": updated_metrics})
        ontology_store.add_or_update_domain(updated_dom, original=target_dom.domain, enforce_bi_formula_readonly=False)

    return {
        "project_id": project_id,
        "metric_name": m_name,
        "reset_expression": reset_expr or "",
        "message": f"Reset metric '{m_name}' back to baseline.",
    }


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

