"""REST API Router for Scheduled Match & Drift Detection with Approval Workflow."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger("semabridge.api.routers.drift_router")

from semabridge.governance.drift_service import (
    bulk_resolve_drift_events,
    clear_drift_events,
    create_schedule,
    delete_schedule,
    get_project_drift_target_info,
    list_drift_events,
    list_schedules,
    resolve_drift_event,
    run_drift_check,
)
from semabridge.reconciliation.mock_parity import is_mock_drift_enabled


def _require_mock_drift() -> None:
    if not is_mock_drift_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


def _find_drift_event(event_id: str) -> Dict[str, Any]:
    from semabridge.governance.drift_service import _load_state
    state = _load_state()
    for ev in state.get("events") or []:
        if ev.get("id") == event_id:
            return ev
    raise HTTPException(status_code=404, detail="Drift event not found.")


def _enforce_drift_permission(request: Request, project_id: Optional[str] = None, event_or_object: Any = None) -> None:
    from semabridge.governance.drift.project_scope import verify_drift_project_access
    verify_drift_project_access(
        project_id,
        event_or_object=event_or_object,
        request=request,
        enforce_mutation=True,
        allow_missing_project=bool(project_id is None),
        raise_on_error=True,
    )


router = APIRouter(prefix="/api/drift", tags=["drift_monitoring"])


class DriftCheckRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    webhook_url: Optional[str] = None
    view: Optional[str] = None
    mode: Optional[str] = "live"
    force_refresh: Optional[bool] = False


class DriftResolutionRequest(BaseModel):
    reason: str = ""


class BulkDriftResolutionRequest(BaseModel):
    project_id: Optional[str] = None
    view: Optional[str] = None
    kind: Optional[str] = None
    category: Optional[str] = None
    event_ids: Optional[List[str]] = None
    action: str = "ignore"
    reason: Optional[str] = ""


class DriftActionRequest(BaseModel):
    action: str
    reason: Optional[str] = ""


class SimulateMetricChangeRequest(BaseModel):
    project_id: str
    metric_name: Optional[str] = None
    new_expression: Optional[str] = None


class ResetMetricChangeRequest(BaseModel):
    project_id: str
    metric_name: Optional[str] = None


class CreateScheduleRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    interval_minutes: int = 60
    webhook_url: Optional[str] = None
    job_type: str = "drift_and_parity"


@router.post("/check", response_model=Dict[str, Any])
def trigger_drift_check(req: DriftCheckRequest, request: Request) -> Dict[str, Any]:
    _enforce_drift_permission(request, req.project_id)
    uid = getattr(request.state, "user_id", None)
    view_to_check = req.view
    if req.project_id and view_to_check:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        try:
            view_to_check = repo.validate_project_view(req.project_id, view_to_check)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    try:
        return run_drift_check(
            project_id=req.project_id,
            domain=req.domain,
            webhook_url=req.webhook_url,
            owner_id=str(uid) if uid else None,
            view=view_to_check,
            mode=req.mode or "live",
            force_refresh=bool(req.force_refresh),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/events", response_model=Dict[str, Any])
def get_drift_events(
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    category: Optional[str] = None,
    kind: Optional[str] = None,
    search: Optional[str] = None,
    sort: Optional[str] = None,
    view: Optional[str] = None,
    mode: Optional[str] = None,
    page: Optional[int] = None,
    page_size: Optional[int] = None,
) -> Dict[str, Any]:
    resolved_view = None
    if project_id:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        if view:
            try:
                resolved_view = repo.validate_project_view(project_id, view)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        if not resolved_view:
            deployments = repo.list_project_deployments(project_id, include_unmanaged=True)
            latest = next((d for d in deployments if d.get("is_latest")), None)
            resolved_view = latest["semantic_view"] if latest else None

    # Load all events for this project to compute accurate aggregate counts across filters
    events = list_drift_events(project_id=project_id, status=None)
    if mode:
        events = [e for e in events if e.get("mode") == mode]
    elif not is_mock_drift_enabled():
        events = [e for e in events if not e.get("mock") and e.get("mode") != "mock"]

    if resolved_view:
        v_tail = resolved_view.strip().split(".")[-1].strip('"').lower()
        events = [
            e for e in events
            if not e.get("semantic_view") or str(e["semantic_view"]).strip().split(".")[-1].strip('"').lower() == v_tail
        ]

    total_unfiltered_count = len(events)
    pending_count = sum(1 for e in events if str(e.get("status") or "pending").strip().lower() == "pending")

    # 1. Apply search filter first if present so all breakdowns respect search query
    search_events = list(events)
    if search and search.strip():
        q = search.strip().lower()
        search_events = [
            e for e in search_events
            if q in str(e.get("metric_name") or "").lower()
            or q in str(e.get("object_name") or "").lower()
            or q in str(e.get("table") or "").lower()
            or q in str(e.get("category") or "").lower()
            or q in str(e.get("kind") or "").lower()
            or q in str(e.get("expression") or "").lower()
            or q in str(e.get("proposed_action") or "").lower()
        ]

    # 2. Status chips breakdown: computed within current category and kind filters
    # Exception to THE RULE: Status chips show the breakdown across statuses within current category/kind
    status_scope_events = list(search_events)
    if category and category.strip().upper() not in ("ALL", ""):
        cat_filters = {c.strip().upper() for c in category.split(",") if c.strip() and c.strip().upper() != "ALL"}
        if cat_filters:
            status_scope_events = [
                e for e in status_scope_events
                if str(e.get("category") or e.get("drift_type") or "").strip().upper() in cat_filters
            ]
    if kind and kind.strip().upper() not in ("ALL", ""):
        k_filters = {k.strip().upper() for k in kind.split(",") if k.strip() and k.strip().upper() != "ALL"}
        if k_filters:
            status_scope_events = [
                e for e in status_scope_events
                if str(e.get("kind") or "METRIC").strip().upper() in k_filters
            ]

    counts_by_status: Dict[str, int] = {
        "pending": 0,
        "approved": 0,
        "accepted": 0,
        "ignored": 0,
        "resolved": 0,
    }
    for e in status_scope_events:
        s = str(e.get("status") or "pending").strip().lower()
        counts_by_status[s] = counts_by_status.get(s, 0) + 1
    counts_by_status["all"] = len(status_scope_events)

    # 3. Category and Kind breakdown: computed strictly within the active status filter
    cat_scope_events = list(search_events)
    if status and status.strip().lower() not in ("all", ""):
        st_filters = {s.strip().lower() for s in status.split(",") if s.strip() and s.strip().lower() != "all"}
        if st_filters:
            cat_scope_events = [
                e for e in cat_scope_events
                if str(e.get("status") or "pending").strip().lower() in st_filters
            ]

    counts_by_category: Dict[str, int] = {}
    counts_by_kind: Dict[str, int] = {}
    for e in cat_scope_events:
        c = str(e.get("category") or e.get("drift_type") or "UNKNOWN").strip().upper()
        counts_by_category[c] = counts_by_category.get(c, 0) + 1
        k = str(e.get("kind") or "METRIC").strip().upper()
        counts_by_kind[k] = counts_by_kind.get(k, 0) + 1

    # 4. Final filtered events for display & pagination (status + category + kind + search)
    filtered = list(cat_scope_events)
    if category and category.strip().upper() not in ("ALL", ""):
        cat_filters = {c.strip().upper() for c in category.split(",") if c.strip() and c.strip().upper() != "ALL"}
        if cat_filters:
            filtered = [
                e for e in filtered
                if str(e.get("category") or e.get("drift_type") or "").strip().upper() in cat_filters
            ]

    if kind and kind.strip().upper() not in ("ALL", ""):
        k_filters = {k.strip().upper() for k in kind.split(",") if k.strip() and k.strip().upper() != "ALL"}
        if k_filters:
            filtered = [
                e for e in filtered
                if str(e.get("kind") or "METRIC").strip().upper() in k_filters
            ]

    # Apply sorting
    sort_key = (sort or "newest").lower()
    if sort_key == "oldest":
        filtered.sort(key=lambda x: str(x.get("detected_at") or x.get("last_seen_at") or ""))
    elif sort_key == "name_asc":
        filtered.sort(key=lambda x: str(x.get("object_name") or x.get("metric_name") or "").lower())
    elif sort_key == "name_desc":
        filtered.sort(key=lambda x: str(x.get("object_name") or x.get("metric_name") or "").lower(), reverse=True)
    elif sort_key == "category":
        filtered.sort(key=lambda x: (str(x.get("category") or "").lower(), str(x.get("object_name") or x.get("metric_name") or "").lower()))
    elif sort_key == "kind":
        filtered.sort(key=lambda x: (str(x.get("kind") or "").lower(), str(x.get("object_name") or x.get("metric_name") or "").lower()))
    else:  # newest default
        filtered.sort(key=lambda x: str(x.get("last_seen_at") or x.get("detected_at") or ""), reverse=True)

    total_count = len(filtered)
    paged_events = filtered
    total_pages = 1
    if page is not None and page_size is not None and page_size > 0:
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        start = max(0, (page - 1) * page_size)
        paged_events = filtered[start : start + page_size]

    target_info = get_project_drift_target_info(project_id, view=resolved_view) if project_id else {}
    from semabridge.governance.drift_service import _load_state
    state = _load_state()
    last_checks = dict(state.get("last_checks") or {})
    return {
        "count": len(paged_events),
        "total_count": total_count,
        "total_unfiltered_count": total_unfiltered_count,
        "pending_count": pending_count,
        "counts_by_category": counts_by_category,
        "counts_by_kind": counts_by_kind,
        "counts_by_status": counts_by_status,
        "page": page or 1,
        "page_size": page_size or (len(paged_events) if paged_events else 25),
        "total_pages": total_pages,
        "events": paged_events,
        "views_checked": target_info.get("views_checked", []),
        "checked_at": target_info.get("checked_at"),
        "status": target_info.get("status"),
        "reason": target_info.get("reason"),
        "reason_code": target_info.get("reason_code"),
        "not_checked_reason": target_info.get("not_checked_reason"),
        "proposed_action": target_info.get("proposed_action"),
        "platform": target_info.get("platform"),
        "baseline_source": target_info.get("baseline_source"),
        "baseline_at": target_info.get("baseline_at"),
        "metrics_compared": target_info.get("metrics_compared", 0),
        "last_checks": last_checks,
        "view": resolved_view,
        "semantic_view": resolved_view,
    }


@router.post("/events/bulk-resolve", response_model=Dict[str, Any])
def bulk_resolve_drift(req: BulkDriftResolutionRequest, request: Request) -> Dict[str, Any]:
    from semabridge.api.services.project_ownership_service import (
        auth_is_enabled,
        is_project_owned_by_user,
    )
    role = getattr(request.state, "user_role", None)
    if auth_is_enabled() and role and str(role).strip().lower() in ("viewer", "read_only", "guest"):
        raise HTTPException(
            status_code=403,
            detail="Forbidden: caller role is not permitted to perform drift actions.",
        )

    if req.project_id:
        _enforce_drift_permission(request, req.project_id)
    elif req.event_ids is None:
        _enforce_drift_permission(request, None)

    allowed_event_ids: Optional[List[str]] = None
    if req.event_ids is not None:
        from semabridge.governance.drift_service import _load_state
        state = _load_state()
        all_events = {ev.get("id"): ev for ev in (state.get("events") or []) if ev.get("id")}
        uid = getattr(request.state, "user_id", None)
        allowed_event_ids = []
        for eid in req.event_ids:
            ev = all_events.get(eid)
            if not ev:
                continue
            ev_pid = ev.get("project_id")
            if not auth_is_enabled() or is_project_owned_by_user(ev_pid, str(uid) if uid else None, log_denied=False):
                allowed_event_ids.append(eid)
            # foreign events outside caller's project are skipped and left untouched

    resolved = bulk_resolve_drift_events(
        project_id=req.project_id,
        view=req.view,
        kind=req.kind,
        category=req.category,
        event_ids=allowed_event_ids if req.event_ids is not None else None,
        action=req.action,
        reason=req.reason or "",
    )
    return resolved


@router.delete("/events", response_model=Dict[str, Any])
def delete_drift_events(request: Request, project_id: Optional[str] = None) -> Dict[str, Any]:
    if not project_id or not str(project_id).strip():
        raise HTTPException(
            status_code=400,
            detail="Missing required query parameter: 'project_id'. Deleting all events across projects is not permitted.",
        )
    pid = str(project_id).strip()
    _enforce_drift_permission(request, pid)
    cleared = clear_drift_events(project_id=pid)
    return {"success": True, "cleared_count": cleared}


@router.post("/events/seed-mock", dependencies=[Depends(_require_mock_drift)], response_model=Dict[str, Any])
def seed_mock_events(request: Request, project_id: Optional[str] = None) -> Dict[str, Any]:
    """"Refresh drift logs": runs real drift detection and seeds demo events
    only into the configured demo project when mock drift is enabled."""
    from semabridge.demo.drift_fixtures import (
        get_configured_demo_project_id,
        seed_mock_drift_events,
    )

    pid = (project_id or "").strip() or None
    uid = getattr(request.state, "user_id", None)
    check: Dict[str, Any] = {}
    try:
        check = run_drift_check(project_id=pid, owner_id=str(uid) if uid else None)
    except Exception as exc:
        check = {"error": str(exc)}
    demo = is_mock_drift_enabled()
    demo_pid = get_configured_demo_project_id()
    if demo and demo_pid:
        seed_mock_drift_events(demo_pid)
    events = list_drift_events(project_id=pid)
    target_info = get_project_drift_target_info(pid) if pid else {}
    return {
        "success": True,
        "count": len(events),
        "events": events,
        "demo_events": demo,
        "check": check,
        "views_checked": target_info.get("views_checked", []) or check.get("views_checked", []),
        "checked_at": target_info.get("checked_at") or check.get("checked_at"),
    }


@router.post("/events/cleanup-mock", dependencies=[Depends(_require_mock_drift)], response_model=Dict[str, Any])
def cleanup_mock_events_endpoint(demo_project_id: Optional[str] = None) -> Dict[str, Any]:
    from semabridge.demo.drift_fixtures import cleanup_mock_drift_events
    removed = cleanup_mock_drift_events(demo_project_id=demo_project_id)
    return {"success": True, "removed_per_project": removed}


@router.post("/simulate-change", dependencies=[Depends(_require_mock_drift)], response_model=Dict[str, Any])
def simulate_change(req: SimulateMetricChangeRequest) -> Dict[str, Any]:
    from semabridge.demo.drift_fixtures import simulate_metric_drift
    try:
        res = simulate_metric_drift(
            project_id=req.project_id,
            metric_name=req.metric_name,
            new_expression=req.new_expression,
        )
        return {"success": True, **res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/reset-change", dependencies=[Depends(_require_mock_drift)], response_model=Dict[str, Any])
def reset_change(req: ResetMetricChangeRequest) -> Dict[str, Any]:
    from semabridge.demo.drift_fixtures import reset_simulated_metric_drift
    try:
        res = reset_simulated_metric_drift(
            project_id=req.project_id,
            metric_name=req.metric_name,
        )
        return {"success": True, **res}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/events/{event_id}/approve", response_model=Dict[str, Any])
def approve_drift(event_id: str, req: DriftResolutionRequest, request: Request) -> Dict[str, Any]:
    ev_existing = _find_drift_event(event_id)
    pid = ev_existing.get("project_id")
    _enforce_drift_permission(request, pid, event_or_object=ev_existing)
    ev = resolve_drift_event(event_id, "approve", req.reason, project_id=pid)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
    # For mock events, return success without triggering a sync
    if ev.get("mock"):
        return {"success": True, "event": ev}
    if pid:
        try:
            from semabridge.ontology.store import ontology_store
            from semabridge.api.services.project_shared import resolve_canonical_project_id
            from semabridge.governance.drift_service import sync_domain_baselines
            c_pid = resolve_canonical_project_id(pid) or pid
            doms = ontology_store.domains_for(c_pid) or ontology_store.domains_for(pid)
            if doms:
                sync_domain_baselines(doms[0])
        except Exception:
            pass
        try:
            import asyncio
            import logging
            import threading
            from semabridge.api.services.run_service import _execute_project_run
            def _run_approved():
                loop = asyncio.new_event_loop()
                try:
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(_execute_project_run(pid, schedule_label="Approved Sync"))
                except Exception as exc:
                    logging.getLogger(__name__).warning("Approved sync run failed for %s: %s", pid, exc)
                finally:
                    loop.close()
            threading.Thread(target=_run_approved, daemon=True).start()
        except Exception:
            pass
    return {"success": True, "event": ev}


@router.post("/events/{event_id}/ignore", response_model=Dict[str, Any])
def ignore_drift(event_id: str, req: DriftResolutionRequest, request: Request) -> Dict[str, Any]:
    ev_existing = _find_drift_event(event_id)
    pid = ev_existing.get("project_id")
    _enforce_drift_permission(request, pid, event_or_object=ev_existing)
    ev = resolve_drift_event(event_id, "ignore", req.reason, project_id=pid)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
    return {"success": True, "event": ev}


@router.post("/events/{event_id}/accept", response_model=Dict[str, Any])
def accept_drift(event_id: str, req: DriftResolutionRequest, request: Request) -> Dict[str, Any]:
    ev_existing = _find_drift_event(event_id)
    pid = ev_existing.get("project_id")
    _enforce_drift_permission(request, pid, event_or_object=ev_existing)
    ev = resolve_drift_event(event_id, "accept", req.reason, project_id=pid)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
    return {"success": True, "event": ev}


@router.post("/events/{event_id}/action", response_model=Dict[str, Any])
def action_drift(event_id: str, req: DriftActionRequest, request: Request) -> Dict[str, Any]:
    ev_existing = _find_drift_event(event_id)
    pid = ev_existing.get("project_id")
    _enforce_drift_permission(request, pid, event_or_object=ev_existing)
    act = (req.action or "").lower().strip()
    if act in ("restore", "approve"):
        return approve_drift(event_id, DriftResolutionRequest(reason=req.reason or ""), request)
    elif act == "accept":
        return accept_drift(event_id, DriftResolutionRequest(reason=req.reason or ""), request)
    elif act == "ignore":
        return ignore_drift(event_id, DriftResolutionRequest(reason=req.reason or ""), request)
    else:
        ev = resolve_drift_event(event_id, act, req.reason or "", project_id=pid)
        if not ev:
            raise HTTPException(status_code=404, detail="Drift event not found.")
        return {"success": True, "event": ev}


@router.get("/schedules", response_model=Dict[str, Any])
def get_schedules(request: Request, project_id: Optional[str] = None) -> Dict[str, Any]:
    if project_id:
        _enforce_drift_permission(request, project_id)
    scheds = list_schedules(project_id=project_id)
    return {"count": len(scheds), "schedules": scheds}


@router.post("/schedules", response_model=Dict[str, Any])
def add_schedule(req: CreateScheduleRequest, request: Request) -> Dict[str, Any]:
    if req.project_id:
        _enforce_drift_permission(request, req.project_id)
    try:
        return {"success": True, "schedule": create_schedule(**req.model_dump())}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/schedules/{schedule_id}", response_model=Dict[str, Any])
def remove_schedule(schedule_id: str, request: Request) -> Dict[str, Any]:
    sched = next((s for s in list_schedules() if s.get("id") == schedule_id), None)
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    pid = sched.get("project_id")
    if pid:
        _enforce_drift_permission(request, pid)
    ok = delete_schedule(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found.")
    return {"success": True}


@router.get("/capabilities", response_model=Dict[str, Any])
def get_drift_capabilities() -> Dict[str, Any]:
    """Report drift monitoring capabilities (supported platforms, mock allowed, schedule minimum interval)."""
    from semabridge.governance.drift.adapters.registry import (
        is_mock_allowed,
        list_supported_platforms,
    )
    return {
        "supported_platforms": list_supported_platforms(),
        "mock_allowed": is_mock_allowed(),
        "min_schedule_interval_minutes": 15,
        "categories": [
            "ADDITION",
            "DELETION",
            "EXPRESSION_DRIFT",
            "DESCRIPTION_DRIFT",
        ],
    }


class AIImpactCheckRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    object_name: Optional[str] = None
    metric_name: Optional[str] = None
    kind: Optional[str] = "METRIC"
    table: Optional[str] = None
    project_id: Optional[str] = None
    view_name: Optional[str] = None
    baseline_description: Optional[str] = None
    live_description: Optional[str] = None
    questions: Optional[List[str]] = None
    max_questions: Optional[int] = None


@router.post("/ai-impact-check", response_model=Dict[str, Any])
def perform_ai_impact_check(req: AIImpactCheckRequest) -> Dict[str, Any]:
    """Run simulated questions against baseline vs live description without writing to warehouse."""
    from semabridge.governance.ai_impact_service import run_ai_impact_check
    target = req.object_name or req.metric_name
    if not target or not target.strip():
        raise HTTPException(status_code=400, detail="Object or metric name must be provided.")
    try:
        report = run_ai_impact_check(
            object_name=target.strip(),
            kind=req.kind or "METRIC",
            table=req.table,
            project_id=req.project_id,
            view_name=req.view_name,
            baseline_description=req.baseline_description,
            live_description=req.live_description,
            custom_questions=req.questions,
            max_questions=req.max_questions,
        )
        return report.model_dump()
    except Exception as exc:
        logger.error("AI impact check failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

