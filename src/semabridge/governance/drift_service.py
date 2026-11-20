"""Scheduled Match & Drift Detection Service with Approval Workflow (APScheduler)."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.governance.drift_service")

_SCHEDULER = None


_DAX_MARKERS = re.compile(r"\[|\b(CALCULATE|DIVIDE|SUMX|AVERAGEX|COUNTROWS|DISTINCTCOUNT|FILTER|ALL|TOTALYTD|SAMEPERIODLASTYEAR|RELATED|VALUES|SELECTEDVALUE)\s*\(", re.I)


def propose_snowflake_sql(metric_name: str, bi_definition: str, *, mock: bool = False) -> str:
    """Proposed Snowflake SQL for a drifted metric, from its BI definition.

    Demo (mock) events keep their curated SQL. Real events go through
    the same DAX translator the sync uses. Returns "" when no faithful translation exists."""
    if mock:
        from semabridge.demo.drift_fixtures import derive_semabridge_sql
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


def _pkey(pid: Optional[str]) -> str:
    """Normalize a project ID for dictionary lookup (lower, strip, remove prefix 'proj-')."""
    return str(pid or "").lower().removeprefix("proj-").strip()


def get_project_drift_target_info(project_id: Optional[str]) -> Dict[str, Any]:
    """Return the view(s) actually checked and the last check time for project_id."""
    if not project_id:
        return {"project_id": "", "views_checked": [], "checked_at": None}

    state = _load_state()
    p_key = _pkey(project_id)
    last_check = (state.get("last_checks") or {}).get(p_key) or {}

    views_checked = list(last_check.get("views_checked") or [])
    checked_at = last_check.get("checked_at")

    if not views_checked and "baselines" in state and p_key in state["baselines"]:
        base_views = [
            b.get("view_name") or b.get("semantic_view")
            for b in state["baselines"][p_key].values()
            if b.get("view_name") or b.get("semantic_view")
        ]
        if base_views:
            views_checked = sorted(set(base_views))
            checked_at = checked_at or max(
                (b.get("deploy_time") or b.get("captured_at")
                 for b in state["baselines"][p_key].values()
                 if b.get("deploy_time") or b.get("captured_at")),
                default=None,
            )

    if not views_checked:
        try:
            from semabridge.ontology.store import ontology_store
            doms = ontology_store.domains_for(project_id)
            views_checked = sorted({d.semantic_view_name for d in doms if getattr(d, "semantic_view_name", None)})
        except Exception:
            pass

    return {
        "project_id": project_id,
        "views_checked": views_checked,
        "checked_at": checked_at,
    }


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


_NULL_PLACEHOLDER_RE = re.compile(
    r"^\s*(NULL|CAST\s*\(\s*NULL\s+AS\s+[^)]+\)|NULL::[A-Za-z0-9_]+)\s*$",
    re.I,
)


def _is_null_placeholder(expr: Optional[str]) -> bool:
    if not expr:
        return False
    return bool(_NULL_PLACEHOLDER_RE.match(str(expr).strip()))


_COMMENT_RE = re.compile(r'/\*.*?\*/|--[^\r\n]*', re.DOTALL)
_TOKEN_SPEC = [
    ('STRING',   r"'(?:''|[^'])*'"),
    ('IDENT_Q',  r'"[^"]+"'),
    ('CAST_OP',  r'::'),
    ('OP',       r'!=|<>|<=|>=|==|\|\||[+\-*/%=<>]'),
    ('PUNCT',    r'[(),:;]'),
    ('DOT',      r'\.'),
    ('NUM',      r'\b\d+(?:\.\d+)?\b'),
    ('WORD',     r'\b[A-Za-z_][A-Za-z0-9_]*\b'),
    ('WS',       r'\s+'),
    ('MISC',     r'.'),
]
_TOKEN_RE = re.compile('|'.join(f'(?P<{name}>{pattern})' for name, pattern in _TOKEN_SPEC))


def _normalize_metric_expr(expr: str) -> str:
    """Normalize expressions via tokenization to compare semantic intent across BI and Snowflake dialects.

    Rules:
    - Comments stripped.
    - Whitespace normalized (including inside parentheses).
    - Keywords / identifiers outside string literals uppercased.
    - Quotes around plain identifiers stripped ('"T"."X"' -> 'T.X').
    - String literals kept verbatim with exact case preserved.
    - Never strips casts (::FLOAT vs ::INT detected).
    - Never strips table qualifiers or prefixes.
    - Never strips CAST target types or literal case.
    """
    if not expr:
        return ""
    cleaned = _COMMENT_RE.sub(" ", expr)
    tokens = []
    for m in _TOKEN_RE.finditer(cleaned):
        kind = m.lastgroup
        val = m.group()
        if kind == "WS":
            continue
        if kind == "STRING":
            tokens.append(("STRING", val))
        elif kind == "IDENT_Q":
            inner = val[1:-1]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", inner):
                tokens.append(("WORD", inner.upper()))
            else:
                tokens.append(("IDENT_Q", f'"{inner.upper()}"'))
        elif kind == "WORD":
            tokens.append(("WORD", val.upper()))
        elif kind == "CAST_OP":
            tokens.append(("CAST_OP", "::"))
        elif kind == "DOT":
            tokens.append(("DOT", "."))
        elif kind == "OP":
            tokens.append(("OP", val))
        elif kind == "PUNCT":
            tokens.append(("PUNCT", val))
        elif kind == "NUM":
            tokens.append(("NUM", val))
        else:
            tokens.append(("MISC", val))

    out = []
    for i, (k, v) in enumerate(tokens):
        prev_k, prev_v = tokens[i - 1] if i > 0 else (None, None)
        need_space = False
        if prev_k is not None:
            if k == "DOT" or prev_k == "DOT":
                need_space = False
            elif k == "CAST_OP" or prev_k == "CAST_OP":
                need_space = False
            elif k == "PUNCT" and v == "(":
                need_space = (prev_k == "WORD" and prev_v in ("AND", "OR", "NOT", "CASE", "WHEN", "THEN", "ELSE", "IN")) or (prev_k in ("NUM", "STRING", "IDENT_Q"))
            elif prev_k == "PUNCT" and prev_v == "(":
                need_space = False
            elif k == "PUNCT" and v == ")":
                need_space = False
            elif prev_k == "PUNCT" and prev_v == ",":
                need_space = True
            elif k == "PUNCT" and v in (",", ";"):
                need_space = False
            elif k == "OP" or prev_k == "OP":
                need_space = True
            else:
                need_space = True
        if need_space and out:
            out.append(" ")
        out.append(v)
    return "".join(out).strip()


def _view_key(view: Optional[str]) -> str:
    """Comparable key of a semantic view name (last identifier, case-folded)."""
    return str(view or "").strip().split(".")[-1].strip('"').casefold()


def record_deploy_baseline(
    project_id: Optional[str],
    view_name: str,
    metrics: Dict[str, str],
    source_definitions: Optional[Dict[str, str]] = None,
    deployed_at: Optional[str] = None,
    snapshot_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Store the exact post-deploy Snowflake DESCRIBE metrics as the baseline."""
    now_iso = deployed_at or datetime.now(timezone.utc).isoformat()
    p_clean = str(project_id or "").lower().removeprefix("proj-").strip()
    v_key = _view_key(view_name)

    baseline_entry = {
        "project_id": project_id,
        "view_name": view_name,
        "semantic_view": view_name,
        "deployed_at": now_iso,
        "snapshot_id": snapshot_id,
        "metrics": {str(k).upper(): str(v).strip() for k, v in (metrics or {}).items()},
        "source_definitions": {str(k).upper(): str(v).strip() for k, v in (source_definitions or {}).items()} if source_definitions else {},
    }

    state = _load_state()
    baselines = state.setdefault("baselines", {})
    if p_clean:
        proj_bases = baselines.setdefault(p_clean, {})
        proj_bases[v_key] = baseline_entry
    else:
        baselines[v_key] = baseline_entry
    _save_state(state)
    logger.info("Recorded deploy baseline for view %s (project=%s, %d metrics)", view_name, project_id, len(baseline_entry["metrics"]))
    return baseline_entry


def get_deploy_baseline(project_id: Optional[str], view_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Retrieve the deploy baseline for a view from state or snapshot history."""
    if not view_name:
        return None
    v_key = _view_key(view_name)
    p_clean = str(project_id or "").lower().removeprefix("proj-").strip()

    state = _load_state()
    baselines = state.get("baselines", {})
    if p_clean and p_clean in baselines and isinstance(baselines[p_clean], dict):
        if v_key in baselines[p_clean]:
            return baselines[p_clean][v_key]

    # Search across all project baselines for matching view
    for proj_k, p_bases in baselines.items():
        if isinstance(p_bases, dict) and v_key in p_bases:
            return p_bases[v_key]
        elif proj_k == v_key and isinstance(p_bases, dict) and "metrics" in p_bases:
            return p_bases

    return None


def migrate_superseded_drift_events(events: List[Dict[str, Any]]) -> int:
    """Mark legacy drift events created under the old pre-baseline comparison as superseded."""
    superseded_count = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for ev in events:
        if ev.get("status") == "pending" and not ev.get("baseline_compared") and not ev.get("mock"):
            ev["status"] = "superseded"
            ev["resolution_reason"] = "Superseded by deploy baseline drift comparison"
            ev["superseded_at"] = now_iso
            superseded_count += 1
    return superseded_count


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
    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

    fed = ontology_store.get_federated_ontology()

    candidates = ontology_store.domains_for(project_id) if project_id else list(fed.domains.values())
    target_domains = [
        d for d in candidates
        if not domain or d.domain.lower() == domain.lower() or (d.id or "").lower() == domain.lower()
    ]

    now = datetime.now(timezone.utc).isoformat()

    if project_id and not target_domains:
        return {
            "status": "NOT_CHECKED",
            "not_checked_reason": "NO_ONTOLOGY",
            "checked_metrics": 0,
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [],
        }

    sf_metrics_by_view: Dict[str, Dict[str, Dict[str, str]]] = {}
    inspected_views: set = set()
    failed_views: Dict[str, str] = {}
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
                    sf_connected = True
                    for dom in target_domains:
                        sv = getattr(dom, "semantic_view_name", None)
                        if not sv:
                            continue
                        v_key = _view_key(sv)
                        if v_key in inspected_views or v_key in failed_views:
                            continue
                        sf_metrics = sf_metrics_by_view.setdefault(v_key, {})
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
                                    if "expression" in rec and rec["expression"]:
                                        entry["expression"] = str(rec["expression"]).strip()
                                    elif prop == "EXPRESSION":
                                        entry["expression"] = val
                            inspected_views.add(v_key)
                        except Exception as exc:
                            logger.info("Drift check could not describe %s: %s", sv, exc)
                            failed_views[v_key] = str(exc)
                finally:
                    conn.close()
    except Exception as exc:
        logger.info("Drift check Snowflake inspection skipped: %s", exc)
        sf_connected = False

    state_curr = _load_state()
    existing_event_map = {drift_event_key(e): e for e in state_curr.get("events") or []}

    # If Snowflake connection failed
    if not sf_connected:
        mock_events = []
        if is_mock_drift_enabled() and target_domains:
            has_pending = any(
                e.get("status") == "pending" and (not project_id or e.get("project_id") == project_id)
                for e in state_curr.get("events", [])
            )
            if not has_pending:
                for dom in target_domains:
                    for m in (getattr(dom, "metrics", None) or []):
                        m_name = m.name
                        bi_raw = m.sql_expression or m.dax_expression or "COUNT(*)"
                        sv = getattr(dom, "semantic_view_name", None) or f"SEMANTIC_VIEWS.{dom.domain}"
                        ev = {
                            "id": f"drift-{uuid.uuid4().hex[:10]}",
                            "project_id": project_id or getattr(dom, "source_project_id", None) or "",
                            "domain": dom.domain,
                            "domain_id": getattr(dom, "id", None) or dom.domain,
                            "model_id": getattr(dom, "source_model_id", None),
                            "semantic_view": getattr(dom, "semantic_view_name", None),
                            "metric_name": m_name,
                            "drift_type": "EXPRESSION_MISMATCH",
                            "bi_definition": bi_raw,
                            "snowflake_definition": f"{bi_raw} * 2",
                            "proposed_snowflake_definition": propose_snowflake_sql(m_name, bi_raw, mock=True) or bi_raw,
                            "mock": True,
                            "affected_objects": [sv, f"METRICS.{m_name}"],
                            "status": "pending",
                            "resolution_reason": "",
                            "detected_at": now,
                        }
                        mock_events.append(ev)
                        break
                    if mock_events:
                        break
                if mock_events:
                    state_curr["events"] = mock_events + state_curr.get("events", [])
                    _save_state(state_curr)
            else:
                mock_events = [
                    e for e in state_curr.get("events", [])
                    if e.get("status") == "pending" and (not project_id or e.get("project_id") == project_id)
                ]

        status = "DRIFT_DETECTED" if mock_events else "NOT_CHECKED"
        reason = None if mock_events else "SNOWFLAKE_UNAVAILABLE"
        return {
            "status": status,
            "not_checked_reason": reason,
            "checked_metrics": len(mock_events),
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": len(mock_events),
            "events": mock_events,
            "checked_at": now,
            "views_checked": [],
        }

    sim = BlastRadiusSimulator()
    new_events: List[Dict[str, Any]] = []
    checked = 0
    not_checked: List[Dict[str, Any]] = []
    skipped_placeholders: List[str] = []

    has_any_comparable_metrics = any(bool(getattr(d, "metrics", None)) for d in target_domains)
    if not has_any_comparable_metrics:
        return {
            "status": "NOT_CHECKED",
            "not_checked_reason": "NO_COMPARABLE_METRICS",
            "checked_metrics": 0,
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": sf_connected,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
        }

    matched_sf_keys_by_view: Dict[str, Set[str]] = {}
    view_primary_domain: Dict[str, Any] = {}

    # Migrate any legacy pre-baseline pending events to superseded
    if migrate_superseded_drift_events(state_curr.get("events") or []) > 0:
        _save_state(state_curr)

    # Check for views that have no baseline yet (Requirement 5)
    has_baseline_captured = False
    views_newly_captured: Set[str] = set()
    for dom in target_domains:
        dom_view = getattr(dom, "semantic_view_name", None)
        if not dom_view:
            continue
        v_key = _view_key(dom_view)
        if v_key in inspected_views and v_key not in failed_views:
            base = get_deploy_baseline(project_id or getattr(dom, "source_project_id", None), dom_view)
            if not base and sf_metrics_by_view.get(v_key):
                src_defs = {
                    m.name.upper(): (getattr(m, "dax_expression", "") or getattr(m, "sql_expression", "") or "").strip()
                    for m in (getattr(dom, "metrics", []) or [])
                    if getattr(m, "name", None)
                }
                record_deploy_baseline(
                    project_id=project_id or getattr(dom, "source_project_id", None),
                    view_name=dom_view,
                    metrics={k: v.get("expression", "") for k, v in sf_metrics_by_view[v_key].items()},
                    source_definitions=src_defs,
                    deployed_at=now,
                )
                has_baseline_captured = True
                views_newly_captured.add(v_key)

    if has_baseline_captured and not any(get_deploy_baseline(project_id, getattr(d, "semantic_view_name", None)) for d in target_domains if _view_key(getattr(d, "semantic_view_name", None)) not in views_newly_captured):
        # First check stores the baseline and returns status BASELINE_CAPTURED with no drift events
        total_captured = sum(len(sf_metrics_by_view.get(vk, {})) for vk in views_newly_captured)
        return {
            "status": "BASELINE_CAPTURED",
            "not_checked_reason": None,
            "checked_metrics": total_captured,
            "not_checked": not_checked,
            "skipped_placeholders": [],
            "snowflake_connected": sf_connected,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
        }

    for dom in target_domains:
        dom_view = getattr(dom, "semantic_view_name", None)
        dom_metrics = getattr(dom, "metrics", []) or []

        if not dom_view:
            not_checked.append({
                "domain": dom.domain,
                "view": None,
                "reason": "NO_DEPLOYED_VIEW",
            })
            continue

        v_key = _view_key(dom_view)
        if v_key not in view_primary_domain:
            view_primary_domain[v_key] = dom

        if v_key in failed_views:
            not_checked.append({
                "domain": dom.domain,
                "view": dom_view,
                "reason": failed_views[v_key],
            })
            continue

        if v_key not in inspected_views:
            not_checked.append({
                "domain": dom.domain,
                "view": dom_view,
                "reason": "VIEW_NOT_INSPECTED",
            })
            continue

        view_metrics = sf_metrics_by_view.get(v_key, {})
        view_matched = matched_sf_keys_by_view.setdefault(v_key, set())

        base = get_deploy_baseline(project_id or getattr(dom, "source_project_id", None), dom_view)
        base_metrics = dict(base.get("metrics", {})) if base else {}
        base_src_defs = dict(base.get("source_definitions", {})) if base else {}

        # Fallback to domain metrics if no baseline metrics were saved
        if not base_metrics and dom_metrics:
            base_metrics = {
                m.name.upper(): (m.sql_expression or getattr(m, "dax_expression", "") or "").strip()
                for m in dom_metrics
                if (m.sql_expression or getattr(m, "dax_expression", ""))
            }

        for base_name, base_expr in base_metrics.items():
            checked += 1
            m_names_to_try = [
                base_name.upper(),
                re.sub(r"\s+", "_", base_name).strip().upper(),
                re.sub(r"[\s_]+", "_", base_name).strip("_").upper(),
            ]

            sf_key = None
            sf_entry = None
            for cand in m_names_to_try:
                if cand in view_metrics:
                    sf_key = cand
                    sf_entry = view_metrics[cand]
                    break

            if sf_key:
                view_matched.add(sf_key)

            drift_reason = None
            sf_expr = (sf_entry.get("expression") or "").strip() if sf_entry else ""

            if not sf_entry:
                if base_expr:
                    drift_reason = "MISSING_IN_SNOWFLAKE"
            else:
                if _is_null_placeholder(sf_expr):
                    if _is_null_placeholder(base_expr) or not base_expr:
                        skipped_placeholders.append(base_name)
                    else:
                        drift_reason = "NULL_IN_SNOWFLAKE"
                else:
                    if base_expr and _normalize_metric_expr(sf_expr) != _normalize_metric_expr(base_expr):
                        drift_reason = "EXPRESSION_MISMATCH"

            if drift_reason:
                m_proj = project_id or getattr(dom, "source_project_id", None)
                m_model = getattr(dom, "source_model_id", None)
                key = (
                    str(m_proj or "").lower().removeprefix("proj-").strip(),
                    str(m_model or "").strip().casefold() or _view_key(dom_view),
                    base_name.lower().strip(),
                )
                prev = existing_event_map.get(key)
                if prev and prev.get("bi_definition") == base_expr and prev.get("status") in ("approved", "ignored"):
                    # Already accepted by reviewer
                    continue

                affected = [dom_view or "SEMANTIC_VIEW", base_name]
                try:
                    impact = sim.simulate(
                        target_object=base_name,
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
                    "project_id": m_proj,
                    "domain": dom.domain,
                    "domain_id": getattr(dom, "id", None) or dom.domain,
                    "model_id": m_model,
                    "semantic_view": dom_view,
                    "metric_name": base_name,
                    "drift_type": drift_reason,
                    "bi_definition": base_expr or "(not present in semantic view)",
                    "snowflake_definition": sf_expr or "(not present in semantic view)",
                    "proposed_snowflake_definition": "redeploy from source",
                    "baseline_compared": True,
                    "affected_objects": affected,
                    "status": "pending",
                    "resolution_reason": "",
                    "detected_at": now,
                }
                new_events.append(ev)

        # Check for Power BI / source definition drift (Requirement 3)
        for m in dom_metrics:
            m_up = m.name.upper()
            curr_src = (getattr(m, "dax_expression", "") or getattr(m, "sql_expression", "") or "").strip()
            deploy_src = base_src_defs.get(m_up)
            if curr_src and deploy_src and _normalize_metric_expr(curr_src) != _normalize_metric_expr(deploy_src):
                m_proj = project_id or getattr(dom, "source_project_id", None)
                m_model = getattr(dom, "source_model_id", None)
                src_key = (
                    str(m_proj or "").lower().removeprefix("proj-").strip(),
                    str(m_model or "").strip().casefold() or _view_key(dom_view),
                    f"{m.name.lower().strip()}__src",
                )
                prev_src = existing_event_map.get(src_key)
                if prev_src and prev_src.get("bi_definition") == curr_src and prev_src.get("status") in ("approved", "ignored"):
                    continue
                ev = {
                    "id": f"drift-{uuid.uuid4().hex[:10]}",
                    "project_id": m_proj,
                    "domain": dom.domain,
                    "domain_id": getattr(dom, "id", None) or dom.domain,
                    "model_id": m_model,
                    "semantic_view": dom_view,
                    "metric_name": m.name,
                    "drift_type": "SOURCE_CHANGED",
                    "bi_definition": curr_src,
                    "snowflake_definition": deploy_src,
                    "proposed_snowflake_definition": "redeploy from source",
                    "baseline_compared": True,
                    "affected_objects": [dom_view or "SEMANTIC_VIEW", m.name],
                    "status": "pending",
                    "resolution_reason": "",
                    "detected_at": now,
                }
                new_events.append(ev)

    # Check for unmanaged metrics in Snowflake views (only metrics not in baseline)
    if has_any_comparable_metrics:
        for v_key, view_metrics in sf_metrics_by_view.items():
            if v_key in inspected_views and v_key in view_primary_domain:
                dom = view_primary_domain[v_key]
                dom_view = getattr(dom, "semantic_view_name", None)
                base = get_deploy_baseline(project_id or getattr(dom, "source_project_id", None), dom_view)
                base_metrics = base.get("metrics", {}) if base else {}
                if not base_metrics and getattr(dom, "metrics", None):
                    base_metrics = {m.name.upper(): (m.sql_expression or getattr(m, "dax_expression", "") or "").strip() for m in dom.metrics}
                base_keys = {k.upper() for k in base_metrics}
                view_matched = matched_sf_keys_by_view.get(v_key, set())
                for sf_k, sf_e in view_metrics.items():
                    if sf_k not in view_matched and sf_k.upper() not in base_keys:
                        unmanaged_name = sf_e.get("name") or sf_k
                        unmanaged_expr = (sf_e.get("expression") or "").strip()
                        m_proj = project_id or getattr(dom, "source_project_id", None)
                        m_model = getattr(dom, "source_model_id", None)
                        u_key = (
                            str(m_proj or "").lower().removeprefix("proj-").strip(),
                            str(m_model or "").strip().casefold() or _view_key(dom_view),
                            unmanaged_name.lower().strip(),
                        )
                        prev_u = existing_event_map.get(u_key)
                        if prev_u and prev_u.get("status") in ("approved", "ignored"):
                            continue
                        ev = {
                            "id": f"drift-{uuid.uuid4().hex[:10]}",
                            "project_id": m_proj,
                            "domain": dom.domain,
                            "domain_id": getattr(dom, "id", None) or dom.domain,
                            "model_id": m_model,
                            "semantic_view": dom_view,
                            "metric_name": unmanaged_name,
                            "drift_type": "UNMANAGED_IN_SNOWFLAKE",
                            "bi_definition": "(not present in deploy baseline)",
                            "snowflake_definition": unmanaged_expr or "(unmanaged in Snowflake)",
                            "proposed_snowflake_definition": "redeploy from source",
                            "baseline_compared": True,
                            "affected_objects": [dom_view, unmanaged_name],
                            "status": "pending",
                            "resolution_reason": "",
                            "detected_at": now,
                        }
                        new_events.append(ev)

    state = _load_state()
    event_map = {}
    for e in state.get("events") or []:
        event_map[drift_event_key(e)] = e
    for ev in new_events:
        key = drift_event_key(ev)
        prev = event_map.get(key)
        if prev and prev.get("bi_definition") == ev.get("bi_definition") and prev.get("status") in ("approved", "ignored"):
            continue
        event_map[key] = ev

    # Per-project cap (keep up to 200 per project)
    events_by_proj: Dict[str, List[Dict[str, Any]]] = {}
    for ev in event_map.values():
        p_key = str(ev.get("project_id") or "").lower().removeprefix("proj-").strip()
        events_by_proj.setdefault(p_key, []).append(ev)

    capped_events = []
    for p_key, p_evs in events_by_proj.items():
        capped_events.extend(p_evs[-200:])

    state["events"] = capped_events
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

    detected_count = len(new_events)

    if not target_domains:
        status = "NOT_CHECKED"
        not_checked_reason = "NO_ONTOLOGY"
    elif not sf_connected:
        status = "NOT_CHECKED"
        not_checked_reason = "SNOWFLAKE_UNAVAILABLE"
    elif not has_any_comparable_metrics:
        status = "NOT_CHECKED"
        not_checked_reason = "NO_COMPARABLE_METRICS"
    elif all(not getattr(d, "semantic_view_name", None) for d in target_domains):
        status = "NOT_CHECKED"
        not_checked_reason = "NO_DEPLOYED_VIEW"
    elif checked == 0:
        status = "NOT_CHECKED"
        not_checked_reason = "NO_COMPARABLE_METRICS"
    elif not_checked:
        status = "PARTIAL"
        not_checked_reason = None
    elif detected_count > 0:
        status = "DRIFT_DETECTED"
        not_checked_reason = None
    else:
        status = "IN_SYNC"
        not_checked_reason = None

    checked_views = sorted(inspected_views) if inspected_views else sorted([getattr(d, "semantic_view_name") for d in target_domains if getattr(d, "semantic_view_name", None)])
    if project_id:
        p_key = _pkey(project_id)
        state_curr = _load_state()
        state_curr.setdefault("last_checks", {})[p_key] = {
            "checked_at": now,
            "views_checked": checked_views,
            "status": status,
        }
        _save_state(state_curr)

    return {
        "status": status,
        "not_checked_reason": not_checked_reason,
        "checked_metrics": checked,
        "not_checked": not_checked,
        "skipped_placeholders": skipped_placeholders,
        "snowflake_connected": sf_connected,
        "drift_detected": detected_count,
        "events": new_events,
        "checked_at": now,
        "views_checked": checked_views,
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


def resolve_drift_event(event_id: str, action: str, reason: str = "") -> Optional[Dict[str, Any]]:
    state = _load_state()
    for ev in state.get("events") or []:
        if ev["id"] == event_id:
            if action == "approve":
                ev["status"] = "approved"
                ev["proposed_action"] = "redeploy"
                ev["proposed_snowflake_definition"] = "redeploy from source"
                ev["resolution_reason"] = reason or "Approved for gated redeploy from source"
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


def __getattr__(name: str) -> Any:
    """Backward compatibility forwarder for test helpers moved to semabridge.demo.drift_fixtures."""
    if name in (
        "get_default_mock_drift_events",
        "seed_mock_drift_events",
        "cleanup_mock_drift_events",
        "get_configured_demo_project_id",
        "set_configured_demo_project_id",
        "derive_semabridge_sql",
        "simulate_metric_drift",
        "reset_simulated_metric_drift",
    ):
        import semabridge.demo.drift_fixtures as _fixtures
        return getattr(_fixtures, name)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


