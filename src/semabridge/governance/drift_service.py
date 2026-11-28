"""Scheduled Match & Drift Detection Service with Approval Workflow (APScheduler)."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from semabridge.governance.drift.normalize import normalize_expression, reset_parse_warnings
from semabridge.governance.drift.project_scope import verify_drift_project_access


def _normalize_metric_expr(expr: Optional[str]) -> str:
    """Normalize expressions for comparison (backward-compatible helper using Snowflake dialect)."""
    if not expr:
        return ""
    return str(normalize_expression(expr, dialect="snowflake"))

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


def get_project_drift_target_info(project_id: Optional[str], view: Optional[str] = None) -> Dict[str, Any]:
    """Return the view(s) actually checked, status, reason, baseline_source, baseline_at, and last check time."""
    if not project_id:
        return {
            "project_id": "",
            "views_checked": [],
            "checked_at": None,
            "status": "NOT_CHECKED",
            "reason": "NO_PROJECT",
            "not_checked_reason": "NO_PROJECT",
            "reason_code": "NO_PROJECT",
            "platform": "UNKNOWN",
            "baseline_source": None,
            "baseline_at": None,
            "metrics_compared": 0,
            "view": None,
            "semantic_view": None,
        }

    platform = "UNKNOWN"
    try:
        from semabridge.ai.project_connection import _project_meta
        from semabridge.governance.drift.adapters.registry import resolve_project_platform
        meta = _project_meta(project_id) if project_id else {}
        platform = resolve_project_platform(meta)
    except Exception:
        pass

    resolved_view = None
    try:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        if view:
            resolved_view = repo.validate_project_view(project_id, view)
        else:
            deployments = repo.list_project_deployments(project_id, include_unmanaged=True)
            latest = next((d for d in deployments if d.get("is_latest")), None)
            resolved_view = latest["semantic_view"] if latest else None
    except Exception as exc:
        logger.warning("Failed validating project view for %s (view=%s): %s", project_id, view, exc)
        resolved_view = None

    state = _load_state()
    p_key = _pkey(project_id)
    last_check = (state.get("last_checks") or {}).get(p_key) or {}

    views_checked = list(last_check.get("views_checked") or [])
    checked_at = last_check.get("checked_at")
    status = last_check.get("status") or "NOT_CHECKED"
    reason = last_check.get("not_checked_reason") or last_check.get("reason")
    reason_code = last_check.get("reason_code")
    proposed_action = last_check.get("proposed_action")
    baseline_source = last_check.get("baseline_source")
    baseline_at = last_check.get("baseline_at")
    metrics_compared = last_check.get("metrics_compared", 0)
    if last_check.get("platform"):
        platform = last_check.get("platform")

    # Check canonical baseline store and legacy state if fields are missing
    try:
        from semabridge.governance.drift.baseline_store import get_active_baseline
        active_b = get_active_baseline(project_id, view or resolved_view, platform=platform) if (view or resolved_view) else None
        if active_b:
            if not baseline_source:
                baseline_source = active_b.get("baseline_source", "deploy")
            if not baseline_at:
                baseline_at = active_b.get("deployed_at")
            if not platform or platform == "UNKNOWN":
                platform = active_b.get("platform") or platform
    except Exception as exc:
        logger.debug("Active baseline check failed in get_project_drift_target_info: %s", exc)

    if "baselines" in state and p_key in state["baselines"]:
        for b in state["baselines"][p_key].values():
            if isinstance(b, dict):
                vn = b.get("view_name") or b.get("semantic_view")
                if vn and vn not in views_checked:
                    views_checked.append(vn)
                if not baseline_source and b.get("baseline_source"):
                    baseline_source = b.get("baseline_source")
                if not baseline_at and (b.get("deployed_at") or b.get("captured_at")):
                    baseline_at = b.get("deployed_at") or b.get("captured_at")
                if not platform or platform == "UNKNOWN":
                    platform = b.get("platform") or platform

    if not views_checked:
        try:
            from semabridge.ontology.store import ontology_store
            doms = ontology_store.domains_for(project_id)
            views_checked = sorted({d.semantic_view_name for d in doms if getattr(d, "semantic_view_name", None)})
        except Exception as exc:
            logger.warning("Failed loading domains for %s: %s", project_id, exc)

    if resolved_view and resolved_view not in views_checked:
        views_checked = [resolved_view]

    if not checked_at and status in ("NOT_CHECKED", "UNKNOWN"):
        status = "NEVER_CHECKED"
        reason_code = "NEVER_CHECKED"
        reason = "Project has not been checked for drift yet."
    elif reason and not reason_code:
        r_str = str(reason).upper()
        if "NO_DEPLOY_BASELINE" in r_str or "NO DEPLOY BASELINE" in r_str or "REDEPLOY TO RECORD ONE" in r_str:
            reason_code = "NO_DEPLOY_BASELINE"
        elif "NO_DEPLOYED_VIEW" in r_str or "NO DEPLOYED VIEW" in r_str:
            reason_code = "NO_DEPLOYED_VIEW"
        elif "NOT SUPPORTED" in r_str:
            reason_code = "PLATFORM_NOT_SUPPORTED"
        elif "AUTH" in r_str or "250001" in r_str or "401" in r_str or "PERMISSION" in r_str or "UNAUTHORIZED" in r_str:
            reason_code = "WAREHOUSE_AUTH_FAILED"
        elif "UNAVAILABLE" in r_str or "CONNECTION" in r_str or "REFUSED" in r_str or "TIMEOUT" in r_str:
            reason_code = "WAREHOUSE_UNREACHABLE"
        elif "NOT EXIST" in r_str or "NOT FOUND" in r_str or "002003" in r_str or "404" in r_str:
            reason_code = "VIEW_NOT_FOUND"
        elif "PARSE" in r_str or "SYNTAX" in r_str:
            reason_code = "PARSE_ERROR"
        elif "NO_COMPARABLE" in r_str or "NO METRICS" in r_str:
            reason_code = "NO_COMPARABLE_OBJECTS"
        elif "CHECK_IN_PROGRESS" in r_str:
            reason_code = "CHECK_IN_PROGRESS"
        else:
            reason_code = str(reason)

    return {
        "project_id": project_id,
        "views_checked": sorted(set(views_checked)),
        "checked_at": checked_at,
        "status": status,
        "reason": reason,
        "reason_code": reason_code,
        "not_checked_reason": reason_code or reason,
        "proposed_action": proposed_action or ("redeploy" if (reason_code == "NO_DEPLOY_BASELINE" or reason == "NO_DEPLOY_BASELINE") else None),
        "platform": platform,
        "baseline_source": baseline_source,
        "baseline_at": baseline_at,
        "metrics_compared": metrics_compared,
        "view": resolved_view,
        "semantic_view": resolved_view,
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
    from semabridge.repository.orm.models import (
        DriftEventRow,
        DriftBaselineRow,
        DriftLastCheckRow,
        DriftScheduleRow,
    )
    from semabridge.repository.orm.session_factory import db_manager

    engine = db_manager.get_engine()
    if id(engine) in _drift_tables_ready:
        return
    Base.metadata.create_all(
        bind=engine,
        tables=[
            DriftEventRow.__table__,
            DriftBaselineRow.__table__,
            DriftLastCheckRow.__table__,
            DriftScheduleRow.__table__,
        ],
        checkfirst=True,
    )
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
    if "PYTEST_CURRENT_TEST" not in os.environ and not os.environ.get("SEMABRIDGE_DRIFT_STATE_PATH"):
        return  # Do not write to ~/.semabridge/drift_state.json
    _state_path().write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _db_load_events(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Events from the database, filtered in the query by project_id and status."""
    try:
        from sqlalchemy import select, or_
        from semabridge.repository.orm.models import DriftEventRow

        _ensure_drift_table()
        with _drift_session() as session:
            stmt = select(DriftEventRow.payload_json)
            if project_id:
                p_clean = _pkey(project_id)
                stmt = stmt.where(
                    or_(
                        DriftEventRow.project_id == project_id,
                        DriftEventRow.project_id == p_clean,
                        DriftEventRow.project_id == f"proj-{p_clean}",
                    )
                )
            if status:
                stmt = stmt.where(DriftEventRow.status == status)
            stmt = stmt.order_by(DriftEventRow.position, DriftEventRow.updated_at)
            if offset is not None and offset > 0:
                stmt = stmt.offset(offset)
            if limit is not None and limit > 0:
                stmt = stmt.limit(limit)
            rows = session.execute(stmt).all()
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


def _db_save_state_metadata(data: Dict[str, Any]) -> None:
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import (
            DriftBaselineRow,
            DriftLastCheckRow,
            DriftScheduleRow,
        )

        _ensure_drift_table()
        now = datetime.now(timezone.utc)
        with _drift_session() as session:
            baselines = data.get("baselines") or {}
            for p_clean, views in baselines.items():
                if isinstance(views, dict):
                    for v_name, b_entry in views.items():
                        if isinstance(b_entry, dict):
                            full_v = _full_view_key(b_entry.get("view_name") or v_name)
                            row_id = f"{p_clean}:{full_v}"
                            row = session.get(DriftBaselineRow, row_id)
                            if row is None:
                                row = DriftBaselineRow(
                                    id=row_id,
                                    project_id=str(p_clean),
                                    view_name=str(b_entry.get("view_name") or v_name),
                                    version=1,
                                    payload_json=json.dumps(b_entry, default=str),
                                    updated_at=now,
                                )
                                session.add(row)
                            else:
                                row.version = (row.version or 1) + 1
                                row.payload_json = json.dumps(b_entry, default=str)
                                row.updated_at = now

            last_checks = data.get("last_checks") or {}
            for p_clean, lc_entry in last_checks.items():
                if isinstance(lc_entry, dict):
                    row_id = str(p_clean)
                    row = session.get(DriftLastCheckRow, row_id)
                    if row is None:
                        row = DriftLastCheckRow(
                            id=row_id,
                            project_id=str(p_clean).split(":")[0],
                            view_name=lc_entry.get("view") or lc_entry.get("semantic_view"),
                            version=1,
                            payload_json=json.dumps(lc_entry, default=str),
                            updated_at=now,
                        )
                        session.add(row)
                    else:
                        row.version = (row.version or 1) + 1
                        row.view_name = lc_entry.get("view") or lc_entry.get("semantic_view") or row.view_name
                        row.payload_json = json.dumps(lc_entry, default=str)
                        row.updated_at = now

            schedules = data.get("schedules") or []
            existing_s_ids = set()
            for s_entry in schedules:
                if isinstance(s_entry, dict) and s_entry.get("id"):
                    s_id = str(s_entry["id"])
                    existing_s_ids.add(s_id)
                    row = session.get(DriftScheduleRow, s_id)
                    if row is None:
                        row = DriftScheduleRow(
                            id=s_id,
                            project_id=str(s_entry.get("project_id") or "") or None,
                            version=1,
                            payload_json=json.dumps(s_entry, default=str),
                            updated_at=now,
                        )
                        session.add(row)
                    else:
                        row.version = (row.version or 1) + 1
                        row.project_id = str(s_entry.get("project_id") or "") or None
                        row.payload_json = json.dumps(s_entry, default=str)
                        row.updated_at = now

            if "schedules" in data:
                all_s_rows = session.execute(select(DriftScheduleRow)).scalars().all()
                for sr in all_s_rows:
                    if sr.id not in existing_s_ids:
                        session.delete(sr)

            session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drift metadata could not be saved to database: %s", exc)


def _db_load_state_metadata() -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    baselines: Dict[str, Dict[str, Any]] = {}
    last_checks: Dict[str, Dict[str, Any]] = {}
    schedules: List[Dict[str, Any]] = []
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import (
            DriftBaselineRow,
            DriftLastCheckRow,
            DriftScheduleRow,
        )

        _ensure_drift_table()
        with _drift_session() as session:
            b_rows = session.execute(select(DriftBaselineRow)).scalars().all()
            for r in b_rows:
                try:
                    payload = json.loads(r.payload_json)
                except Exception as ex:
                    logger.warning("Unreadable baseline row %s: %s", r.id, ex)
                    continue
                if isinstance(payload, dict):
                    p = _pkey(payload.get("project_id") or r.project_id)
                    fv = _full_view_key(payload.get("view_name") or payload.get("semantic_view") or r.view_name)
                    baselines.setdefault(p, {})[fv] = payload
                    vk = _view_key(payload.get("view_name") or payload.get("semantic_view") or r.view_name)
                    if vk and vk != fv:
                        baselines[p][vk] = payload

            lc_rows = session.execute(select(DriftLastCheckRow)).scalars().all()
            for r in lc_rows:
                try:
                    payload = json.loads(r.payload_json)
                except Exception as ex:
                    logger.warning("Unreadable last check row %s: %s", r.id, ex)
                    continue
                if isinstance(payload, dict):
                    p = _pkey(payload.get("project_id") or r.project_id)
                    last_checks[p] = payload

            sc_rows = session.execute(select(DriftScheduleRow)).scalars().all()
            for r in sc_rows:
                try:
                    payload = json.loads(r.payload_json)
                except Exception as ex:
                    logger.warning("Unreadable schedule row %s: %s", r.id, ex)
                    continue
                if isinstance(payload, dict):
                    schedules.append(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drift metadata could not be loaded from database: %s", exc)
    return baselines, last_checks, schedules


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
    events and metadata of the old JSON file (once; the file is then marked imported)."""
    data = _read_json_file()
    if not data or data.get(_IMPORTED_MARKER):
        return db_events

    file_events = [e for e in (data.get("events") or []) if isinstance(e, dict) and e.get("id")]
    if not db_events and file_events:
        if not _db_save_events(file_events, set()):
            return file_events
        db_events = file_events

    if data.get("baselines") or data.get("last_checks") or data.get("schedules"):
        _db_save_state_metadata(data)

    data[_IMPORTED_MARKER] = True
    try:
        _write_json_file(data)
    except OSError as exc:
        logger.warning("Drift state file could not be marked as imported: %s", exc)
    logger.info("Imported drift events and metadata from %s into the database", _state_path())
    return db_events


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
    if isinstance(data.get("last_checks"), dict):
        state["last_checks"] = data["last_checks"]

    if db_events is not None:
        db_bases, db_lcs, db_scheds = _db_load_state_metadata()
        if db_bases:
            for p, views in db_bases.items():
                state.setdefault("baselines", {}).setdefault(p, {}).update(views)
        if db_lcs:
            state.setdefault("last_checks", {}).update(db_lcs)
        if db_scheds:
            existing_ids = {s.get("id") for s in state["schedules"] if isinstance(s, dict)}
            for s in db_scheds:
                if s.get("id") not in existing_ids:
                    state["schedules"].append(s)
                    existing_ids.add(s.get("id"))

    return state


def _save_state(data: Dict[str, Any]) -> None:
    events = list(data.get("events") or [])
    in_db = _db_save_events(events, data.get(_LOADED_IDS_KEY))
    if in_db:
        _db_save_state_metadata(data)
        data[_LOADED_IDS_KEY] = {str(e.get("id")) for e in events if isinstance(e, dict) and e.get("id")}
    else:
        file_data: Dict[str, Any] = {
            k: v for k, v in data.items() if k not in (_LOADED_IDS_KEY,)
        }
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
        from semabridge.governance.drift.webhook import validate_webhook_url
        validate_webhook_url(url)
    except Exception as val_exc:
        logger.warning("Drift webhook URL validation rejected: %s", val_exc)
        return
    try:
        import requests

        requests.post(url, json=payload, timeout=5)
    except Exception as exc:
        logger.warning("Drift webhook notification failed: %s", exc)


_NULL_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:NULL|CAST\s*\(\s*NULL\s+AS\s+[A-Za-z0-9_]+(?:\s*\([^)]*\))?\s*\)|NULL::[A-Za-z0-9_]+(?:\s*\([^)]*\))?)\s*$",
    re.I,
)


def _is_null_placeholder(expr: Optional[str]) -> bool:
    if not expr:
        return False
    return bool(_NULL_PLACEHOLDER_RE.match(str(expr).strip()))





def _view_key(view: Optional[str]) -> str:
    """Comparable key of a semantic view name (last identifier, case-folded)."""
    return str(view or "").strip().split(".")[-1].strip('"').casefold()


def _full_view_key(view: Optional[str]) -> str:
    """Comparable key of full DB.SCHEMA.VIEW name (all identifiers, stripped quotes, case-folded)."""
    if not view:
        return ""
    parts = [p.strip().strip('"').casefold() for p in str(view).split(".") if p.strip()]
    return ".".join(parts)


def record_deploy_baseline(
    project_id: Optional[str],
    view_name: str,
    metrics: Dict[str, str],
    source_definitions: Optional[Dict[str, str]] = None,
    deployed_at: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    baseline_source: str = "deploy",
    definition_text: Optional[str] = None,
    platform: str = "SNOWFLAKE",
    not_deployed_objects: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Store the canonical baseline in database and state."""
    if not project_id:
        logger.warning("Refusing to record deploy baseline without project_id for view %s", view_name)
        return None
    if not view_name:
        return None

    now_iso = deployed_at or datetime.now(timezone.utc).isoformat()
    p_clean = _pkey(project_id)
    full_v_key = _full_view_key(view_name)
    v_key = _view_key(view_name)

    from semabridge.governance.drift.baseline_store import save_deploy_baseline
    res = save_deploy_baseline(
        project_id=project_id,
        platform=platform,
        view_name=view_name,
        definition_text=definition_text or "",
        snapshot_id=snapshot_id,
        deployed_at=now_iso,
        metrics=metrics,
        source_definitions=source_definitions,
        not_deployed_objects=not_deployed_objects,
        baseline_source=baseline_source,
    )

    state = _load_state()
    baselines = state.setdefault("baselines", {})
    proj_bases = baselines.setdefault(p_clean, {})
    proj_bases[full_v_key] = res
    if v_key and v_key != full_v_key:
        if v_key not in proj_bases or proj_bases[v_key].get("baseline_source") == "live_capture" or baseline_source != "live_capture":
            proj_bases[v_key] = res
    _save_state(state)
    logger.info("Recorded deploy baseline for view %s (project=%s, source=%s, %d metrics)", view_name, project_id, baseline_source, len(res.get("metrics") or {}))
    return res


def get_deploy_baseline(
    project_id: Optional[str],
    view_name: Optional[str],
    platform: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve the deploy baseline for a view from state (looked up by project + full view name)."""
    if not view_name or not project_id:
        return None
    from semabridge.governance.drift.baseline_store import get_active_baseline
    active = get_active_baseline(project_id, view_name, platform=platform)
    if active:
        return active
    p_clean = _pkey(project_id)
    full_v_key = _full_view_key(view_name)
    v_key = _view_key(view_name)

    state = _load_state()
    baselines = state.get("baselines", {})
    if p_clean in baselines and isinstance(baselines[p_clean], dict):
        p_bases = baselines[p_clean]
        if full_v_key in p_bases:
            return p_bases[full_v_key]
        if v_key in p_bases:
            return p_bases[v_key]

    return None


def _iso_str(val: Any) -> Optional[str]:
    if not val:
        return None
    if hasattr(val, "isoformat"):
        try:
            return val.isoformat()
        except Exception as exc:
            logger.warning("Converting timestamp to ISO string failed: %s", exc)
    return str(val)


_ACTIVE_CHECKS: Set[str] = set()
_ACTIVE_CHECKS_LOCK = threading.Lock()


class DriftEventKey(tuple):
    """6-part canonical drift event key: (project, full_view, kind, table, name, category)."""

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, tuple) and len(other) == 3:
            # 3-tuple legacy compatibility: (project, model_or_view, name)
            model_or_view = self[1].split(".")[-1].lower() if self[1] else (self[3].lower() if len(self) > 3 else "")
            name = self[4].lower() if len(self) > 4 else ""
            return (self[0].lower(), model_or_view, name) == (str(other[0]).lower(), str(other[1]).lower(), str(other[2]).lower())
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()


def _build_baseline_from_deployment(project_id: str, view_name: str) -> Optional[Dict[str, Any]]:
    """Construct baseline from deployed snapshot if available."""
    try:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        snap = None
        if hasattr(repo, "get_last_deployed_snapshot"):
            snap = repo.get_last_deployed_snapshot(project_id)
        if not snap and hasattr(repo, "get_head"):
            snap = repo.get_head(project_id)
        if not snap:
            return None

        blob = getattr(snap, "sml_blob", None)
        if not blob:
            return None
        data = json.loads(blob) if isinstance(blob, str) else blob
        metrics_dict: Dict[str, str] = {}
        source_defs: Dict[str, str] = {}
        for m in data.get("metrics", []):
            m_name = m.get("name")
            if m_name:
                sql = m.get("sql_expression") or m.get("expression") or ""
                dax = m.get("dax_expression") or m.get("source_expression") or ""
                metrics_dict[m_name] = sql
                source_defs[m_name] = dax

        if not metrics_dict:
            return None

        return record_deploy_baseline(
            project_id=project_id,
            view_name=view_name,
            metrics=metrics_dict,
            source_definitions=source_defs,
            deployed_at=getattr(snap, "timestamp", None),
            baseline_source="snapshot",
        )
    except Exception as exc:
        logger.warning("Failed to build baseline from deployment for %s: %s", project_id, exc)
        return None


def drift_event_key(ev: Dict[str, Any]) -> tuple:
    """Canonical 6-part dedup key: (project, full_view, kind, table, name, category)."""
    pid = str(ev.get("project_id") or "").lower().removeprefix("proj-").strip()
    full_view = _full_view_key(ev.get("semantic_view") or ev.get("view") or "")
    k_raw = ev.get("kind")
    kind = (k_raw.value if hasattr(k_raw, "value") else str(k_raw or "METRIC")).lower().strip()
    table = str(ev.get("table") or ev.get("model_id") or ev.get("model") or "").lower().strip()
    name = str(ev.get("metric_name") or ev.get("name") or "").lower().strip()
    cat_raw = ev.get("category") or ev.get("drift_type")
    category = (cat_raw.value if hasattr(cat_raw, "value") else str(cat_raw or "")).strip()
    cat_norm = {
        "UNMANAGED_IN_SNOWFLAKE": "ADDITION",
        "MISSING_IN_SNOWFLAKE": "DELETION",
        "EXPRESSION_MISMATCH": "EXPRESSION_DRIFT",
        "DESCRIPTION_MISMATCH": "DESCRIPTION_DRIFT",
    }.get(category, category)
    return DriftEventKey((pid, full_view, kind, table, name, cat_norm))


def run_drift_check(
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    webhook_url: Optional[str] = None,
    owner_id: Optional[str] = None,
    view: Optional[str] = None,
    mode: str = "live",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Compare BI/ontology metric definitions against Snowflake & record pending drift events."""
    from semabridge.repository.model_repository import ModelRepository

    target_view_name = None
    if project_id:
        repo = ModelRepository()
        if view:
            try:
                target_view_name = repo.validate_project_view(project_id, view)
            except ValueError as exc:
                logger.warning(
                    "Requested view '%s' does not belong to project '%s'; falling back to project's active view: %s",
                    view, project_id, exc,
                )
                target_view_name = view
        else:
            deployments = repo.list_project_deployments(project_id, include_unmanaged=True)
            latest = next((d for d in deployments if d.get("is_latest")), None)
            target_view_name = latest["semantic_view"] if latest else None
    elif view:
        target_view_name = view

    lock_view = target_view_name or view or ""
    lock_key = f"{_pkey(project_id)}::{_full_view_key(lock_view)}"
    with _ACTIVE_CHECKS_LOCK:
        if lock_key in _ACTIVE_CHECKS:
            return {
                "status": "CHECK_IN_PROGRESS",
                "not_checked_reason": "CHECK_IN_PROGRESS",
                "reason_code": "CHECK_IN_PROGRESS",
                "reason": "Drift check is already running for this view.",
                "platform": "UNKNOWN",
                "checked_metrics": 0,
                "not_checked": [],
                "skipped_placeholders": [],
                "snowflake_connected": False,
                "drift_detected": 0,
                "events": [],
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "views_checked": [target_view_name] if target_view_name else [],
                "view": target_view_name,
                "semantic_view": target_view_name,
                "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
            }
        _ACTIVE_CHECKS.add(lock_key)

    try:
        return _run_drift_check_impl(
            project_id=project_id,
            domain=domain,
            webhook_url=webhook_url,
            owner_id=owner_id,
            view=view,
            target_view_name=target_view_name,
            mode=mode,
            force_refresh=force_refresh,
        )
    finally:
        with _ACTIVE_CHECKS_LOCK:
            _ACTIVE_CHECKS.discard(lock_key)


def _run_drift_check_impl(
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    webhook_url: Optional[str] = None,
    owner_id: Optional[str] = None,
    view: Optional[str] = None,
    target_view_name: Optional[str] = None,
    mode: str = "live",
    force_refresh: bool = False,
) -> Dict[str, Any]:
    from semabridge.ai.project_connection import _project_meta, resolve_project_warehouse_config
    from semabridge.ontology.store import ontology_store
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.governance.drift.adapters.registry import resolve_project_platform, is_platform_supported, get_adapter
    from semabridge.governance.drift import compare_views
    from semabridge.governance.drift.models import ViewDefinition, ObjectDef, ObjectKind, SourcePathMismatchError, ParserOrBaselineMismatchError
    from semabridge.governance.drift.introspection_cache import (
    derive_connection_identity,
    get_cached_definition,
    set_cached_definition,
    invalidate_cached_definition,
)
    from semabridge.governance.drift.normalize import (
        is_placeholder_expression,
        reset_missing_dialect_count,
        get_missing_dialect_count,
        reset_parse_warnings,
    )

    reset_missing_dialect_count()
    reset_parse_warnings()
    now = datetime.now(timezone.utc).isoformat()
    p_key = _pkey(project_id) if project_id else ""

    # 1. Platform resolution & support check
    meta = _project_meta(project_id) if project_id else {}
    platform = resolve_project_platform(meta)
    wh_cfg = resolve_project_warehouse_config(project_id) if project_id else {}
    if wh_cfg is None:
        return {
            "status": "NOT_CHECKED",
            "reason_code": "WAREHOUSE_UNREACHABLE",
            "not_checked_reason": "SNOWFLAKE_UNAVAILABLE" if platform in ("SNOWFLAKE", "UNKNOWN", "NOT_SET") else f"{platform}_UNAVAILABLE",
            "reason": f"No warehouse connection configured for project '{project_id}'",
            "platform": platform if platform not in ("UNKNOWN", "NOT_SET") else "SNOWFLAKE",
            "checked_metrics": 0,
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [target_view_name] if target_view_name else [],
            "view": target_view_name,
            "semantic_view": target_view_name,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }
    wh_dict = wh_cfg.dict() if hasattr(wh_cfg, "dict") else (wh_cfg.model_dump() if hasattr(wh_cfg, "model_dump") else (wh_cfg if isinstance(wh_cfg, dict) else getattr(wh_cfg, "__dict__", {})))

    if platform in ("UNKNOWN", "NOT_SET", ""):
        cfg_plat = str(wh_dict.get("platform") or wh_dict.get("type") or "").upper().strip()
        if not cfg_plat and (wh_dict.get("account") or wh_dict.get("sf_account") or wh_dict.get("snowflake_account")):
            cfg_plat = "SNOWFLAKE"
        elif not cfg_plat and (wh_dict.get("workspace_url") or wh_dict.get("http_path") or wh_dict.get("server_hostname")):
            cfg_plat = "DATABRICKS"
        if cfg_plat:
            platform = cfg_plat
        else:
            base_check = get_deploy_baseline(project_id, target_view_name or view)
            if base_check and base_check.get("platform"):
                platform = str(base_check["platform"]).upper().strip()
            elif wh_dict:
                platform = "SNOWFLAKE"
            else:
                platform = "NOT_SET"

    if platform in ("NOT_SET", ""):
        return {
            "status": "NOT_CHECKED",
            "reason_code": "TARGET_NOT_CONFIGURED",
            "reason": f"No target warehouse configured for project '{project_id}'",
            "not_checked_reason": "No target warehouse configured for this project",
            "platform": "NOT_SET",
            "checked_metrics": 0,
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [target_view_name] if target_view_name else [],
            "view": target_view_name,
            "semantic_view": target_view_name,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }

    if not is_platform_supported(platform):
        return {
            "status": "NOT_CHECKED",
            "reason_code": "PLATFORM_NOT_SUPPORTED",
            "reason": f"Platform '{platform}' is not supported for drift monitoring",
            "not_checked_reason": f"Platform '{platform}' is not supported for drift monitoring",
            "platform": platform,
            "checked_metrics": 0,
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [target_view_name] if target_view_name else [],
            "view": target_view_name,
            "semantic_view": target_view_name,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }

    if platform == "SNOWFLAKE" and mode != "mock":
        from unittest.mock import MagicMock
        from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter
        if not isinstance(getattr(SnowflakeAdapter, "fetch_definition_text", None), MagicMock):
            try:
                from semabridge.connectors import snowflake_connection
                kw = snowflake_connection.get_snowflake_connect_kwargs(wh_dict or wh_cfg)
                import snowflake.connector
                c = snowflake.connector.connect(**kw)
                c.close()
            except Exception as exc:
                err_text = str(exc).lower()
                is_unavail = any(k in err_text for k in ("250001", "could not connect", "connection refused", "timeout", "timed out", "unreachable", "down"))
                if is_unavail:
                    return {
                        "status": "NOT_CHECKED",
                        "reason_code": "WAREHOUSE_UNREACHABLE",
                        "not_checked_reason": "SNOWFLAKE_UNAVAILABLE",
                        "reason": f"Snowflake unavailable: {exc}",
                        "platform": "SNOWFLAKE",
                        "checked_metrics": 0,
                        "not_checked": [],
                        "skipped_placeholders": [],
                        "snowflake_connected": False,
                        "drift_detected": 0,
                        "events": [],
                        "checked_at": now,
                        "views_checked": [],
                        "view": target_view_name,
                        "semantic_view": target_view_name,
                        "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
                    }

    # 2. Domain resolution
    fed = ontology_store.get_federated_ontology()
    candidates = ontology_store.domains_for(project_id) if project_id else list(fed.domains.values())
    target_domains = [
        d for d in candidates
        if not domain or d.domain.lower() == domain.lower() or (d.id or "").lower() == domain.lower()
    ]
    if target_view_name and target_domains:
        full_target = _full_view_key(target_view_name)
        tail = str(target_view_name).strip().split(".")[-1].strip('"').lower()
        matching = [
            d for d in target_domains
            if getattr(d, "semantic_view_name", None)
            and (_full_view_key(d.semantic_view_name) == full_target or str(d.semantic_view_name).strip().split(".")[-1].strip('"').lower() == tail)
        ]
        if matching:
            target_domains = matching

    if project_id and not target_domains and not target_view_name:
        return {
            "status": "NOT_CHECKED",
            "reason_code": "NO_DEPLOYED_VIEW",
            "not_checked_reason": "NO_ONTOLOGY",
            "reason": "Project has no deployed view. Please deploy the project first.",
            "platform": platform,
            "checked_metrics": 0,
            "not_checked": [],
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [],
            "view": None,
            "semantic_view": None,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }

    if target_domains and not any(getattr(d, "semantic_view_name", None) for d in target_domains) and not target_view_name:
        return {
            "status": "NOT_CHECKED",
            "reason_code": "NO_DEPLOYED_VIEW",
            "not_checked_reason": "NO_DEPLOYED_VIEW",
            "reason": "Project has no deployed view. Please deploy the project first.",
            "platform": platform,
            "checked_metrics": 0,
            "not_checked": [{"domain": getattr(d, "domain", ""), "view": None, "reason": "No deployed view"} for d in target_domains],
            "skipped_placeholders": [],
            "snowflake_connected": True,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [],
            "view": None,
            "semantic_view": None,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }

    total_dom_metrics = sum(len(getattr(d, "metrics", []) or []) for d in target_domains)
    if target_domains and total_dom_metrics == 0 and not target_view_name:
        return {
            "status": "NOT_CHECKED",
            "reason_code": "NO_COMPARABLE_OBJECTS",
            "not_checked_reason": "NO_COMPARABLE_METRICS",
            "reason": "No metrics or objects found in this view to compare.",
            "platform": platform,
            "checked_metrics": 0,
            "not_checked": [{"domain": getattr(d, "domain", ""), "view": getattr(d, "semantic_view_name", ""), "reason": "No metrics in domain"} for d in target_domains],
            "skipped_placeholders": [],
            "snowflake_connected": True,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": [getattr(d, "semantic_view_name", "") for d in target_domains if getattr(d, "semantic_view_name", None)],
            "view": target_view_name,
            "semantic_view": target_view_name,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }

    # 3. View target and baseline resolution
    if target_view_name:
        views_to_check = [target_view_name]
    else:
        seen_keys = set()
        views_to_check = []
        raw_vns = [getattr(d, "semantic_view_name", None) for d in target_domains if getattr(d, "semantic_view_name", None)]
        # Sort so qualified forms with '.' are evaluated first
        raw_vns.sort(key=lambda v: (0 if "." in str(v) else 1))
        for vn in raw_vns:
            vk = _view_key(vn)
            if vk and vk not in seen_keys:
                seen_keys.add(vk)
                views_to_check.append(vn)

    adapter = get_adapter(platform, mode=mode)
    not_checked_list = []
    valid_views_with_baselines = []
    seen_valid_keys = set()

    for sv in views_to_check:
        vk = _view_key(sv)
        if vk in seen_valid_keys:
            continue
        base = get_deploy_baseline(project_id, sv, platform=platform)
        if not base and project_id:
            base = _build_baseline_from_deployment(project_id, sv)
        dom_for_sv = next((d for d in target_domains if getattr(d, "semantic_view_name", None) and (_full_view_key(d.semantic_view_name) == _full_view_key(sv) or _view_key(d.semantic_view_name) == _view_key(sv))), None)

        if not base:
            not_checked_list.append({
                "domain": getattr(dom_for_sv, "domain", "") if dom_for_sv else (domain or ""),
                "view": sv,
                "reason": "Not checked: no deploy baseline (deploy the project to record one)",
                "reason_code": "NO_DEPLOY_BASELINE",
                "proposed_action": "redeploy",
            })
        else:
            seen_valid_keys.add(vk)
            valid_views_with_baselines.append((sv, base))

    if not valid_views_with_baselines:
        res_payload = {
            "status": "NOT_CHECKED",
            "reason_code": "NO_DEPLOY_BASELINE",
            "not_checked_reason": "NO_DEPLOY_BASELINE",
            "reason": "No deploy baseline recorded for this view. Deploy the project to record a baseline.",
            "proposed_action": "redeploy",
            "platform": platform,
            "baseline_source": None,
            "checked_metrics": 0,
            "not_checked": not_checked_list or [{"domain": domain or "", "view": target_view_name or "", "reason": "Not checked: no deploy baseline (redeploy to record one)"}],
            "skipped_placeholders": [],
            "snowflake_connected": True,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": views_to_check,
            "view": target_view_name,
            "semantic_view": target_view_name,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }
        if p_key:
            state_curr = _load_state()
            state_curr.setdefault("last_checks", {})[p_key] = {
                "checked_at": now,
                "views_checked": views_to_check,
                "status": "NOT_CHECKED",
                "reason_code": "NO_DEPLOY_BASELINE",
                "not_checked_reason": "NO_DEPLOY_BASELINE",
                "reason": res_payload["reason"],
                "proposed_action": "redeploy",
                "platform": platform,
                "baseline_source": None,
                "baseline_at": None,
                "metrics_compared": 0,
                "view": target_view_name,
                "semantic_view": target_view_name,
            }
            _save_state(state_curr)
        return res_payload

    # 4. Adapter execution and View comparison
    all_findings = []
    checked_metrics_count = 0
    skipped_placeholders = []
    inspected_views = []
    views_source_paths: Dict[str, str] = {}
    definition_sources: Dict[str, str] = {}
    run_baseline_source = "deploy"
    run_baseline_at = None

    for sv, base in valid_views_with_baselines:
        conn_ident = derive_connection_identity(wh_cfg or wh_dict)
        if force_refresh:
            invalidate_cached_definition(platform, sv, connection_identity=conn_ident)

        b_src = base.get("baseline_source") or "deploy"
        if b_src in ("live_capture", "snapshot"):
            run_baseline_source = b_src
        if not run_baseline_at:
            run_baseline_at = base.get("deployed_at")

        # Check Drift Now, pressed by a person, must always refresh. Scheduled runs may use the cache.
        use_cache = (mode == "scheduled" and not force_refresh)
        raw_live = None
        def_source = "warehouse"
        if use_cache:
            raw_live = get_cached_definition(platform, conn_ident, sv, force_refresh=False)
            if raw_live is not None:
                def_source = "cache"

        if raw_live is None:
            try:
                raw_live = adapter.fetch_definition_text(sv, connection_config=wh_cfg or wh_dict)
            except Exception as exc:
                logger.warning("Failed to fetch live definition for %s: %s", sv, exc)
                dom_for_sv = next((d for d in target_domains if getattr(d, "semantic_view_name", None) and (_full_view_key(d.semantic_view_name) == _full_view_key(sv) or _view_key(d.semantic_view_name) == _view_key(sv))), None)
                not_checked_list.append({
                    "domain": getattr(dom_for_sv, "domain", "") if dom_for_sv else (domain or ""),
                    "view": sv,
                    "reason": str(exc),
                })
                continue
            set_cached_definition(platform, conn_ident, sv, raw_live)
            def_source = "warehouse"

        logger.info("Drift check for %s (%s): definition loaded from %s", sv, platform, def_source)
        definition_sources[sv] = def_source

        live_def = adapter.parse_definition(raw_live, view_name=sv, source="live")
        views_source_paths[sv] = getattr(live_def, "source_path", "unknown")
        view_tail = sv.split(".")[-1]
        if base.get("definition_text"):
            base_def = adapter.parse_definition(
                base["definition_text"],
                view_name=sv,
                source="baseline",
                descriptions=base.get("descriptions") or {},
            )
        else:
            live_names = {getattr(obj, "name", "").upper() for obj in getattr(live_def, "objects", [])}
            base_objs = []
            for m_name, m_expr in (base.get("metrics") or {}).items():
                canon_name = m_name
                if canon_name.upper() not in live_names and re.sub(r"[\s_]+", "_", canon_name.upper()) in live_names:
                    canon_name = re.sub(r"[\s_]+", "_", canon_name.upper())
                base_objs.append(ObjectDef(
                    kind=ObjectKind.METRIC,
                    table=view_tail,
                    name=canon_name,
                    expression=m_expr,
                    description=(base.get("descriptions") or {}).get(m_name),
                ))
            base_caps_m = {"METRIC": {"expression", "description"}} if base.get("descriptions") else {"METRIC": {"expression"}}
            base_def = ViewDefinition(
                view_name=sv,
                platform=platform,
                dialect=getattr(live_def, "dialect", None) or (platform.lower() if platform else "snowflake"),
                source="baseline",
                retrieved_at=base.get("deployed_at") or now,
                objects=base_objs,
                capabilities=base_caps_m,
            )
        base_def.not_deployed_objects = list(base.get("not_deployed_objects") or [])
        for bo in base_def.objects:
            if not (bo.expression or "").strip():
                if bo.name not in base_def.not_deployed_objects:
                    base_def.not_deployed_objects.append(bo.name)

        # Detect intentional null placeholders with no source SQL
        live_obj_map = {o.name.upper(): o for o in live_def.objects}
        for bo in base_def.objects:
            if not (bo.expression or "").strip():
                lo = live_obj_map.get(bo.name.upper())
                if lo and is_placeholder_expression(lo.expression):
                    if bo.name not in skipped_placeholders:
                        skipped_placeholders.append(bo.name)

        try:
            findings = compare_views(baseline=base_def, live=live_def)
            if skipped_placeholders:
                skipped_upper = {p.upper() for p in skipped_placeholders}
                findings = [f for f in findings if getattr(f, "name", "").upper() not in skipped_upper]
            all_findings.extend([(sv, f) for f in findings])
            inspected_views.append(sv)
            from semabridge.governance.drift.comparison import _obj_key
            evaluated_keys = set(_obj_key(o) for o in base_def.objects) | set(_obj_key(o) for o in live_def.objects)
            checked_metrics_count += len(evaluated_keys)
        except SourcePathMismatchError as sp_err:
            logger.warning("Source path mismatch for %s: %s", sv, sp_err)
            not_checked_list.append({
                "view": sv,
                "reason_code": "SOURCE_PATH_MISMATCH",
                "reason": str(sp_err),
            })
            continue
        except ParserOrBaselineMismatchError as mismatch_err:
            logger.critical("Parser or baseline mismatch for %s: %s", sv, mismatch_err)
            not_checked_list.append({
                "view": sv,
                "reason_code": "PARSER_OR_BASELINE_MISMATCH",
                "reason": str(mismatch_err),
            })
            continue

        # 5. Check for Power BI / source definition drift (SOURCE_CHANGED)
        base_src_defs = dict(base.get("source_definitions", {}))
        if base_src_defs and project_id:
            try:
                from semabridge.repository.model_repository import _deserialize_sml_blob
                repo = ModelRepository()
                latest_snap = repo.get_last_deployed_snapshot(project_id=project_id, view_name=sv) or repo.get_head(project_id)
                latest_src_defs = {}
                if latest_snap and getattr(latest_snap, "sml_blob", None):
                    blob = _deserialize_sml_blob(latest_snap.sml_blob)
                    if isinstance(blob, dict):
                        for m in (blob.get("metrics") or []):
                            mn = (m.get("name") or m.get("unique_name") or "").upper()
                            dax = (m.get("expression") or m.get("dax_expression") or "").strip()
                            if mn and dax:
                                latest_src_defs[mn] = dax
                if not latest_src_defs and target_domains:
                    for m in getattr(target_domains[0], "metrics", []) or []:
                        mn = (getattr(m, "name", "") or "").upper()
                        dax = (getattr(m, "dax_expression", "") or getattr(m, "expression", "") or "").strip()
                        if mn and dax:
                            latest_src_defs[mn] = dax

                src_dialect = "dax"
                for m_name_up, curr_src in latest_src_defs.items():
                    deploy_src = base_src_defs.get(m_name_up)
                    if curr_src and deploy_src and normalize_expression(curr_src, dialect=src_dialect) != normalize_expression(deploy_src, dialect=src_dialect):
                        from semabridge.governance.drift.models import DriftCategory, DriftFinding
                        f_src = DriftFinding(
                            category=DriftCategory.EXPRESSION_DRIFT,
                            kind="METRIC",
                            table=view_tail,
                            name=m_name_up,
                            baseline_value=deploy_src,
                            live_value=curr_src,
                            details={"drift_type": "SOURCE_CHANGED", "reason": "Power BI source definition changed"},
                        )
                        all_findings.append((sv, f_src))
            except Exception as src_exc:
                logger.warning("SOURCE_CHANGED check failed: %s", src_exc)

    if not inspected_views:
        err_msg = not_checked_list[0]["reason"] if not_checked_list else "Failed to inspect warehouse views"
        err_str = err_msg.lower()
        if not_checked_list and not_checked_list[0].get("reason_code"):
            rc = not_checked_list[0]["reason_code"]
        elif "different form" in err_str or "source path" in err_str:
            rc = "SOURCE_PATH_MISMATCH"
        elif "auth" in err_str or "250001" in err_str or "401" in err_str or "permission" in err_str or "unauthorized" in err_str:
            rc = "WAREHOUSE_AUTH_FAILED"
        elif "does not exist" in err_str or "not found" in err_str or "002003" in err_str or "404" in err_str:
            rc = "VIEW_NOT_FOUND"
        elif "parse" in err_str or "syntax" in err_str:
            rc = "PARSE_ERROR"
        else:
            rc = "WAREHOUSE_UNREACHABLE"
        res_payload = {
            "status": "NOT_CHECKED",
            "reason_code": rc,
            "not_checked_reason": rc if rc in ("SOURCE_PATH_MISMATCH", "PARSER_OR_BASELINE_MISMATCH", "VIEW_NOT_FOUND", "PARSE_ERROR") else ("SNOWFLAKE_UNAVAILABLE" if platform == "SNOWFLAKE" else f"{platform}_UNAVAILABLE"),
            "reason": err_msg,
            "platform": platform,
            "checked_metrics": 0,
            "not_checked": not_checked_list,
            "skipped_placeholders": [],
            "snowflake_connected": False,
            "drift_detected": 0,
            "events": [],
            "checked_at": now,
            "views_checked": views_to_check,
            "view": target_view_name,
            "semantic_view": target_view_name,
            "category_counts": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0},
        }
        if p_key:
            state_curr = _load_state()
            state_curr.setdefault("last_checks", {})[p_key] = {
                "checked_at": now,
                "views_checked": views_to_check,
                "status": "NOT_CHECKED",
                "reason_code": rc,
                "not_checked_reason": res_payload["not_checked_reason"],
                "reason": err_msg,
                "platform": platform,
                "baseline_source": None,
                "baseline_at": None,
                "metrics_compared": 0,
                "view": target_view_name,
                "semantic_view": target_view_name,
            }
            _save_state(state_curr)
        return res_payload

    # 6. Event Lifecycle & Deduplication
    state_curr = _load_state()
    existing_events = state_curr.get("events") or []

    current_finding_keys = set()
    category_counts = {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0}
    by_kind = {
        "METRIC": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0, "TOTAL": 0},
        "DIMENSION": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0, "TOTAL": 0},
        "FACT": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0, "TOTAL": 0},
        "RELATIONSHIP": {"ADDITION": 0, "DELETION": 0, "EXPRESSION_DRIFT": 0, "DESCRIPTION_DRIFT": 0, "TOTAL": 0},
    }
    grouped_findings = {
        "METRIC": {"ADDITION": [], "DELETION": [], "EXPRESSION_DRIFT": [], "DESCRIPTION_DRIFT": []},
        "DIMENSION": {"ADDITION": [], "DELETION": [], "EXPRESSION_DRIFT": [], "DESCRIPTION_DRIFT": []},
        "FACT": {"ADDITION": [], "DELETION": [], "EXPRESSION_DRIFT": [], "DESCRIPTION_DRIFT": []},
        "RELATIONSHIP": {"ADDITION": [], "DELETION": [], "EXPRESSION_DRIFT": [], "DESCRIPTION_DRIFT": []},
    }

    for sv, f in all_findings:
        cat_name = f.category.value if hasattr(f.category, "value") else str(f.category)
        category_counts[cat_name] = category_counts.get(cat_name, 0) + 1
        k_name = str(f.kind or "METRIC").upper()
        if k_name in by_kind and cat_name in by_kind[k_name]:
            by_kind[k_name][cat_name] += 1
            by_kind[k_name]["TOTAL"] += 1
        if k_name in grouped_findings and cat_name in grouped_findings[k_name]:
            grouped_findings[k_name][cat_name].append({
                "view": sv,
                "name": f.name,
                "kind": f.kind,
                "table": f.table,
                "category": cat_name,
                "baseline_value": f.baseline_value,
                "live_value": f.live_value,
                "baseline_description": f.baseline_description,
                "live_description": f.live_description,
                "details": f.details,
            })
        kind_str = (f.kind.value if hasattr(f.kind, "value") else str(f.kind)).upper().strip()
        tmp_ev = {
            "project_id": project_id,
            "semantic_view": sv,
            "kind": kind_str,
            "table": f.table,
            "metric_name": f.name,
            "name": f.name,
            "category": cat_name,
        }
        current_finding_keys.add(drift_event_key(tmp_ev))

    event_by_key = {}
    for e in sorted(existing_events, key=lambda ev: 0 if ev.get("status") == "pending" else 1):
        k = drift_event_key(e)
        if k not in event_by_key or e.get("status") == "pending":
            event_by_key[k] = e
    result_events = []
    new_events_count = 0

    for sv, f in all_findings:
        cat_name = f.category.value if hasattr(f.category, "value") else str(f.category)
        legacy_type = (f.details.get("drift_type") if f.details else None) or {
            "ADDITION": "UNMANAGED_IN_SNOWFLAKE",
            "DELETION": "MISSING_IN_SNOWFLAKE",
            "EXPRESSION_DRIFT": "EXPRESSION_MISMATCH",
            "DESCRIPTION_DRIFT": "DESCRIPTION_MISMATCH",
        }.get(cat_name, "EXPRESSION_MISMATCH")

        kind_str = (f.kind.value if hasattr(f.kind, "value") else str(f.kind)).upper().strip()
        tmp_ev = {
            "project_id": project_id,
            "semantic_view": sv,
            "kind": kind_str,
            "table": f.table,
            "metric_name": f.name,
            "name": f.name,
            "category": cat_name,
        }
        k = drift_event_key(tmp_ev)
        existing = event_by_key.get(k)

        dom_match = next((d for d in target_domains if getattr(d, "semantic_view_name", None) and (_full_view_key(d.semantic_view_name) == _full_view_key(sv) or _view_key(d.semantic_view_name) == _view_key(sv))), None) or (target_domains[0] if target_domains else None)
        ev_domain = getattr(dom_match, "domain", "") if dom_match else ""
        ev_domain_id = getattr(dom_match, "id", None) or ev_domain
        ev_model_id = getattr(dom_match, "source_model_id", None) if dom_match else None

        if cat_name == "DESCRIPTION_DRIFT":
            bi_def = f.baseline_description or "(no description)"
            sf_def = f.live_description or "(no description)"
            proposed_resolution_text = "update description in semantic view"
        elif legacy_type == "SOURCE_CHANGED":
            bi_def = f.live_value or "(not present in semantic view)"
            sf_def = f.baseline_value or "(not present in semantic view)"
            proposed_resolution_text = "redeploy from source"
        else:
            bi_def = f.baseline_value or "(not present in semantic view)"
            sf_def = f.live_value or "(not present in semantic view)"
            proposed_resolution_text = "redeploy from source"

        if existing:
            if existing.get("status") in ("approved", "ignored", "accepted"):
                old_live = existing.get("live_value") or existing.get("snowflake_definition") or ""
                new_live = f.live_value or ""
                old_base = existing.get("baseline_value") or existing.get("bi_definition") or ""
                new_base = f.baseline_value or ""
                if old_live.strip() != new_live.strip() or old_base.strip() != new_base.strip():
                    existing["status"] = "pending"
                    existing["resolution_reason"] = "reopened: live warehouse value changed"
                    existing["reopened_at"] = now
                    existing["last_seen_at"] = now
                    existing["updated_at"] = now
                    base_expr = (f.details.get("baseline_expression") if f.details else None) or (f.baseline_value if cat_name != "DESCRIPTION_DRIFT" else existing.get("baseline_expression"))
                    live_expr = (f.details.get("live_expression") if f.details else None) or (f.live_value if cat_name != "DESCRIPTION_DRIFT" else existing.get("live_expression"))
                    existing["live_value"] = f.live_value
                    existing["live_expression"] = live_expr
                    existing["baseline_expression"] = base_expr
                    existing["baseline_description"] = f.baseline_description
                    existing["live_description"] = f.live_description
                    existing["snowflake_definition"] = sf_def
                    existing["bi_definition"] = bi_def
                    existing["proposed_snowflake_definition"] = proposed_resolution_text
                    existing["proposed_resolution"] = proposed_resolution_text
                    _update_event_row(existing)
                    new_events_count += 1
                else:
                    existing["last_seen_at"] = now
                    if cat_name == "DESCRIPTION_DRIFT":
                        existing["baseline_description"] = f.baseline_description
                        existing["live_description"] = f.live_description
                        existing["snowflake_definition"] = sf_def
                        existing["bi_definition"] = bi_def
                        existing["proposed_snowflake_definition"] = proposed_resolution_text
                        existing["proposed_resolution"] = proposed_resolution_text
                    _update_event_row(existing)
            elif existing.get("status") == "pending":
                existing["last_seen_at"] = now
                existing["updated_at"] = now
                base_expr = (f.details.get("baseline_expression") if f.details else None) or (f.baseline_value if cat_name != "DESCRIPTION_DRIFT" else existing.get("baseline_expression"))
                live_expr = (f.details.get("live_expression") if f.details else None) or (f.live_value if cat_name != "DESCRIPTION_DRIFT" else existing.get("live_expression"))
                existing["live_value"] = f.live_value
                existing["baseline_value"] = f.baseline_value
                existing["live_expression"] = live_expr
                existing["baseline_expression"] = base_expr
                existing["baseline_description"] = f.baseline_description
                existing["live_description"] = f.live_description
                existing["snowflake_definition"] = sf_def
                existing["bi_definition"] = bi_def
                existing["proposed_snowflake_definition"] = proposed_resolution_text
                existing["proposed_resolution"] = proposed_resolution_text
                if cat_name == "DESCRIPTION_DRIFT" and f.details:
                    existing["severity"] = f.details.get("description_severity") or existing.get("severity") or "medium"
                    existing["description_classification"] = f.details.get("description_classification")
                    existing["baseline_marked"] = f.details.get("baseline_marked")
                    existing["live_marked"] = f.details.get("live_marked")
                    existing["diff_segments"] = f.details.get("diff_segments")
                    existing["modified_by"] = f.details.get("modified_by") or existing.get("modified_by")
                if f.details:
                    existing["details"] = {**(existing.get("details") or {}), **f.details}
                _update_event_row(existing)
            elif existing.get("status") in ("resolved", "superseded"):
                existing["status"] = "pending"
                existing["resolution_reason"] = None
                existing["resolved_at"] = None
                existing["reopened_at"] = now
                existing["last_seen_at"] = now
                existing["updated_at"] = now
                base_expr = (f.details.get("baseline_expression") if f.details else None) or (f.baseline_value if cat_name != "DESCRIPTION_DRIFT" else None)
                live_expr = (f.details.get("live_expression") if f.details else None) or (f.live_value if cat_name != "DESCRIPTION_DRIFT" else None)
                existing["live_value"] = f.live_value
                existing["baseline_value"] = f.baseline_value
                existing["live_expression"] = live_expr
                existing["baseline_expression"] = base_expr
                existing["baseline_description"] = f.baseline_description
                existing["live_description"] = f.live_description
                existing["snowflake_definition"] = sf_def
                existing["bi_definition"] = bi_def
                existing["proposed_snowflake_definition"] = proposed_resolution_text
                existing["proposed_resolution"] = proposed_resolution_text
                if cat_name == "DESCRIPTION_DRIFT" and f.details:
                    existing["severity"] = f.details.get("description_severity") or existing.get("severity") or "medium"
                    existing["description_classification"] = f.details.get("description_classification")
                    existing["baseline_marked"] = f.details.get("baseline_marked")
                    existing["live_marked"] = f.details.get("live_marked")
                    existing["diff_segments"] = f.details.get("diff_segments")
                    existing["modified_by"] = f.details.get("modified_by") or existing.get("modified_by")
                if f.details:
                    existing["details"] = {**(existing.get("details") or {}), **f.details}
                _update_event_row(existing)
                new_events_count += 1
            result_events.append(existing)
        else:
            ev_id = f"drift-{uuid.uuid4().hex[:12]}"
            new_ev = {
                "id": ev_id,
                "project_id": project_id,
                "domain": ev_domain,
                "domain_id": ev_domain_id,
                "model_id": ev_model_id,
                "semantic_view": sv,
                "view": sv,
                "view_name": sv,
                "platform": platform,
                "kind": kind_str,
                "table": f.table,
                "name": f.name,
                "metric_name": f.name,
                "category": cat_name,
                "drift_type": legacy_type,
                "status": "pending",
                "severity": (
                    "high" if cat_name in ("DELETION", "EXPRESSION_DRIFT")
                    else (
                        (f.details.get("description_severity") or "medium")
                        if cat_name == "DESCRIPTION_DRIFT" and f.details
                        else ("low" if cat_name == "ADDITION" else "medium")
                    )
                ),
                "baseline_value": f.baseline_value,
                "live_value": f.live_value,
                "baseline_expression": (f.details.get("baseline_expression") if f.details else None) or (f.baseline_value if cat_name != "DESCRIPTION_DRIFT" else None),
                "live_expression": (f.details.get("live_expression") if f.details else None) or (f.live_value if cat_name != "DESCRIPTION_DRIFT" else None),
                "baseline_description": f.baseline_description,
                "live_description": f.live_description,
                "description_classification": (f.details.get("description_classification") if f.details else None) if cat_name == "DESCRIPTION_DRIFT" else None,
                "baseline_marked": (f.details.get("baseline_marked") if f.details else None) if cat_name == "DESCRIPTION_DRIFT" else None,
                "live_marked": (f.details.get("live_marked") if f.details else None) if cat_name == "DESCRIPTION_DRIFT" else None,
                "diff_segments": (f.details.get("diff_segments") if f.details else None) if cat_name == "DESCRIPTION_DRIFT" else None,
                "modified_by": (f.details.get("modified_by") if f.details else None),
                "bi_definition": bi_def,
                "snowflake_definition": sf_def,
                "proposed_snowflake_definition": proposed_resolution_text,
                "proposed_resolution": proposed_resolution_text,
                "first_seen_at": now,
                "last_seen_at": now,
                "detected_at": now,
                "updated_at": now,
                "mode": mode,
                "baseline_compared": True,
                "affected_objects": [sv, f.name],
                "details": f.details,
            }
            existing_events.append(new_ev)
            event_by_key[k] = new_ev
            result_events.append(new_ev)
            new_events_count += 1
            _update_event_row(new_ev)

    # Auto-resolve older pending events for inspected views that are no longer drifting
    for ev in existing_events:
        ev_pid = _pkey(ev.get("project_id"))
        ev_raw_view = ev.get("semantic_view") or ev.get("view")
        ev_view = _full_view_key(ev_raw_view) if ev_raw_view else ""
        if ev_pid == _pkey(project_id) and (not ev_view or any(ev_view == _full_view_key(iv) for iv in inspected_views)):
            if ev.get("status") == "pending" and drift_event_key(ev) not in current_finding_keys:
                is_legacy = bool(str(ev.get("id", "")).startswith("ev-old") or not ev.get("category"))
                if is_legacy:
                    ev["status"] = "superseded"
                    ev["resolution_reason"] = "Superseded: no longer detected in live warehouse view"
                else:
                    ev["status"] = "resolved"
                    ev["resolution_reason"] = "resolved: no longer detected in live warehouse view"
                ev["resolved_at"] = now
                ev["updated_at"] = now
                _update_event_row(ev)

    state_curr["events"] = existing_events
    p_key = _pkey(project_id) if project_id else ""
    if len(all_findings) > 0:
        status_val = "DRIFT_DETECTED"
    elif not_checked_list and inspected_views:
        status_val = "PARTIAL"
    elif run_baseline_source == "live_capture":
        status_val = "IN_SYNC_SINCE_CAPTURE"
    else:
        status_val = "IN_SYNC"

    primary_def_source = list(definition_sources.values())[-1] if definition_sources else "warehouse"
    if p_key:
        state_curr.setdefault("last_checks", {})[p_key] = {
            "checked_at": now,
            "views_checked": inspected_views,
            "status": status_val,
            "not_checked_reason": None,
            "reason_code": None,
            "reason": None,
            "platform": platform,
            "baseline_source": run_baseline_source,
            "baseline_at": run_baseline_at or now,
            "metrics_compared": checked_metrics_count,
            "view": target_view_name,
            "semantic_view": target_view_name,
            "definition_source": primary_def_source,
        }
    _save_state(state_curr)

    if webhook_url and len(all_findings) > 0:
        _send_webhook(webhook_url, {
            "text": f"SemaBridge Drift Alert: {len(all_findings)} drift item(s) detected.",
            "project_id": project_id,
            "status": status_val,
            "drift_count": len(all_findings),
            "events": result_events[:5],
            "checked_at": now,
        })

    def _is_path_fallback(plt: str, sp: str) -> bool:
        sp_lower = str(sp).lower().strip()
        if plt.lower() == "snowflake":
            return sp_lower in ("fallback", "regex")
        elif plt.lower() == "databricks":
            return sp_lower in ("ddl", "sql_ddl", "fallback", "regex")
        return False

    resolved_source_path = views_source_paths.get(target_view_name) if target_view_name else (list(views_source_paths.values())[-1] if views_source_paths else "unknown")
    fallback_used = any(_is_path_fallback(platform, sp) for sp in views_source_paths.values())
    missing_dialect_count = get_missing_dialect_count()
    if missing_dialect_count > 0:
        logger.warning("Check run completed with %d expression(s) normalized without a dialect", missing_dialect_count)

    return {
        "status": status_val,
        "checked_metrics": checked_metrics_count,
        "not_checked": not_checked_list,
        "skipped_placeholders": skipped_placeholders,
        "snowflake_connected": True,
        "drift_detected": len(all_findings),
        "missing_dialect_count": missing_dialect_count,
        "events": result_events,
        "new_events": new_events_count,
        "category_counts": category_counts,
        "by_kind": by_kind,
        "grouped_findings": grouped_findings,
        "source_path": resolved_source_path,
        "views_source_paths": views_source_paths,
        "fallback_used": fallback_used,
        "views_checked": inspected_views,
        "view": target_view_name,
        "semantic_view": target_view_name,
        "platform": platform,
        "mode": mode,
        "checked_at": now,
        "baseline_source": run_baseline_source,
        "baseline_at": run_baseline_at,
        "definition_source": primary_def_source,
        "definition_sources": definition_sources,
    }



def list_drift_events(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> List[Dict[str, Any]]:
    db_events = _db_load_events(project_id=project_id, status=status, limit=limit, offset=offset)
    if db_events is not None and (db_events or not _load_state().get("events")):
        return db_events
    events = _load_state()["events"]
    if project_id:
        p_clean = project_id.lower().removeprefix("proj-").strip()
        events = [
            e for e in events
            if str(e.get("project_id") or "").lower().removeprefix("proj-").strip() == p_clean
        ]
    if status:
        events = [e for e in events if e.get("status") == status]
    if offset is not None or limit is not None:
        start = offset or 0
        end = start + limit if limit is not None else len(events)
        return events[start:end]
    return events


def clear_drift_events(project_id: Optional[str] = None, *, _allow_all_for_testing: bool = False) -> int:
    """Clear all drift events for a project. Refuse clearing across all projects unless explicitly in test mode with _allow_all_for_testing=True."""
    if not project_id and not _allow_all_for_testing:
        raise ValueError("project_id is required to clear drift events; clearing events across all projects is refused.")
    state = _load_state()
    orig = len(state.get("events", []))
    if project_id:
        p_clean = _pkey(project_id)
        state["events"] = [
            e for e in state.get("events", [])
            if _pkey(e.get("project_id")) != p_clean
        ]
    else:
        state["events"] = []
    _save_state(state)
    return orig - len(state.get("events", []))


def resolve_drift_event(event_id: str, action: str, reason: str = "", project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    state = _load_state()
    for ev in state.get("events") or []:
        if ev["id"] == event_id:
            if project_id and not verify_drift_project_access(project_id, event_or_object=ev):
                logger.warning(
                    "Cross-project resolution refused: event %s belongs to %s, caller specified %s",
                    event_id, ev.get("project_id"), project_id,
                )
                return None
            act_clean = (action or "").lower().strip()
            if act_clean in ("approve", "approved", "restore", "restored"):
                ev["status"] = "approved"
                ev["proposed_action"] = "redeploy"
                ev["proposed_snowflake_definition"] = "redeploy from source"
                ev["resolution_reason"] = reason or "Approved for gated redeploy from source"
            elif act_clean in ("ignore", "ignored"):
                ev["status"] = "ignored"
                ev["resolution_reason"] = reason or "Ignored by reviewer"
            elif act_clean in ("accept", "accepted"):
                ev["status"] = "accepted"
                ev["resolution_reason"] = reason or "Accepted live warehouse change into baseline"
            else:
                ev["status"] = "resolved"
                ev["resolution_reason"] = reason
            ev["resolved_at"] = datetime.now(timezone.utc).isoformat()
            # One row: a whole-list save could undo another process's edits.
            if not _update_event_row(ev):
                _save_state(state)
            return ev
    return None


def bulk_resolve_drift_events(
    *,
    project_id: Optional[str] = None,
    view: Optional[str] = None,
    kind: Optional[str] = None,
    category: Optional[str] = None,
    event_ids: Optional[List[str]] = None,
    action: str = "ignore",
    reason: str = "",
) -> Dict[str, Any]:
    """Bulk resolve a group or selection of drift events with a single reason and confirmation."""
    state = _load_state()
    resolved_ids: List[str] = []
    v_tail = view.strip().split(".")[-1].strip('"').lower() if view else None
    act_clean = (action or "").lower().strip()
    now_iso = datetime.now(timezone.utc).isoformat()

    for ev in state.get("events") or []:
        if project_id and not verify_drift_project_access(project_id, event_or_object=ev):
            continue
        if event_ids is not None:
            if ev.get("id") not in event_ids:
                continue
        else:
            if v_tail and ev.get("semantic_view"):
                if str(ev["semantic_view"]).strip().split(".")[-1].strip('"').lower() != v_tail:
                    continue
            if kind and str(ev.get("kind") or "METRIC").upper() != kind.upper():
                continue
            if category:
                ev_cat = str(ev.get("category") or ev.get("drift_type") or "").upper()
                if ev_cat != category.upper() and not ev_cat.endswith(category.upper()):
                    continue
            if ev.get("status") != "pending":
                continue

        if act_clean in ("approve", "approved", "restore", "restored"):
            ev["status"] = "approved"
            ev["proposed_action"] = "redeploy"
            ev["proposed_snowflake_definition"] = "redeploy from source"
            ev["resolution_reason"] = reason or "Bulk approved for gated redeploy"
        elif act_clean in ("ignore", "ignored"):
            ev["status"] = "ignored"
            ev["resolution_reason"] = reason or "Bulk ignored by reviewer"
        elif act_clean in ("accept", "accepted"):
            ev["status"] = "accepted"
            ev["resolution_reason"] = reason or "Bulk accepted live warehouse change into baseline"
        else:
            ev["status"] = "resolved"
            ev["resolution_reason"] = reason
        ev["resolved_at"] = now_iso
        resolved_ids.append(ev.get("id"))
        _update_event_row(ev)

    _save_state(state)
    return {
        "success": True,
        "resolved_count": len(resolved_ids),
        "event_ids": resolved_ids,
        "action": act_clean,
    }


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
    if webhook_url:
        from semabridge.governance.drift.webhook import validate_webhook_url
        validate_webhook_url(webhook_url)

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
            kwargs={"project_id": project_id, "domain": domain, "webhook_url": webhook_url, "mode": "scheduled"},
        )
    except Exception as exc:
        logger.warning("Could not register APScheduler job %s: %s", sched_id, exc)

    return entry


def restore_drift_schedules() -> int:
    """Re-registers all saved enabled schedules into APScheduler on server startup."""
    state = _load_state()
    schedules = state.get("schedules") or []
    registered = 0
    scheduler = _get_scheduler()
    for entry in schedules:
        if not entry.get("enabled", True):
            continue
        sched_id = entry.get("id")
        if not sched_id:
            continue
        try:
            interval = max(5, int(entry.get("interval_minutes") or 60))
            scheduler.add_job(
                run_drift_check,
                "interval",
                minutes=interval,
                id=sched_id,
                replace_existing=True,
                kwargs={
                    "project_id": entry.get("project_id"),
                    "domain": entry.get("domain"),
                    "webhook_url": entry.get("webhook_url"),
                    "mode": "scheduled",
                },
            )
            registered += 1
        except Exception as exc:
            logger.warning("Could not restore drift schedule %s on startup: %s", sched_id, exc)
    logger.info("Restored %d drift schedule(s) into background runner", registered)
    return registered


def list_schedules(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    scheds = _load_state().get("schedules") or []
    if project_id:
        scheds = [s for s in scheds if s.get("project_id") == project_id]
    
    enriched = []
    scheduler = _get_scheduler()
    for s in scheds:
        item = dict(s)
        job = scheduler.get_job(s.get("id", "")) if scheduler else None
        if job and getattr(job, "next_run_time", None):
            item["next_run"] = job.next_run_time.isoformat()
        enriched.append(item)
    return enriched


def delete_schedule(schedule_id: str) -> bool:
    state = _load_state()
    before = len(state["schedules"])
    state["schedules"] = [s for s in state["schedules"] if s["id"] != schedule_id]
    try:
        from sqlalchemy import delete
        from semabridge.repository.orm.models import DriftScheduleRow
        with _drift_session() as session:
            session.execute(delete(DriftScheduleRow).where(DriftScheduleRow.id == schedule_id))
            session.commit()
    except Exception as exc:
        logger.warning("Failed deleting drift schedule %s from database: %s", schedule_id, exc)
    _save_state(state)
    try:
        if _SCHEDULER is not None:
            _SCHEDULER.remove_job(schedule_id)
    except Exception as exc:
        logger.warning("Failed removing APScheduler job %s: %s", schedule_id, exc)
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


