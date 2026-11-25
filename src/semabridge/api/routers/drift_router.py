"""REST API Router for Scheduled Match & Drift Detection with Approval Workflow."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from semabridge.governance.drift_service import (
    _load_state,
    _save_state,
    _send_webhook,
    clear_drift_events,
    create_schedule,
    delete_schedule,
    drift_event_key,
    list_drift_events,
    list_schedules,
    reset_simulated_metric_drift,
    resolve_drift_event,
    run_drift_check,
    seed_mock_drift_events,
    simulate_metric_drift,
)

logger = logging.getLogger("semabridge.api.routers.drift_router")

router = APIRouter(prefix="/api/drift", tags=["drift_monitoring"])


class DriftCheckRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    webhook_url: Optional[str] = None
    target_type: Optional[str] = None


class DriftResolutionRequest(BaseModel):
    reason: str = ""


class SimulateMetricChangeRequest(BaseModel):
    project_id: str
    metric_name: Optional[str] = "TOTAL_UNITS"
    new_expression: Optional[str] = None


class ResetMetricChangeRequest(BaseModel):
    project_id: str
    metric_name: Optional[str] = "TOTAL_UNITS"


class CreateScheduleRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    interval_minutes: int = 60
    webhook_url: Optional[str] = None
    job_type: str = "drift_and_parity"


def _normalize_target_type(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    if "databricks" in val:
        return "databricks"
    if "fabric" in val:
        return "fabric"
    if "snowflake" in val:
        return "snowflake"
    return val


def _extract_target_type_from_meta(meta: Any) -> str:
    if not isinstance(meta, dict):
        return ""
    for key in ("target_type",):
        found = _normalize_target_type(meta.get(key))
        if found:
            return found
    target = meta.get("target")
    if isinstance(target, dict):
        found = _normalize_target_type(target.get("type"))
        if found:
            return found
    elif isinstance(target, str):
        found = _normalize_target_type(target)
        if found:
            return found
    targets = meta.get("targets")
    if isinstance(targets, list) and targets:
        first = targets[0]
        if isinstance(first, dict):
            found = _normalize_target_type(first.get("type"))
            if found:
                return found
        elif isinstance(first, str):
            found = _normalize_target_type(first)
            if found:
                return found
    return ""


def _resolve_project_target_type(project_id: Optional[str], explicit: Optional[str] = None) -> str:
    """Resolve the target platform ('databricks' or 'snowflake') from request, YAML/config, project metadata, or latest snapshot."""
    if explicit:
        norm = _normalize_target_type(explicit)
        if norm:
            return norm
    if not project_id:
        return "snowflake"

    # 1. Project YAML/config
    try:
        from semabridge.api.services.mapping_service import _compat_project_target_type

        cfg_target = _normalize_target_type(_compat_project_target_type(project_id))
        if cfg_target:
            return cfg_target
    except Exception as exc:
        logger.debug("Project YAML target_type lookup skipped for %s: %s", project_id, exc)

    # 2. Loaded project metadata
    try:
        from semabridge.api.services.project_shared import _compat_ensure_loaded, _compat_projects

        _compat_ensure_loaded()
        meta_target = _extract_target_type_from_meta(_compat_projects.get(project_id))
        if meta_target:
            return meta_target
    except Exception as exc:
        logger.debug("Project metadata target_type lookup skipped for %s: %s", project_id, exc)

    # 3. Latest SML snapshot metadata
    try:
        from semabridge.repository.model_repository import ModelRepository

        snaps = ModelRepository().list_snapshots(project_id, limit=1)
        if snaps:
            snap = snaps[0]
            blob = getattr(snap, "sml_blob", None) or {}
            if isinstance(blob, dict):
                snap_target = _extract_target_type_from_meta(blob)
                if not snap_target:
                    snap_target = _normalize_target_type(blob.get("adapter") or blob.get("platform"))
                if snap_target:
                    return snap_target
    except Exception as exc:
        logger.debug("Snapshot target_type lookup skipped for %s: %s", project_id, exc)

    return "snowflake"


def _extract_databricks_catalog_schema(
    project_id: Optional[str], dbx_cfg: Any
) -> Tuple[str, str]:
    """Resolve Databricks Unity Catalog catalog_name and schema_name from config, project metadata, or deployment info."""
    catalog = str(getattr(dbx_cfg, "catalog", None) or "").strip()
    schema = str(
        getattr(dbx_cfg, "schema_name", None) or getattr(dbx_cfg, "schema", None) or ""
    ).strip()

    if project_id and (not catalog or not schema):
        try:
            from semabridge.api.services.project_shared import _compat_ensure_loaded, _compat_projects

            _compat_ensure_loaded()
            meta = _compat_projects.get(project_id)
            if isinstance(meta, dict):
                target = meta.get("target")
                if not isinstance(target, dict):
                    targets = meta.get("targets")
                    target = targets[0] if isinstance(targets, list) and targets and isinstance(targets[0], dict) else {}
                catalog = catalog or str(target.get("catalog") or target.get("database") or meta.get("database") or "").strip()
                schema = schema or str(target.get("schema") or target.get("schema_name") or meta.get("schema") or "").strip()
        except Exception as exc:
            logger.debug("Project metadata catalog/schema lookup skipped for %s: %s", project_id, exc)

    if project_id and (not catalog or not schema):
        try:
            from semabridge.repository.model_repository import ModelRepository

            info = ModelRepository().get_project_deployment_info(project_id) or {}
            catalog = catalog or str(info.get("database") or "").strip()
            schema = schema or str(info.get("schema") or "").strip()
        except Exception as exc:
            logger.debug("Deployment info catalog/schema lookup skipped for %s: %s", project_id, exc)

    return catalog or "main", schema or "default"


def _view_tail(name: Optional[str]) -> str:
    return str(name or "").strip().split(".")[-1].strip('"`').casefold()


def _extract_expected_databricks_views(
    project_id: Optional[str], domain: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """Collect expected Databricks metric views & their columns/metrics from SML snapshots and ontology domains."""
    expected: Dict[str, Dict[str, Any]] = {}

    def _register_entry(
        view_name: str,
        col_name: str,
        expr: str = "",
        *,
        domain_name: str = "",
        domain_id: Optional[str] = None,
        model_id: Optional[str] = None,
        kind: str = "metric",
    ) -> None:
        vtail = _view_tail(view_name)
        if not vtail:
            return
        bucket = expected.setdefault(
            vtail,
            {
                "view_name": view_name,
                "domain": domain_name or view_name,
                "domain_id": domain_id,
                "model_id": model_id,
                "columns": {},
            },
        )
        cname = str(col_name or "").strip()
        if cname:
            bucket["columns"].setdefault(
                cname.upper(),
                {"name": cname, "expression": str(expr or "").strip(), "kind": kind},
            )

    # 1. Ontology domains for the project
    try:
        from semabridge.ontology.store import ontology_store

        fed = ontology_store.get_federated_ontology()
        candidates = ontology_store.domains_for(project_id) if project_id else list(fed.domains.values())
        for dom in candidates:
            if domain and dom.domain.lower() != domain.lower() and (dom.id or "").lower() != domain.lower():
                continue
            dom_view = getattr(dom, "semantic_view_name", None) or dom.domain
            vtail = _view_tail(dom_view)
            if vtail:
                expected.setdefault(
                    vtail,
                    {
                        "view_name": dom_view,
                        "domain": dom.domain,
                        "domain_id": dom.id,
                        "model_id": getattr(dom, "source_model_id", None),
                        "columns": {},
                    },
                )
            for m in getattr(dom, "metrics", None) or []:
                _register_entry(
                    dom_view,
                    m.name,
                    m.sql_expression or m.dax_expression or "",
                    domain_name=dom.domain,
                    domain_id=dom.id,
                    model_id=getattr(dom, "source_model_id", None),
                    kind="metric",
                )
    except Exception as exc:
        logger.warning("Databricks drift check ontology lookup failed for %s: %s", project_id, exc)

    # 2. Latest SML snapshot(s) from ModelRepository
    if project_id:
        try:
            from semabridge.repository.model_repository import ModelRepository

            snaps = ModelRepository().list_snapshots(project_id, limit=5)
            for snap in snaps:
                blob = getattr(snap, "sml_blob", None) or {}
                if not isinstance(blob, dict):
                    continue
                snap_view = (
                    getattr(snap, "view_name", None)
                    or blob.get("view_name")
                    or blob.get("unique_name")
                    or blob.get("label")
                    or blob.get("name")
                )

                # Explicit metric_views / views list if present in snapshot
                for mv in blob.get("metric_views") or blob.get("views") or []:
                    if not isinstance(mv, dict):
                        continue
                    mv_name = str(mv.get("full_name") or mv.get("view_name") or mv.get("name") or "").strip()
                    if not mv_name:
                        continue
                    if domain and _view_tail(mv_name) != domain.lower() and mv_name.lower() != domain.lower():
                        continue
                    vtail = _view_tail(mv_name)
                    expected.setdefault(
                        vtail,
                        {
                            "view_name": mv_name,
                            "domain": mv.get("domain") or mv_name,
                            "domain_id": None,
                            "model_id": getattr(snap, "snapshot_id", None),
                            "columns": {},
                        },
                    )
                    for col in mv.get("columns") or []:
                        cname = col.get("name") if isinstance(col, dict) else str(col)
                        cexpr = col.get("expression") or col.get("comment") or "" if isinstance(col, dict) else ""
                        _register_entry(mv_name, cname, cexpr, model_id=getattr(snap, "snapshot_id", None), kind="column")
                    for met in mv.get("metrics") or mv.get("measures") or []:
                        mname = met.get("name") or met.get("unique_name") if isinstance(met, dict) else str(met)
                        mexpr = met.get("sql_expression") or met.get("expression") or met.get("dax_expression") or "" if isinstance(met, dict) else ""
                        _register_entry(mv_name, mname, mexpr, model_id=getattr(snap, "snapshot_id", None), kind="metric")

                if snap_view:
                    snap_view_str = str(snap_view).strip()
                    if domain and _view_tail(snap_view_str) != domain.lower() and snap_view_str.lower() != domain.lower():
                        continue
                    vtail = _view_tail(snap_view_str)
                    expected.setdefault(
                        vtail,
                        {
                            "view_name": snap_view_str,
                            "domain": blob.get("label") or snap_view_str,
                            "domain_id": None,
                            "model_id": getattr(snap, "snapshot_id", None),
                            "columns": {},
                        },
                    )
                    for met in blob.get("metrics") or []:
                        if isinstance(met, dict):
                            mname = met.get("unique_name") or met.get("name") or met.get("label") or ""
                            mexpr = met.get("sql_expression") or met.get("expression") or met.get("dax_expression") or ""
                            _register_entry(
                                snap_view_str,
                                mname,
                                mexpr,
                                domain_name=blob.get("label") or snap_view_str,
                                model_id=getattr(snap, "snapshot_id", None),
                                kind="metric",
                            )
                    for ds in blob.get("datasets") or []:
                        if not isinstance(ds, dict):
                            continue
                        for col in ds.get("columns") or []:
                            if isinstance(col, dict):
                                cname = col.get("unique_name") or col.get("name") or ""
                                _register_entry(
                                    snap_view_str,
                                    cname,
                                    col.get("data_type") or "",
                                    domain_name=blob.get("label") or snap_view_str,
                                    model_id=getattr(snap, "snapshot_id", None),
                                    kind="column",
                                )
                # Only use the newest snapshot that defines views/metrics
                if expected:
                    break
        except Exception as exc:
            logger.warning("Databricks drift check snapshot lookup failed for %s: %s", project_id, exc)

    return expected


def _extract_secret(val: Any) -> str:
    if val is None:
        return ""
    if hasattr(val, "get_secret_value"):
        return str(val.get_secret_value() or "").strip()
    return str(val).strip()


def _run_databricks_drift_check(
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    webhook_url: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare expected SML snapshot / deployed metric views against live Databricks Unity Catalog views."""
    import requests as _requests

    from semabridge.ai.project_connection import resolve_project_warehouse_config
    from semabridge.utils.tls import requests_verify

    now = datetime.now(timezone.utc).isoformat()
    expected_views = _extract_expected_databricks_views(project_id, domain=domain)
    checked = sum(max(len(v["columns"]), 1) for v in expected_views.values())

    dbx_cfg = None
    try:
        dbx_cfg = resolve_project_warehouse_config(project_id, "DATABRICKS", owner_id=owner_id)
    except Exception as exc:
        logger.warning("Failed to resolve Databricks warehouse config for %s: %s", project_id, exc)

    catalog, schema = _extract_databricks_catalog_schema(project_id, dbx_cfg)
    host = str(getattr(dbx_cfg, "host", None) or "").strip().rstrip("/")
    if host.startswith("https://"):
        host = host[len("https://"):]
    elif host.startswith("http://"):
        host = host[len("http://"):]

    token = _extract_secret(getattr(dbx_cfg, "token", None)) or _extract_secret(
        getattr(dbx_cfg, "access_token", None)
    )
    client_id = str(getattr(dbx_cfg, "client_id", None) or "").strip()
    client_secret = _extract_secret(getattr(dbx_cfg, "client_secret", None))

    verify = requests_verify("Databricks Unity Catalog drift check")
    databricks_error: Optional[str] = None

    if host and not token and client_id and client_secret:
        try:
            oidc_resp = _requests.post(
                f"https://{host}/oidc/v1/token",
                data={"grant_type": "client_credentials", "scope": "all-apis"},
                auth=(client_id, client_secret),
                timeout=15,
                verify=verify,
            )
            if oidc_resp.status_code == 200:
                token = str((oidc_resp.json() or {}).get("access_token") or "").strip()
            else:
                databricks_error = f"Databricks OAuth token exchange failed (HTTP {oidc_resp.status_code})"
        except Exception as exc:
            logger.warning("Databricks OAuth token exchange error for %s: %s", project_id, exc)
            databricks_error = f"Databricks OAuth token exchange failed: {exc}"

    if not host or not token:
        databricks_error = databricks_error or "No Databricks host/token configured for project."
        state = _load_state()
        pending_detected = [
            e
            for e in state.get("events") or []
            if e.get("status") == "pending"
            and (
                not project_id
                or str(e.get("project_id", "")).lower().removeprefix("proj-")
                == str(project_id).lower().removeprefix("proj-")
            )
        ]
        return {
            "target_type": "databricks",
            "catalog": catalog,
            "schema": schema,
            "checked_metrics": checked,
            "checked_views": len(expected_views),
            "live_views_found": 0,
            "databricks_connected": False,
            "snowflake_connected": False,
            "drift_detected": len(pending_detected),
            "events": pending_detected,
            "error": databricks_error,
            "checked_at": now,
        }

    # Inspect live Unity Catalog tables/views via unified DatabricksRestClient
    from semabridge.ai.databricks_genie_service import create_databricks_rest_client
    from semabridge.api.services.databricks_connection_service import DatabricksAPIError

    live_views: Dict[str, Dict[str, Any]] = {}
    try:
        uc_client = create_databricks_rest_client(
            host,
            token,
            timeout=15.0,
            verify_override=verify,
            requests_mod=_requests,
            strip_default_accept=True,
        )
        tables, _ = uc_client.list_tables(catalog, schema)
        for tbl in tables:
            if not isinstance(tbl, dict):
                continue
            tname = str(tbl.get("name") or "").strip()
            fqn = str(tbl.get("full_name") or f"{catalog}.{schema}.{tname}").strip()
            ttype = str(tbl.get("table_type") or "").upper()
            tail = _view_tail(tname or fqn)
            if not tail:
                continue
            if "VIEW" not in ttype and "METRIC" not in ttype and tail not in expected_views:
                continue
            cols_map: Dict[str, Dict[str, str]] = {}
            for col in tbl.get("columns") or []:
                if not isinstance(col, dict):
                    continue
                cname = str(col.get("name") or "").strip()
                if cname:
                    cols_map[cname.upper()] = {
                        "name": cname,
                        "type": str(col.get("type_text") or col.get("type_name") or "").strip(),
                        "comment": str(col.get("comment") or "").strip(),
                    }
            live_views[tail] = {
                "name": tname or tail,
                "full_name": fqn,
                "table_type": ttype,
                "columns": cols_map,
            }
    except Exception as exc:
        err_msg = (
            f"Unity Catalog REST API returned HTTP {exc.status_code}: {exc.message}"
            if isinstance(exc, DatabricksAPIError) and exc.status_code
            else str(exc)
        )
        logger.warning("Databricks Unity Catalog drift inspection failed for %s: %s", project_id, err_msg)
        state = _load_state()
        pending_detected = [
            e
            for e in state.get("events") or []
            if e.get("status") == "pending"
            and (
                not project_id
                or str(e.get("project_id", "")).lower().removeprefix("proj-")
                == str(project_id).lower().removeprefix("proj-")
            )
        ]
        return {
            "target_type": "databricks",
            "catalog": catalog,
            "schema": schema,
            "checked_metrics": checked,
            "checked_views": len(expected_views),
            "live_views_found": 0,
            "databricks_connected": False,
            "snowflake_connected": False,
            "drift_detected": len(pending_detected),
            "events": pending_detected,
            "error": err_msg,
            "checked_at": now,
        }

    # Compare expected views/columns against live Unity Catalog views
    new_events: List[Dict[str, Any]] = []
    for vtail, exp_view in expected_views.items():
        exp_view_name = exp_view["view_name"]
        exp_fqn = exp_view_name if "." in exp_view_name else f"{catalog}.{schema}.{exp_view_name}"
        live = live_views.get(vtail)

        if live is None:
            new_events.append(
                {
                    "id": f"drift-{uuid.uuid4().hex[:10]}",
                    "project_id": project_id,
                    "target_type": "databricks",
                    "domain": exp_view["domain"],
                    "domain_id": exp_view.get("domain_id"),
                    "model_id": exp_view.get("model_id"),
                    "semantic_view": exp_fqn,
                    "metric_name": exp_view_name,
                    "drift_type": "MISSING_IN_DATABRICKS",
                    "badges": ["MISSING_IN_DATABRICKS", "VIEW_MISSING", "PENDING_APPROVAL"],
                    "story": f"Deployed Databricks Metric View '{exp_fqn}' was not found in Unity Catalog ({catalog}.{schema}).",
                    "bi_definition": f"Expected Metric View: {exp_fqn} ({len(exp_view['columns'])} expected column(s))",
                    "snowflake_definition": "(not present in Databricks Unity Catalog)",
                    "databricks_definition": "(not present in Databricks Unity Catalog)",
                    "proposed_snowflake_definition": f"CREATE OR REPLACE VIEW {exp_fqn} -- redeploy from SML snapshot",
                    "proposed_databricks_definition": f"CREATE OR REPLACE VIEW {exp_fqn} -- redeploy from SML snapshot",
                    "what_changed": [f"Metric view '{exp_fqn}' is missing from Unity Catalog {catalog}.{schema}"],
                    "affected_objects": [exp_fqn],
                    "status": "pending",
                    "resolution_reason": "",
                    "detected_at": now,
                }
            )
            continue

        live_fqn = live["full_name"]
        live_cols = live["columns"]
        exp_cols = exp_view["columns"]

        # Removed / missing columns or metrics
        for col_key, exp_col in exp_cols.items():
            if col_key not in live_cols:
                expr = exp_col.get("expression") or exp_col["name"]
                new_events.append(
                    {
                        "id": f"drift-{uuid.uuid4().hex[:10]}",
                        "project_id": project_id,
                        "target_type": "databricks",
                        "domain": exp_view["domain"],
                        "domain_id": exp_view.get("domain_id"),
                        "model_id": exp_view.get("model_id"),
                        "semantic_view": live_fqn,
                        "metric_name": exp_col["name"],
                        "drift_type": "MISSING_IN_DATABRICKS",
                        "badges": ["MISSING_IN_DATABRICKS", "COLUMN_REMOVED", "PENDING_APPROVAL"],
                        "story": f"Expected column/metric '{exp_col['name']}' is missing from Databricks Metric View '{live_fqn}'.",
                        "bi_definition": expr,
                        "snowflake_definition": "(not present in Databricks metric view)",
                        "databricks_definition": "(not present in Databricks metric view)",
                        "proposed_snowflake_definition": expr,
                        "proposed_databricks_definition": expr,
                        "what_changed": [f"Column/metric '{exp_col['name']}' removed or missing in '{live_fqn}'"],
                        "affected_objects": [live_fqn, exp_col["name"]],
                        "status": "pending",
                        "resolution_reason": "",
                        "detected_at": now,
                    }
                )

        # Added / unexpected columns in live Unity Catalog view
        if exp_cols:
            for col_key, live_col in live_cols.items():
                if col_key not in exp_cols:
                    col_sig = f"{live_col['name']} ({live_col['type']})" if live_col.get("type") else live_col["name"]
                    new_events.append(
                        {
                            "id": f"drift-{uuid.uuid4().hex[:10]}",
                            "project_id": project_id,
                            "target_type": "databricks",
                            "domain": exp_view["domain"],
                            "domain_id": exp_view.get("domain_id"),
                            "model_id": exp_view.get("model_id"),
                            "semantic_view": live_fqn,
                            "metric_name": live_col["name"],
                            "drift_type": "EXTRA_IN_DATABRICKS",
                            "badges": ["EXTRA_IN_DATABRICKS", "COLUMN_ADDED", "PENDING_APPROVAL"],
                            "story": f"Unexpected column '{live_col['name']}' exists in live Databricks Metric View '{live_fqn}' but not in the latest SML snapshot.",
                            "bi_definition": "(not present in SML snapshot)",
                            "snowflake_definition": col_sig,
                            "databricks_definition": col_sig,
                            "proposed_snowflake_definition": "",
                            "proposed_databricks_definition": "",
                            "what_changed": [f"Column '{live_col['name']}' added directly in Unity Catalog view '{live_fqn}'"],
                            "affected_objects": [live_fqn, live_col["name"]],
                            "status": "pending",
                            "resolution_reason": "",
                            "detected_at": now,
                        }
                    )

    state = _load_state()
    event_map = {}
    for e in state.get("events") or []:
        event_map[drift_event_key(e)] = e
    for ev in new_events:
        key = drift_event_key(ev)
        prev = event_map.get(key)
        if (
            prev
            and prev.get("bi_definition") == ev.get("bi_definition")
            and prev.get("status") in ("approved", "ignored")
        ):
            continue
        event_map[key] = ev
    state["events"] = list(event_map.values())[:200]
    _save_state(state)

    if new_events and webhook_url:
        _send_webhook(
            webhook_url,
            {
                "text": f"SemaBridge Databricks Drift Alert: {len(new_events)} drift item(s) detected.",
                "project_id": project_id,
                "target_type": "databricks",
                "events": new_events[:5],
            },
        )

    pending_detected = [
        e
        for e in state.get("events") or []
        if e.get("status") == "pending"
        and (
            not project_id
            or str(e.get("project_id", "")).lower().removeprefix("proj-")
            == str(project_id).lower().removeprefix("proj-")
        )
    ]
    detected_count = len(new_events) if new_events else len(pending_detected)

    return {
        "target_type": "databricks",
        "catalog": catalog,
        "schema": schema,
        "checked_metrics": checked,
        "checked_views": len(expected_views),
        "live_views_found": len(live_views),
        "databricks_connected": True,
        "snowflake_connected": False,
        "drift_detected": detected_count,
        "events": new_events or pending_detected,
        "checked_at": now,
    }


@router.post("/check", response_model=Dict[str, Any])
def trigger_drift_check(req: DriftCheckRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    owner_id = str(uid) if uid else None
    target_type = _resolve_project_target_type(req.project_id, explicit=req.target_type)
    if target_type == "databricks":
        return _run_databricks_drift_check(
            project_id=req.project_id,
            domain=req.domain,
            webhook_url=req.webhook_url,
            owner_id=owner_id,
        )
    result = run_drift_check(
        project_id=req.project_id,
        domain=req.domain,
        webhook_url=req.webhook_url,
        owner_id=owner_id,
    )
    if isinstance(result, dict):
        result.setdefault("target_type", target_type)
    return result


@router.get("/events", response_model=Dict[str, Any])
def get_drift_events(project_id: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    events = list_drift_events(project_id=project_id, status=status)
    return {"count": len(events), "events": events}


@router.delete("/events", response_model=Dict[str, Any])
def delete_drift_events(project_id: Optional[str] = None) -> Dict[str, Any]:
    cleared = clear_drift_events(project_id=project_id)
    return {"success": True, "cleared_count": cleared}


@router.post("/events/refresh", response_model=Dict[str, Any])
def refresh_drift_events(request: Request, project_id: str) -> Dict[str, Any]:
    """"Refresh drift logs": runs real drift detection for the project and
    returns its events. Never adds demo or seeded events (no env switch can
    turn that on); a failed check is a 400 with the reason, not an empty list."""
    pid = str(project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="project_id is required to refresh drift events.")
    uid = getattr(request.state, "user_id", None)
    try:
        check = run_drift_check(project_id=pid, owner_id=str(uid) if uid else None)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Drift check failed for project '{pid}': {exc}") from exc
    events = list_drift_events(project_id=pid)
    return {"success": True, "count": len(events), "events": events, "check": check}


@router.post("/events/seed-mock", response_model=Dict[str, Any])
def seed_mock_events(request: Request, project_id: Optional[str] = "proj-comp") -> Dict[str, Any]:
    """"Refresh drift logs": always runs real drift detection for the
    project; demo events are layered on top only when demo/mock-drift mode
    is enabled (SEMABRIDGE_DEMO_MODE / SEMABRIDGE_MOCK_DRIFT). Demo events
    never replace or delete real ones."""
    from semabridge.core.demo_mode import demo_mode_globally_armed as is_demo_mode_enabled
    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

    pid = project_id or "proj-comp"
    uid = getattr(request.state, "user_id", None)
    owner_id = str(uid) if uid else None
    target_type = _resolve_project_target_type(pid)
    check: Dict[str, Any] = {}
    try:
        if target_type == "databricks":
            check = _run_databricks_drift_check(project_id=pid, owner_id=owner_id)
        else:
            check = run_drift_check(project_id=pid, owner_id=owner_id)
            if isinstance(check, dict):
                check.setdefault("target_type", target_type)
    except Exception as exc:
        logger.warning("Drift check during log refresh failed for %s: %s", pid, exc)
        check = {"error": str(exc), "target_type": target_type}
    demo = is_mock_drift_enabled() or is_demo_mode_enabled()
    if demo:
        seed_mock_drift_events(pid)
    events = list_drift_events(project_id=pid)
    return {
        "success": True,
        "target_type": target_type,
        "count": len(events),
        "events": events,
        "demo_events": demo,
        "check": check,
    }


@router.post("/simulate-change", response_model=Dict[str, Any])
def simulate_change(req: SimulateMetricChangeRequest) -> Dict[str, Any]:
    try:
        res = simulate_metric_drift(
            project_id=req.project_id,
            metric_name=req.metric_name,
            new_expression=req.new_expression,
        )
        return {"success": True, **res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/reset-change", response_model=Dict[str, Any])
def reset_change(req: ResetMetricChangeRequest) -> Dict[str, Any]:
    try:
        res = reset_simulated_metric_drift(
            project_id=req.project_id,
            metric_name=req.metric_name,
        )
        return {"success": True, **res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/events/{event_id}/approve", response_model=Dict[str, Any])
def approve_drift(event_id: str, req: DriftResolutionRequest) -> Dict[str, Any]:
    ev = resolve_drift_event(event_id, "approve", req.reason)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
    pid = ev.get("project_id")
    if pid:
        try:
            import asyncio
            import threading
            from semabridge.api.services.run_service import _execute_project_run

            def _run_approved():
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(_execute_project_run(pid, schedule_label="Approved Sync"))
                except Exception as exc:
                    logger.warning("Approved sync run failed for %s: %s", pid, exc)
                finally:
                    loop.close()

            threading.Thread(target=_run_approved, daemon=True).start()
        except Exception as exc:
            logger.warning("Failed to launch approved sync thread for %s: %s", pid, exc)
    return {"success": True, "event": ev}


@router.post("/events/{event_id}/ignore", response_model=Dict[str, Any])
def ignore_drift(event_id: str, req: DriftResolutionRequest) -> Dict[str, Any]:
    ev = resolve_drift_event(event_id, "ignore", req.reason)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
    return {"success": True, "event": ev}


@router.get("/schedules", response_model=Dict[str, Any])
def get_schedules(project_id: Optional[str] = None) -> Dict[str, Any]:
    scheds = list_schedules(project_id=project_id)
    return {"count": len(scheds), "schedules": scheds}


@router.post("/schedules", response_model=Dict[str, Any])
def add_schedule(req: CreateScheduleRequest) -> Dict[str, Any]:
    return {"success": True, "schedule": create_schedule(**req.model_dump())}


@router.delete("/schedules/{schedule_id}", response_model=Dict[str, Any])
def remove_schedule(schedule_id: str) -> Dict[str, Any]:
    ok = delete_schedule(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"success": True}
