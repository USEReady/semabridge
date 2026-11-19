"""Scheduled Match & Drift Detection Service with Approval Workflow (APScheduler)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.governance.drift_service")

_SCHEDULER = None


def derive_semabridge_sql(metric_name: str, bi_definition: str) -> str:
    """Translate or optimize modified BI / DAX expression to governed Snowflake SQL via SemaBridge logic."""
    if not bi_definition or str(bi_definition).strip() in ("", "—", "(not present in semantic view)"):
        return "—"
    bi_clean = str(bi_definition).strip()
    m_up = (metric_name or "").upper()

    # Specific known enterprise metric mappings
    if "DSO" in m_up or "OUTSTANDING" in m_up:
        return 'DIV0(SUM(IFF(AR_SNAPSHOT."SNAPSHOT_DATE" IN (PERIOD."START_DATE", PERIOD."END_DATE") AND AR_SNAPSHOT."IS_INTERCOMPANY" = FALSE AND AR_SNAPSHOT."DISPUTE_STATUS" <> \'OPEN\', AR_SNAPSHOT."OPEN_AMOUNT", 0)) / 2.0, SUM(IFF(INVOICE."PAYMENT_TERMS" <> \'CASH\', INVOICE."NET_AMOUNT", 0))) * COUNT(DISTINCT CALENDAR."DATE_KEY")'
    if "NET_REVENUE" in m_up:
        return 'COALESCE(SUM(SALES."GROSS_AMOUNT"), 0) - SUM(IFF(DEDUCTION."TYPE" IN (\'CHARGEBACK_340B\', \'CHARGEBACK_WAC\'), DEDUCTION."AMOUNT", 0)) - SUM(IFF(DEDUCTION."TYPE" = \'MEDICAID_REBATE\', DEDUCTION."ACCRUED_AMOUNT", 0)) - SUM(IFF(DEDUCTION."TYPE" IN (\'COPAY_ASSIST\', \'RETURNS_RESERVE\'), DEDUCTION."AMOUNT", 0))'
    if "GROWTH" in m_up or "YOY" in m_up:
        return 'DIV0(SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0)) - SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY - 1 AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0)), SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY - 1 AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0))) * 100'
    if "YTD" in m_up:
        return 'SUM(IFF(DATE."DATE" BETWEEN DATE_TRUNC(\'YEAR\', CURRENT_DATE()) AND CURRENT_DATE(), SALESFACT."UNITS", 0))'
    if "VANARSDEL" in m_up:
        return 'SUM(IFF(PRODUCT."ISVANARSDEL" = TRUE AND SALESFACT."STATUS" = \'Delivered\', SALESFACT."UNITS", 0))'
    if "MARKET_SHARE" in m_up or "SHARE" in m_up:
        return 'DIV0(SUM(IFF(PRODUCT."MANUFACTURER" = \'VanArsdel\' AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')), SALESFACT."UNITS", 0)), SUM(IFF(NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND PRODUCT."SEGMENT" IN (SELECT DISTINCT "SEGMENT" FROM PRODUCT WHERE "MANUFACTURER" = \'VanArsdel\'), SALESFACT."UNITS", 0))) * 100'
    if "CATEGORY" in m_up:
        return 'SUM(IFF(SALESFACT."STATUS" <> \'Cancelled\', SALESFACT."UNITS", 0)) OVER (PARTITION BY PRODUCT."CATEGORY")'
    if "UNIT" in m_up:
        if "Cancelled" in bi_clean or "STATUS" in bi_clean.upper():
            return 'SUM(IFF(NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')), SALESFACT."UNITS", 0))'
        return 'SUM(SALESFACT."UNITS")'
    if "PRODUCT" in m_up:
        if "DISTINCT" in bi_clean.upper():
            return 'COUNT(DISTINCT PRODUCT."PRODUCTID")'
        return 'COUNT(PRODUCT."PRODUCT")'
    if "MARGIN" in m_up:
        return 'ROUND(DIV0(SUM(SALESFACT."REVENUE") - SUM(SALESFACT."COGS"), SUM(SALESFACT."REVENUE")) * 100, 2)'
    if "DOL" in m_up or "REVENUE" in m_up or "SALES" in m_up:
        if "DISCOUNT" in bi_clean.upper():
            return 'COALESCE(SUM(SALESFACT."REVENUE"), 0) - COALESCE(SUM(SALESFACT."DISCOUNT"), 0)'
        return 'SUM(SALESFACT."REVENUE")'
    if "SENTIMENT" in m_up or "SCORE" in m_up:
        return 'AVG(IFF(SENTIMENT."SCORE" IS NOT NULL, CAST(SENTIMENT."SCORE" AS FLOAT), NULL))'

    # General conversion: normalize CASE / IF / DISTINCTCOUNT / AVERAGE to Snowflake SQL
    s = bi_clean
    s = re.sub(r"(?i)DISTINCTCOUNT\(([^)]+)\)", r"COUNT(DISTINCT \1)", s)
    s = re.sub(r"(?i)AVERAGE\(([^)]+)\)", r"AVG(\1)", s)
    return s


_DAX_MARKERS = re.compile(r"\[|\b(CALCULATE|DIVIDE|SUMX|AVERAGEX|COUNTROWS|DISTINCTCOUNT|FILTER|ALL|TOTALYTD|SAMEPERIODLASTYEAR|RELATED|VALUES|SELECTEDVALUE)\s*\(", re.I)


def propose_snowflake_sql(metric_name: str, bi_definition: str, *, mock: bool = False) -> str:
    """Proposed Snowflake SQL for a drifted metric, from its BI definition.

    Demo (mock) events keep their curated SQL (derive_semabridge_sql). Real
    events go through the same DAX translator the sync uses -- never the
    name-keyed demo strings, which produced unrelated SQL for real metrics.
    SQL definitions are proposed as-is. Returns "" when no faithful
    translation exists (the UI then shows no proposal; approving still
    re-syncs from the BI source)."""
    if mock:
        return derive_semabridge_sql(metric_name, bi_definition)
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


def get_default_mock_drift_events(project_id: str = "proj-comp") -> List[Dict[str, Any]]:
    """Return high-fidelity mock drift events for project demonstration and monitoring."""
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(minutes=18)).isoformat()
    t2 = (now - timedelta(minutes=42)).isoformat()
    t3 = (now - timedelta(hours=1, minutes=10)).isoformat()
    t4 = (now - timedelta(hours=2, minutes=5)).isoformat()
    t5 = (now - timedelta(hours=3, minutes=15)).isoformat()
    t6 = (now - timedelta(hours=4, minutes=20)).isoformat()
    t7 = (now - timedelta(days=1, hours=1)).isoformat()
    t7_res = (now - timedelta(days=1, minutes=30)).isoformat()
    t8 = (now - timedelta(days=1, hours=3)).isoformat()
    t8_res = (now - timedelta(days=1, hours=2, minutes=30)).isoformat()
    t9 = (now - timedelta(days=2, hours=5)).isoformat()
    t9_res = (now - timedelta(days=2, hours=4)).isoformat()

    pid_lower = (project_id or "").lower()
    if "sales" in pid_lower:
        # Regional sales sample is designated clean with zero drift
        return []

    # Default to Competitive Marketing Analysis (proj-comp)
    return [
        {
            "id": "drift-comp-mkt-share",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "PCT_UNITS_MARKET_SHARE",
            "drift_type": "EXPRESSION_MISMATCH",
            "badges": ["EXPRESSION_MISMATCH", "FILTER_ADDED", "WINDOW_CHANGED", "PENDING_APPROVAL"],
            "story": "Marketing changed how share is measured. Share now counts only valid sales, is measured within the segments VanArsdel actually competes in, and uses a rolling 12 months.",
            "bi_definition": "SUM(CASE WHEN PRODUCT.MANUFACTURER = 'VanArsdel' AND SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') THEN SALESFACT.UNITS ELSE 0 END) / NULLIF(SUM(CASE WHEN SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') AND PRODUCT.SEGMENT IN (SELECT DISTINCT SEGMENT FROM PRODUCT WHERE MANUFACTURER = 'VanArsdel') THEN SALESFACT.UNITS ELSE 0 END), 0) -- window: rolling 12 months ending on selected date",
            "snowflake_definition": "SUM(CASE WHEN PRODUCT.MANUFACTURER = 'VanArsdel' THEN SALESFACT.UNITS ELSE 0 END) / NULLIF(SUM(SALESFACT.UNITS), 0) -- window: selected period",
            "proposed_snowflake_definition": 'DIV0(SUM(IFF(PRODUCT."MANUFACTURER" = \'VanArsdel\' AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')), SALESFACT."UNITS", 0)), SUM(IFF(NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND PRODUCT."SEGMENT" IN (SELECT DISTINCT "SEGMENT" FROM PRODUCT WHERE "MANUFACTURER" = \'VanArsdel\'), SALESFACT."UNITS", 0))) * 100',
            "what_changed": [
                "Cancelled and returned units are excluded from both numerator and denominator (matches TOTAL_UNITS drift)",
                "The market is now only the segments VanArsdel competes in, not every segment",
                "The period is now a rolling 12 months instead of the selected period",
            ],
            "value_impact": "Q3 2026: 13.8% (Snowflake) vs 17.2% (BI), a +3.4 percentage point difference.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.PCT_UNITS_MARKET_SHARE", "REPORTS.EXECUTIVE_DASHBOARD", "REPORTS.MARKET_SHARE_BY_REGION"],
            "status": "pending",
            "resolution_reason": "",
            "detected_at": t1,
        },
        {
            "id": "drift-comp-rev-growth",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "REVENUE_YOY_GROWTH",
            "drift_type": "EXPRESSION_MISMATCH",
            "badges": ["EXPRESSION_MISMATCH", "DATE_LOGIC_CHANGED", "PENDING_APPROVAL"],
            "story": "Finance moved to fiscal years starting in July, and to like-for-like growth. Only regions that had sales in both years are compared, so new territories no longer inflate growth.",
            "bi_definition": "( SUM(CASE WHEN DATE_DIM.FISCAL_YEAR = :CURRENT_FY AND SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') AND GEO.REGION IN (SELECT REGION FROM REGIONS_ACTIVE_BOTH_FY) THEN SALESFACT.REVENUE ELSE 0 END) - SUM(CASE WHEN DATE_DIM.FISCAL_YEAR = :CURRENT_FY - 1 AND SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') AND GEO.REGION IN (SELECT REGION FROM REGIONS_ACTIVE_BOTH_FY) THEN SALESFACT.REVENUE ELSE 0 END) ) / NULLIF(SUM(CASE WHEN DATE_DIM.FISCAL_YEAR = :CURRENT_FY - 1 AND SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') AND GEO.REGION IN (SELECT REGION FROM REGIONS_ACTIVE_BOTH_FY) THEN SALESFACT.REVENUE ELSE 0 END), 0) -- fiscal year starts 1 July",
            "snowflake_definition": "( SUM(CASE WHEN YEAR(SALESFACT.DATE) = :CURRENT_YEAR THEN SALESFACT.REVENUE ELSE 0 END) - SUM(CASE WHEN YEAR(SALESFACT.DATE) = :CURRENT_YEAR - 1 THEN SALESFACT.REVENUE ELSE 0 END) ) / NULLIF(SUM(CASE WHEN YEAR(SALESFACT.DATE) = :CURRENT_YEAR - 1 THEN SALESFACT.REVENUE ELSE 0 END), 0) -- calendar year",
            "proposed_snowflake_definition": 'DIV0(SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0)) - SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY - 1 AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0)), SUM(IFF(DATE_DIM."FISCAL_YEAR" = :CURRENT_FY - 1 AND NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')) AND GEO."REGION" IN (SELECT "REGION" FROM REGIONS_ACTIVE_BOTH_FY), SALESFACT."REVENUE", 0))) * 100',
            "what_changed": [
                "Calendar year → fiscal year starting in July",
                "Like-for-like: only regions with sales in both years are counted",
                "Cancelled and returned orders are excluded",
            ],
            "value_impact": "Q3 2026: +11.4% (Snowflake) vs +7.9% (BI), a −3.5 percentage point like-for-like correction.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.REVENUE_YOY_GROWTH", "REPORTS.EXECUTIVE_DASHBOARD", "REPORTS.REGIONAL_SALES"],
            "status": "pending",
            "resolution_reason": "",
            "detected_at": t2,
        },
        {
            "id": "drift-90fe41780e",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "TOTAL_UNITS",
            "drift_type": "EXPRESSION_MISMATCH",
            "badges": ["EXPRESSION_MISMATCH", "FILTER_ADDED", "PENDING_APPROVAL"],
            "story": "Operations excluded cancelled and returned transactions from gross dispatch reporting.",
            "bi_definition": "SUM(CASE WHEN SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') THEN SALESFACT.UNITS ELSE 0 END)",
            "snowflake_definition": "SUM(SALESFACT.UNITS)",
            "proposed_snowflake_definition": 'SUM(IFF(NOT (SALESFACT."STATUS" IN (\'Cancelled\', \'Returned\')), SALESFACT."UNITS", 0))',
            "what_changed": ["Excludes cancelled and returned status from unit aggregation"],
            "value_impact": "Q3 2026: 2,412 units (Snowflake) vs 2,291 units (BI), a −5.0% return adjustment.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.TOTAL_UNITS", "REPORTS.EXECUTIVE_DASHBOARD"],
            "status": "pending",
            "resolution_reason": "",
            "detected_at": t3,
        },
        {
            "id": "drift-84ac12e9b1",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "COUNT_OF_PRODUCT",
            "drift_type": "EXPRESSION_MISMATCH",
            "badges": ["EXPRESSION_MISMATCH", "PENDING_APPROVAL"],
            "story": "Assortment team standardized distinct SKU tracking across active product catalogues.",
            "bi_definition": "COUNT(DISTINCT PRODUCT.PRODUCT_ID)",
            "snowflake_definition": "COUNT(PRODUCT.PRODUCT)",
            "proposed_snowflake_definition": 'COUNT(DISTINCT PRODUCT."PRODUCTID")',
            "what_changed": ["Switched product name count to distinct product ID primary key"],
            "value_impact": "Discrepancy: 14 product names vs 10 unique product IDs in warehouse.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.COUNT_OF_PRODUCT", "REPORTS.PORTFOLIO_DEEP_DIVE"],
            "status": "pending",
            "resolution_reason": "",
            "detected_at": t4,
        },
        {
            "id": "drift-99ef23cd48",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "MARGIN_PCT",
            "drift_type": "EXPRESSION_MISMATCH",
            "badges": ["EXPRESSION_MISMATCH", "APPROVED"],
            "story": "Finance Controller aligned gross margin divisor with Snowflake GAAP reporting standards.",
            "bi_definition": "DIVIDE(SUM(SALESFACT.REVENUE) - SUM(SALESFACT.COGS), SUM(SALESFACT.REVENUE), 0) * 100",
            "snowflake_definition": "((SUM(SALESFACT.REVENUE) - SUM(SALESFACT.COGS)) / NULLIF(SUM(SALESFACT.REVENUE), 0)) * 100",
            "proposed_snowflake_definition": 'ROUND(DIV0(SUM(SALESFACT."REVENUE") - SUM(SALESFACT."COGS"), SUM(SALESFACT."REVENUE")) * 100, 2)',
            "what_changed": ["Reconciled COGS deduction formula with GAAP gross margin standard"],
            "value_impact": "Approved sync: aligned margin percentages within 0.01% tolerance.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.MARGIN_PCT", "REPORTS.COMMERCIAL_AUDIT"],
            "status": "approved",
            "resolution_reason": "Approved by Financial Controller: aligned gross margin divisor with Snowflake GAAP reporting standards.",
            "detected_at": t5,
            "resolved_at": t7_res,
        },
        {
            "id": "drift-71fd55c3a4",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "SALES_DOL",
            "drift_type": "EXPRESSION_MISMATCH",
            "badges": ["EXPRESSION_MISMATCH", "APPROVED"],
            "story": "Commercial finance subtracted promotional customer discounts from gross sales totals.",
            "bi_definition": "SUM(SALESFACT.REVENUE) - SUM(SALESFACT.DISCOUNT)",
            "snowflake_definition": "SUM(SALESFACT.REVENUE)",
            "proposed_snowflake_definition": 'COALESCE(SUM(SALESFACT."REVENUE"), 0) - COALESCE(SUM(SALESFACT."DISCOUNT"), 0)',
            "what_changed": ["Gross sales revenue netted against promotional discounts"],
            "value_impact": "Approved sync: verified against Snowflake enterprise sales views.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.SALES_DOL", "METRICS.MARGIN_PCT", "REPORTS.COMMERCIAL_AUDIT"],
            "status": "approved",
            "resolution_reason": "Approved by Data Steward: promotional discounts subtracted from gross sales.",
            "detected_at": t6,
            "resolved_at": t8_res,
        },
        {
            "id": "drift-52be9934c7",
            "project_id": project_id,
            "domain": "COMP_ANALYSIS",
            "metric_name": "SENTIMENT_SCORE",
            "drift_type": "MISSING_IN_SNOWFLAKE",
            "badges": ["MISSING_IN_SNOWFLAKE", "IGNORED"],
            "story": "Experimental consumer sentiment index created in BI workbook for testing.",
            "bi_definition": "AVERAGE(SENTIMENT.SCORE)",
            "snowflake_definition": "(not present in semantic view)",
            "proposed_snowflake_definition": 'AVG(IFF(SENTIMENT."SCORE" IS NOT NULL, CAST(SENTIMENT."SCORE" AS FLOAT), NULL))',
            "what_changed": ["Ad-hoc metric kept isolated in local BI workspace"],
            "value_impact": "No warehouse impact.",
            "affected_objects": ["SEMANTIC_VIEWS.COMP_ANALYSIS", "METRICS.SENTIMENT_SCORE", "REPORTS.PORTFOLIO_DEEP_DIVE"],
            "status": "ignored",
            "resolution_reason": "Ignored: experimental BI metric not designated for warehouse promotion.",
            "detected_at": t9,
            "resolved_at": t9_res,
        },
    ]


def seed_mock_drift_events(project_id: str = "proj-comp") -> List[Dict[str, Any]]:
    """Re-populate the demo drift events of ``project_id`` (flagged ``mock``).

    Only previously seeded demo events of that project are replaced: real
    drift events -- of this or any other project -- are never touched, and a
    real event for a metric always wins over a demo one."""
    from semabridge.core.demo_mode import demo_mode_globally_armed
    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

    if not (demo_mode_globally_armed() or is_mock_drift_enabled()):
        return []

    state = _load_state()
    events = list(state.get("events") or [])
    pid = project_id or "proj-comp"
    kept = [e for e in events if not (e.get("mock") and e.get("project_id") == pid)]
    real_metrics = {
        str(e.get("metric_name") or "").lower()
        for e in kept
        if e.get("project_id") == pid and not e.get("mock")
    }
    seeded = []
    for e in get_default_mock_drift_events(pid):
        if str(e.get("metric_name") or "").lower() in real_metrics:
            continue
        seeded.append({**e, "mock": True})
    state["events"] = seeded + kept
    _save_state(state)
    return [e for e in state["events"] if e.get("project_id") == pid]


def _state_path() -> Path:
    """The local JSON file: drift schedules and baselines (per host), and
    the drift events only while the database is unavailable. Under pytest
    it never resolves to the real per-user file."""
    import os

    override = os.environ.get("SEMABRIDGE_DRIFT_STATE_PATH")
    if override:
        p = Path(override)
    elif "PYTEST_CURRENT_TEST" in os.environ:
        import tempfile

        p = Path(tempfile.gettempdir()) / "semabridge_test_drift_state" / "drift_state.json"
    else:
        p = Path.home() / ".semabridge" / "drift_state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------------------
# Drift events live in the database (``drift_events``) so every server
# process sees the same list; the JSON file above was per host. The list
# order, dedupe key and 200-event cap are unchanged (applied by the callers).
# ---------------------------------------------------------------------------

_LOADED_IDS_KEY = "_loaded_event_ids"  # private: which rows a save may delete
_IMPORTED_MARKER = "events_imported_to_db"
_drift_tables_ready: set = set()


def _drift_session():
    from semabridge.repository.orm.session_factory import db_manager

    return db_manager.get_session()


def _ensure_drift_table() -> None:
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import DriftEventRow
    from semabridge.repository.orm.session_factory import db_manager

    engine = db_manager.get_engine()
    if id(engine) in _drift_tables_ready:
        return
    Base.metadata.create_all(bind=engine, tables=[DriftEventRow.__table__], checkfirst=True)
    _drift_tables_ready.add(id(engine))


def _read_json_file() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("Drift state file %s could not be read: %s", p, exc)
        return {}


def _write_json_file(data: Dict[str, Any]) -> None:
    _state_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def _db_load_events() -> Optional[List[Dict[str, Any]]]:
    """Events from the database, in list order; None when it cannot be read."""
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import DriftEventRow

        _ensure_drift_table()
        with _drift_session() as session:
            rows = session.execute(
                select(DriftEventRow.payload_json).order_by(DriftEventRow.position, DriftEventRow.updated_at)
            ).all()
    except Exception as exc:  # noqa: BLE001 -- the JSON file is the fallback
        logger.warning("Drift events could not be read from the database (using the local file): %s", exc)
        return None
    events: List[Dict[str, Any]] = []
    for (payload,) in rows:
        try:
            ev = json.loads(payload)
        except ValueError as exc:
            logger.warning("Skipping unreadable drift event row: %s", exc)
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _db_save_events(events: List[Dict[str, Any]], loaded_ids: Optional[set]) -> bool:
    """Upsert ``events`` (position = list index) and delete the rows that
    were loaded but are no longer in the list. Rows another process added
    meanwhile are kept. False when the database cannot be written."""
    try:
        from sqlalchemy import delete
        from semabridge.repository.orm.models import DriftEventRow

        _ensure_drift_table()
        now = datetime.now(timezone.utc)
        keep = [ev for ev in events if isinstance(ev, dict) and ev.get("id")]
        keep_ids = {str(ev["id"]) for ev in keep}
        with _drift_session() as session:
            removed = {str(i) for i in (loaded_ids or set())} - keep_ids
            if removed:
                session.execute(delete(DriftEventRow).where(DriftEventRow.id.in_(sorted(removed))))
            for position, ev in enumerate(keep):
                row = session.get(DriftEventRow, str(ev["id"]))
                if row is None:
                    row = DriftEventRow(id=str(ev["id"]))
                    session.add(row)
                row.project_id = str(ev.get("project_id") or "") or None
                row.status = str(ev.get("status") or "") or None
                row.position = position
                row.payload_json = json.dumps(ev, default=str)
                row.updated_at = now
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 -- the JSON file is the fallback
        logger.warning("Drift events could not be saved to the database (kept in the local file): %s", exc)
        return False


def _import_json_events_once(db_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """First use after the move to the database: an empty table takes the
    events of the old JSON file (once; the file is then marked imported)."""
    if db_events:
        return db_events
    data = _read_json_file()
    file_events = [e for e in (data.get("events") or []) if isinstance(e, dict) and e.get("id")]
    if not file_events or data.get(_IMPORTED_MARKER):
        return db_events
    if not _db_save_events(file_events, set()):
        return file_events
    data[_IMPORTED_MARKER] = True
    try:
        _write_json_file(data)
    except OSError as exc:
        logger.warning("Drift state file could not be marked as imported: %s", exc)
    logger.info("Imported %d drift event(s) from %s into the database", len(file_events), _state_path())
    return file_events


def _load_state() -> Dict[str, Any]:
    data = _read_json_file()
    db_events = _db_load_events()
    if db_events is None:
        events = list(data.get("events") or [])
    else:
        events = _import_json_events_once(db_events)
    for ev in events:
        if not ev.get("proposed_snowflake_definition"):
            ev["proposed_snowflake_definition"] = propose_snowflake_sql(
                ev.get("metric_name", ""),
                ev.get("bi_definition", ""),
                mock=bool(ev.get("mock")),
            )
    state: Dict[str, Any] = {
        "schedules": list(data.get("schedules") or []),
        "events": events,
        _LOADED_IDS_KEY: {str(e.get("id")) for e in events if isinstance(e, dict) and e.get("id")},
    }
    if isinstance(data.get("baselines"), dict):
        state["baselines"] = data["baselines"]
    return state


def _save_state(data: Dict[str, Any]) -> None:
    events = list(data.get("events") or [])
    in_db = _db_save_events(events, data.get(_LOADED_IDS_KEY))
    file_data: Dict[str, Any] = {
        k: v for k, v in data.items() if k not in (_LOADED_IDS_KEY, "events")
    }
    if in_db:
        file_data[_IMPORTED_MARKER] = True
        data[_LOADED_IDS_KEY] = {str(e.get("id")) for e in events if isinstance(e, dict) and e.get("id")}
    else:
        file_data["events"] = events
    _write_json_file(file_data)


def _update_event_row(ev: Dict[str, Any]) -> bool:
    """Write one event's new state (resolve) without touching the others."""
    try:
        from semabridge.repository.orm.models import DriftEventRow

        _ensure_drift_table()
        with _drift_session() as session:
            row = session.get(DriftEventRow, str(ev.get("id")))
            if row is None:
                return False
            row.status = str(ev.get("status") or "") or None
            row.payload_json = json.dumps(ev, default=str)
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drift event %s could not be updated in the database: %s", ev.get("id"), exc)
        return False


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


def _fabric_live_drift_enabled(project_id: Optional[str]) -> bool:
    from semabridge.sources.fabric.live_definition import fabric_live_drift_enabled

    return fabric_live_drift_enabled(project_id)


def _live_view_name(view: Optional[str]) -> str:
    from semabridge.sources.fabric.live_definition import _view_name

    return _view_name(view)


def drift_event_key(ev: Dict[str, Any]) -> tuple:
    """Dedup key of a drift event: (project, model-or-view, metric)."""
    pid = str(ev.get("project_id") or "").lower().removeprefix("proj-").strip()
    scope = str(ev.get("model_id") or "").strip().casefold() or _view_key(ev.get("semantic_view"))
    if ev.get("source") == "fabric_live":
        # Live Fabric drift can report several kinds of change for one metric
        # (expression, description, synonyms): each is its own item.
        return (pid, scope, str(ev.get("metric_name") or "").lower().strip(), str(ev.get("drift_type") or ""))
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
    if project_id and not target_domains:
        # Never widen to other projects' domains: no events, and say why.
        warning = (
            f"Drift check: project '{project_id}' has no matching ontology domain"
            + (f" '{domain}'" if domain else "")
            + "; nothing was checked. Sync the project or attach a domain first."
        )
        logger.warning(warning)
        return {
            "checked_metrics": 0,
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "warnings": [warning],
        }

    # Snowflake metrics per semantic view: {VIEW_TAIL: {METRIC: entry}} -- each
    # domain is compared against ITS OWN view only (multi-model projects have
    # one view per model, and same-named metrics differ between them).
    sf_metrics_by_view: Dict[str, Dict[str, Dict[str, str]]] = {}
    # Raw DESCRIBE rows per view (Fabric live drift also reads COMMENT / SYNONYMS).
    sf_rows_by_view: Dict[str, List[Dict[str, Any]]] = {}
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
                                sf_rows_by_view.setdefault(_live_view_name(sv), []).append(rec)
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

    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

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

    live_warnings: List[str] = []
    live_mode = bool(project_id and _fabric_live_drift_enabled(project_id))
    if live_mode:
        # Fabric + features.fabric_live_drift: the live Fabric definition vs the
        # last synced snapshot vs the deployed view replaces the stored-ontology
        # comparison above for this project. PBIX keeps today's comparison.
        from semabridge.sources.fabric.live_definition import live_fabric_drift

        live = live_fabric_drift(project_id, target_domains, sf_rows_by_view if sf_connected else None)
        new_events, live_warnings = live.events, live.warnings
        checked = sum(len(getattr(d, "metrics", []) or []) for d in target_domains)

    from semabridge.core.demo_mode import demo_mode_globally_armed
    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

    # When mock drift is enabled and no real Snowflake inspection is active, generate a realistic drift event
    if not live_mode and not sf_connected and is_mock_drift_enabled() and target_domains:
        state_curr = _load_state()
        has_pending = any(
            e.get("status") == "pending" and (not project_id or e.get("project_id") == project_id)
            for e in state_curr.get("events", [])
        )
        if not has_pending:
            dom = target_domains[0]
            m = dom.metrics[0] if getattr(dom, "metrics", None) else None
            m_name = (m.name if m else None) or "TOTAL_UNITS"
            bi_raw = (m.sql_expression or m.dax_expression if m else None) or "SUM(SALESFACT.UNITS)"
            if "UNIT" in m_name.upper():
                bi_updated = "SUM(CASE WHEN SALESFACT.STATUS NOT IN ('Cancelled', 'Returned') THEN SALESFACT.UNITS ELSE 0 END)"
            elif "DOL" in m_name.upper() or "REVENUE" in m_name.upper() or "SALES" in m_name.upper():
                bi_updated = "SUM(SALESFACT.REVENUE) - SUM(COALESCE(SALESFACT.DISCOUNT, 0))"
            elif "PRODUCT" in m_name.upper():
                bi_updated = "COUNT(DISTINCT CASE WHEN PRODUCT.IS_ACTIVE = TRUE THEN PRODUCT.PRODUCT_ID END)"
            else:
                bi_updated = f"SUM(CASE WHEN STATUS != 'Cancelled' THEN ({bi_raw}) ELSE 0 END)"
            sf_expr = f"{bi_raw}"
            sv = getattr(dom, "semantic_view_name", "SEMANTIC_VIEWS.COMP_ANALYSIS") or "SEMANTIC_VIEWS.COMP_ANALYSIS"
            ev = {
                "id": f"drift-{uuid.uuid4().hex[:10]}",
                "project_id": project_id or getattr(dom, "source_project_id", None) or "proj-comp",
                "domain": dom.domain,
                "domain_id": dom.id,
                "model_id": getattr(dom, "source_model_id", None),
                "semantic_view": sv,
                "metric_name": m_name,
                "drift_type": "EXPRESSION_MISMATCH",
                "bi_definition": bi_updated,
                "snowflake_definition": sf_expr,
                "proposed_snowflake_definition": propose_snowflake_sql(m_name, bi_updated, mock=True),
                "affected_objects": [str(sv), f"METRICS.{m_name}"],
                "status": "pending",
                "resolution_reason": "",
                "detected_at": now,
                "mock": True,
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
        **({"mode": "fabric_live", "warnings": live_warnings} if live_mode else {}),
    }


def list_drift_events(project_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    from semabridge.core.demo_mode import demo_mode_globally_armed
    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

    events = _load_state()["events"]
    # With demo flag off, every path is real (no mock rows, no demo events)
    if not (demo_mode_globally_armed() or is_mock_drift_enabled()):
        events = [e for e in events if not e.get("mock")]
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
    if m_target.name not in proj_baselines:
        proj_baselines[m_target.name] = orig_expr
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
        "reset_expression": reset_expr or "RESTORED",
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
            # One row: a whole-list save could undo another process's edits.
            if not _update_event_row(ev):
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

