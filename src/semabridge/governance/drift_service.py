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


_DAX_MARKERS = re.compile(r"\[|\b(CALCULATE|DIVIDE|SUMX|AVERAGEX|COUNTROWS|DISTINCTCOUNT|FILTER|ALL|TOTALYTD|SAMEPERIODLASTYEAR|RELATED|VALUES|SELECTEDVALUE)\s*\(", re.I)


def propose_snowflake_sql(metric_name: str, bi_definition: str) -> str:
    """Proposed Snowflake SQL for a drifted metric, from its BI definition.

    Goes through the same DAX translator the sync uses (never name-keyed
    demo strings). SQL definitions are proposed as-is. Returns "" when no
    faithful translation exists (the UI then shows no proposal; approving
    still re-syncs from the BI source)."""
    text = str(bi_definition or "").strip()
    if not text or text in ("—", "(not present in semantic view)"):
        return ""
    if not _DAX_MARKERS.search(text):
        return text  # already SQL (ontology sql_expression)
    try:
        from semabridge.converter.dax_translator import DAXTranslator

        result = DAXTranslator().translate(
            text, table_alias="", dataset_name="", metric_name=metric_name, skip_tier5=True,
        )
        return str(result.sql or "").strip()
    except Exception as exc:
        logger.debug("Drift proposal translation failed for %s: %s", metric_name, exc)
        return ""


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
        # Demo events seeded by earlier versions (flagged "mock") are not
        # real drift: they are dropped on read and never shown or acted on.
        events = [ev for ev in list(data.get("events") or []) if not ev.get("mock")]
        for ev in events:
            if not ev.get("proposed_snowflake_definition"):
                ev["proposed_snowflake_definition"] = propose_snowflake_sql(
                    ev.get("metric_name", ""),
                    ev.get("bi_definition", ""),
                )
        return {
            "schedules": list(data.get("schedules") or []),
            "events": events,
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
    # Strip table name qualifiers (e.g. SALESFACT.UNITS -> UNITS)
    s = re.sub(r'\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)\b', r'\1', s)
    # Strip table prefix denormalizations e.g. product_isvanarsdel -> isvanarsdel
    s = re.sub(r'\b(product|salesfact|date|geo|sentiment)_([A-Za-z0-9_]+)\b', r'\2', s, flags=re.I)
    # Strip SQL/DAX comment annotations
    s = re.sub(r'/\*.*?\*/', '', s)
    s = re.sub(r'--.*$', '', s, flags=re.M)
    s = re.sub(r'\s+', ' ', s).strip().lower()
    return s


def _view_key(view: Optional[str]) -> str:
    """Comparable key of a semantic view name (last identifier, case-folded)."""
    return str(view or "").strip().split(".")[-1].strip('"').casefold()


def drift_event_key(ev: Dict[str, Any]) -> tuple:
    """Dedup key of a drift event: (project, model-or-view, metric)."""
    pid = str(ev.get("project_id") or "").lower().removeprefix("proj-").strip()
    scope = str(ev.get("model_id") or "").strip().casefold() or _view_key(ev.get("semantic_view"))
    return (pid, scope, str(ev.get("metric_name") or "").lower().strip())


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

    # One resolver for "the project's domains" (no fallback to other projects'
    # domains); without a project every domain is checked.
    candidates = ontology_store.domains_for(project_id) if project_id else list(fed.domains.values())
    target_domains = [d for d in candidates if not domain or d.domain.lower() == domain.lower() or (d.id or "").lower() == domain.lower()]

    # Snowflake metrics per semantic view: {VIEW_TAIL: {METRIC: entry}} -- each
    # domain is compared against ITS OWN view only (multi-model projects have
    # one view per model, and same-named metrics differ between them).
    sf_metrics_by_view: Dict[str, Dict[str, Dict[str, str]]] = {}
    inspected_views: set = set()
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
                        if not sv or _view_key(sv) in inspected_views:
                            continue
                        sf_metrics = sf_metrics_by_view.setdefault(_view_key(sv), {})
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
                            inspected_views.add(_view_key(sv))
                        except Exception as exc:
                            logger.info("Drift check could not describe %s: %s", sv, exc)
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
        dom_view = getattr(dom, "semantic_view_name", None)
        view_inspected = bool(dom_view) and _view_key(dom_view) in inspected_views
        view_metrics = sf_metrics_by_view.get(_view_key(dom_view), {}) if dom_view else {}
        for m in dom.metrics:
            checked += 1
            m_key = m.name.upper()
            sf_entry = view_metrics.get(m_key)
            bi_expr = (m.sql_expression or m.dax_expression or "").strip()
            drift_reason = None
            sf_expr = ""
            if sf_connected and view_inspected and sf_entry is None:
                clean_sql = (m.sql_expression or "").strip().strip("'\"")
                if clean_sql and not clean_sql.isspace():
                    drift_reason = "MISSING_IN_SNOWFLAKE"
            elif sf_entry is not None:
                sf_expr = (sf_entry.get("expression") or "").strip()
                # Ignore expected null placeholder for measures without SQL
                if not (m.sql_expression or "").strip() or not sf_expr or "null" in sf_expr.lower():
                    pass
                elif _normalize_metric_expr(sf_expr) != _normalize_metric_expr(bi_expr):
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
                    "domain_id": dom.id,
                    "model_id": getattr(dom, "source_model_id", None),
                    "semantic_view": dom_view,
                    "metric_name": m.name,
                    "drift_type": drift_reason,
                    "bi_definition": bi_expr,
                    "snowflake_definition": sf_expr or "(not present in semantic view)",
                    "proposed_snowflake_definition": propose_snowflake_sql(m.name, bi_expr),
                    "affected_objects": affected,
                    "status": "pending",
                    "resolution_reason": "",
                    "detected_at": now,
                }
                new_events.append(ev)

    state = _load_state()
    # Deduplicate into state by (project, model/view, metric): same-named
    # metrics of two models of one project are different drift items. Legacy
    # events without model/view keep their (project, "", metric) key.
    event_map = {}
    for e in state.get("events") or []:
        event_map[drift_event_key(e)] = e
    for ev in new_events:
        key = drift_event_key(ev)
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

    pending_detected = [
        e for e in state.get("events") or []
        if e.get("status") == "pending"
        and (not project_id or str(e.get("project_id", "")).lower().removeprefix("proj-") == str(project_id).lower().removeprefix("proj-"))
    ]
    detected_count = len(new_events) if new_events else len(pending_detected)

    return {
        "checked_metrics": checked,
        "snowflake_connected": sf_connected,
        "drift_detected": detected_count,
        "events": new_events or pending_detected,
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
    """Clear all drift events for a project or all projects and synchronize baselines."""
    state = _load_state()
    orig = len(state.get("events", []))
    if project_id:
        p_clean = project_id.lower().removeprefix("proj-").strip()
        state["events"] = [
            e for e in state.get("events", [])
            if str(e.get("project_id") or "").lower().removeprefix("proj-").strip() != p_clean
        ]
        if "baselines" in state and p_clean in state["baselines"]:
            state["baselines"].pop(p_clean, None)
    else:
        state["events"] = []
        state["baselines"] = {}
    _save_state(state)
    return orig - len(state.get("events", []))


def _project_domain_for_metric(store: Any, project_id: str, metric_name: Optional[str]) -> Any:
    """The project's domain declaring ``metric_name`` (first project domain otherwise)."""
    doms = store.domains_for(project_id)
    if metric_name:
        for d in doms:
            if any(m.name.lower() == metric_name.lower() for m in d.metrics):
                return d
    return doms[0] if doms else None


def simulate_metric_drift(
    project_id: str,
    metric_name: Optional[str] = None,
    new_expression: Optional[str] = None,
) -> Dict[str, Any]:
    """Simulate changing a metric definition in the domain ontology so scheduled run reflects it as drift."""
    from semabridge.ontology.store import ontology_store
    target_dom = _project_domain_for_metric(ontology_store, project_id, metric_name)
    if not target_dom:
        # Never fall back to another project's domain: simulating would
        # rewrite that project's metric formula.
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

    # Record baseline for original expression before modifying
    state = _load_state()
    p_clean_target = project_id.lower().removeprefix("proj-").strip()
    proj_baselines = state.setdefault("baselines", {}).setdefault(p_clean_target, {})
    if m_target.name.upper() not in proj_baselines:
        proj_baselines[m_target.name.upper()] = _normalize_metric_expr(orig_expr)
    _save_state(state)

    if not new_expression:
        if "UNIT" in m_target.name.upper():
            mod_expr = "SUM(CASE WHEN SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') THEN SALESFACT.UNITS ELSE 0 END)"
        elif "DOL" in m_target.name.upper() or "REVENUE" in m_target.name.upper() or "SALES" in m_target.name.upper():
            mod_expr = "SUM(SALESFACT.REVENUE) - SUM(COALESCE(SALESFACT.DISCOUNT, 0))"
        elif "PRODUCT" in m_target.name.upper():
            mod_expr = "COUNT(DISTINCT CASE WHEN PRODUCT.IS_ACTIVE = TRUE THEN PRODUCT.PRODUCT_ID END)"
        else:
            mod_expr = f"SUM(CASE WHEN STATUS != 'Cancelled' THEN ({orig_expr}) ELSE 0 END)"
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
    # Never another project's domain: resetting would rewrite its formula.
    target_dom = _project_domain_for_metric(ontology_store, project_id, metric_name or "TOTAL_UNITS")

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

