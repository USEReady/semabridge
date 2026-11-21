"""Isolated demo and mock drift fixtures, SQL derivation, and simulation helpers.

Only imported when demo mode / mock drift is enabled. Never imported by production drift_service.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

logger = logging.getLogger("semabridge.demo.drift_fixtures")

_CONFIGURED_DEMO_PROJECT_ID: Optional[str] = None


def get_configured_demo_project_id() -> Optional[str]:
    """Return the explicitly configured demo project ID."""
    global _CONFIGURED_DEMO_PROJECT_ID
    return (
        _CONFIGURED_DEMO_PROJECT_ID
        or os.environ.get("SEMABRIDGE_DEMO_PROJECT_ID")
        or os.environ.get("SEMABRIDGE_DEMO_PROJECT")
        or None
    )


def set_configured_demo_project_id(project_id: Optional[str]) -> None:
    """Set or clear the explicitly configured demo project ID."""
    global _CONFIGURED_DEMO_PROJECT_ID
    _CONFIGURED_DEMO_PROJECT_ID = project_id.strip() if project_id and project_id.strip() else None


def derive_semabridge_sql(metric_name: str, bi_definition: str) -> str:
    """Translate or optimize modified BI / DAX expression to governed Snowflake SQL via demo fixture mappings."""
    if not bi_definition or str(bi_definition).strip() in ("", "—", "(not present in semantic view)"):
        return "—"
    bi_clean = str(bi_definition).strip()
    m_up = (metric_name or "").upper()

    if "DSO" in m_up or "OUTSTANDING" in m_up:
        return 'DIV0(SUM(IFF(AR_SNAPSHOT."SNAPSHOT_DATE" IN (PERIOD."START_DATE", PERIOD."END_DATE") AND AR_SNAPSHOT."IS_INTERCOMPANY" = FALSE AND AR_SNAPSHOT."DISPUTE_STATUS" <> \'OPEN\', AR_SNAPSHOT."OPEN_AMOUNT", 0)) / 2.0, SUM(IFF(INVOICE."PAYMENT_TERMS" <> \'CASH\', INVOICE."NET_AMOUNT", 0))) * COUNT(DISTINCT CALENDAR."DATE_KEY")'
    if "NET_REVENUE" in m_up:
        return 'COALESCE(SUM(SALES."GROSS_AMOUNT"), 0) - SUM(IFF(DEDUCTION."TYPE" IN (\'CHARGEBACK_340B\', \'CHARGEBACK_WAC\'), DEDUCTION."AMOUNT", 0)) - SUM(IFF(DEDUCTION."TYPE" = \'MEDICAID_REBATE\', DEDUCTION."ACCRUED_AMOUNT", 0)) - SUM(IFF(DEDUCTION."TYPE" IN (\'COPAY_ASSIST\', \'RETURNS_RESERVE\'), DEDUCTION."AMOUNT", 0))'
    if "GROWTH" in m_up or "YOY" in m_up:
        return 'DIV0(SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0)) - SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY - 1 AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0)), SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY - 1 AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0))) * 100'
    if "MARKET_SHARE" in m_up or "MKT_SHARE" in m_up or "SHARE" in m_up or "PCT_UNITS" in m_up:
        return 'DIV0(SUM(IFF(PRODUCT."MANUFACTURER" = \'VanArsdel\' AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')), SALESFACT."UNITS", 0)), SUM(IFF(NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND PRODUCT."SEGMENT" IN (SELECT DISTINCT "SEGMENT" FROM PRODUCT WHERE "MANUFACTURER" = \'VanArsdel\'), SALESFACT."UNITS", 0))) * 100'
    if "MARGIN" in m_up:
        return 'ROUND(DIV0(SUM(SALESFACT."REVENUE") - SUM(SALESFACT."COGS"), SUM(SALESFACT."REVENUE")) * 100, 2)'
    if "UNIT" in m_up:
        return 'SUM(IFF(NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')), SALESFACT."UNITS", 0))'
    if "PRODUCT" in m_up:
        return 'COUNT(DISTINCT PRODUCT."PRODUCTID")'
    if "SALES" in m_up or "REVENUE" in m_up:
        return 'COALESCE(SUM(SALESFACT."REVENUE"), 0) - COALESCE(SUM(SALESFACT."DISCOUNT"), 0)'
    if "SENTIMENT" in m_up or "SCORE" in m_up:
        return 'AVG(IFF(SENTIMENT."SCORE" IS NOT NULL, CAST(SENTIMENT."SCORE" AS FLOAT), NULL))'

    # General SQL transformation heuristics
    s = bi_clean
    s = re.sub(r"(?i)DIVIDE\(([^,]+),\s*([^,]+)(?:,\s*[^)]+)?\)", r"DIV0(\1, \2)", s)
    s = re.sub(r"(?i)CALCULATE\(\s*SUM\(([^)]+)\)\s*,\s*FILTER\([^,]+,\s*([^)]+)\)\)", r"SUM(IFF(\2, \1, 0))", s)
    s = re.sub(r"(?i)CALCULATE\(\s*SUM\(([^)]+)\)\s*,\s*([^)]+)\)", r"SUM(IFF(\2, \1, 0))", s)
    s = re.sub(r"(?i)COUNTROWS\(DISTINCT\(([^)]+)\)\)", r"COUNT(DISTINCT \1)", s)
    s = re.sub(r"(?i)DISTINCTCOUNT\(([^)]+)\)", r"COUNT(DISTINCT \1)", s)
    s = re.sub(r"(?i)AVERAGE\(([^)]+)\)", r"AVG(\1)", s)
    return s


def _load_demo_json_fixtures() -> List[Dict[str, Any]]:
    """Load base demo drift fixtures from Config/demo/drift_events.json."""
    candidates = [
        Path(__file__).resolve().parents[3] / "Config" / "demo" / "drift_events.json",
        Path.cwd() / "Config" / "demo" / "drift_events.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Could not read demo fixture file %s: %s", p, exc)
    return []


def get_default_mock_drift_events(project_id: str, force_demo: bool = False) -> List[Dict[str, Any]]:
    """Return mock drift events for project demonstration only when demo flag is enabled."""
    if not force_demo and not is_mock_drift_enabled():
        return []

    pid = str(project_id or "").strip()
    if not pid:
        return []

    fixtures = _load_demo_json_fixtures()
    now = datetime.now(timezone.utc)
    events: List[Dict[str, Any]] = []

    for item in fixtures:
        ev = dict(item)
        ev["project_id"] = pid
        ev["mock"] = True
        offset = item.get("offset_minutes", 10)
        ev["detected_at"] = (now - timedelta(minutes=offset)).isoformat()
        if "resolved_offset_minutes" in item:
            ev["resolved_at"] = (now - timedelta(minutes=item["resolved_offset_minutes"])).isoformat()
        ev.pop("offset_minutes", None)
        ev.pop("resolved_offset_minutes", None)
        events.append(ev)

    return events


def seed_mock_drift_events(project_id: str) -> List[Dict[str, Any]]:
    """Re-populate demo drift events for ``project_id`` (flagged ``mock: True``)."""
    from semabridge.governance import drift_service

    pid = str(project_id or "").strip()
    if not pid:
        return []

    p_clean = pid.lower().removeprefix("proj-").strip()
    state = drift_service._load_state()
    events = list(state.get("events") or [])

    # Preserve original detected_at timestamps for existing mock events across refreshes
    existing_mock_timestamps = {
        e["id"]: e.get("detected_at")
        for e in events
        if e.get("mock")
        and str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean
        and e.get("detected_at")
    }

    kept = [
        e for e in events
        if not (e.get("mock") and str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean)
    ]
    real_metrics = {
        str(e.get("metric_name") or "").lower()
        for e in kept
        if str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean and not e.get("mock")
    }

    seeded = []
    for e in get_default_mock_drift_events(pid, force_demo=True):
        if str(e.get("metric_name") or "").lower() in real_metrics:
            continue
        ev_copy = {**e, "mock": True}
        if ev_copy["id"] in existing_mock_timestamps:
            ev_copy["detected_at"] = existing_mock_timestamps[ev_copy["id"]]
        seeded.append(ev_copy)

    state["events"] = seeded + kept
    drift_service._save_state(state)
    return [e for e in state["events"] if str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean]


def cleanup_mock_drift_events(demo_project_id: Optional[str] = None) -> Dict[str, int]:
    """Remove mock events whose project_id is not the configured demo project."""
    from semabridge.governance import drift_service

    demo_pid = demo_project_id or get_configured_demo_project_id()
    demo_clean = str(demo_pid or "").lower().removeprefix("proj-").strip() if demo_pid else None

    state = drift_service._load_state()
    events = list(state.get("events") or [])
    kept: List[Dict[str, Any]] = []
    removed_per_project: Dict[str, int] = {}

    for ev in events:
        is_mock = bool(ev.get("mock"))
        ev_pid = str(ev.get("project_id") or "").strip()
        ev_clean = ev_pid.lower().removeprefix("proj-").strip() if ev_pid else None

        if is_mock:
            if demo_clean and ev_clean == demo_clean:
                kept.append(ev)
            else:
                label = ev_pid or "unknown"
                removed_per_project[label] = removed_per_project.get(label, 0) + 1
        else:
            kept.append(ev)

    if len(kept) != len(events):
        state["events"] = kept
        drift_service._save_state(state)
        logger.info("Cleaned up orphaned mock drift events: %s", removed_per_project)

    return removed_per_project


def simulate_metric_drift(
    project_id: str,
    metric_name: Optional[str] = None,
    new_expression: Optional[str] = None,
) -> Dict[str, Any]:
    """Simulate changing a metric definition without writing to the real ontology store or baselines."""
    from semabridge.governance import drift_service
    from semabridge.ontology.store import ontology_store

    pid = str(project_id or "").strip()
    doms = ontology_store.domains_for(pid)
    target_dom = doms[0] if doms else None
    domain_name = getattr(target_dom, "domain", "DEFAULT")

    m_target = None
    if target_dom and metric_name:
        for m in getattr(target_dom, "metrics", []):
            if m.name.lower() == metric_name.lower():
                m_target = m
                break
    if not m_target and target_dom and getattr(target_dom, "metrics", None):
        m_target = target_dom.metrics[0]

    m_name = (m_target.name if m_target else metric_name) or "SAMPLE_METRIC"
    orig_expr = (m_target.sql_expression or m_target.dax_expression if m_target else "COUNT(*)") or "COUNT(*)"
    if not new_expression:
        mod_expr = f"SUM(CASE WHEN STATUS NOT IN ('Cancelled', 'Returned') THEN ({orig_expr}) ELSE 0 END)"
    else:
        mod_expr = new_expression

    state = drift_service._load_state()
    p_clean = pid.lower().removeprefix("proj-").strip()

    sim_ev = {
        "id": f"drift-sim-{uuid.uuid4().hex[:8]}",
        "project_id": pid,
        "domain": domain_name,
        "metric_name": m_name,
        "drift_type": "EXPRESSION_MISMATCH",
        "bi_definition": mod_expr,
        "snowflake_definition": orig_expr,
        "proposed_snowflake_definition": mod_expr,
        "mock": True,
        "status": "pending",
        "resolution_reason": "",
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }

    # Store in events without touching baselines or ontology store
    existing_events = list(state.get("events") or [])
    kept = [
        e for e in existing_events
        if not (e.get("mock") and str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean and e.get("metric_name") == m_name)
    ]
    state["events"] = [sim_ev] + kept
    drift_service._save_state(state)

    return {
        "project_id": pid,
        "domain": domain_name,
        "metric_name": m_name,
        "original_expression": orig_expr,
        "modified_expression": mod_expr,
        "message": f"Successfully simulated drift for metric '{m_name}' to '{mod_expr}'.",
    }


def reset_simulated_metric_drift(
    project_id: str,
    metric_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Reset simulated metric drift without touching the real ontology store or baselines."""
    from semabridge.governance import drift_service

    pid = str(project_id or "").strip()
    p_clean = pid.lower().removeprefix("proj-").strip()
    state = drift_service._load_state()

    existing_events = list(state.get("events") or [])
    state["events"] = [
        e for e in existing_events
        if not (
            e.get("mock")
            and str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean
            and (not metric_name or str(e.get("metric_name") or "").lower() == metric_name.lower())
        )
    ]
    drift_service._save_state(state)

    return {
        "project_id": pid,
        "metric_name": metric_name or "ALL",
        "reset_expression": "",
        "message": f"Reset simulated drift for '{metric_name or 'project'}'.",
    }
