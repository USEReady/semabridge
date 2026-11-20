"""REST API Router for Scheduled Match & Drift Detection with Approval Workflow."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from semabridge.governance.drift_service import (
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


router = APIRouter(prefix="/api/drift", tags=["drift_monitoring"])


class DriftCheckRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    webhook_url: Optional[str] = None


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


@router.post("/check", response_model=Dict[str, Any])
def trigger_drift_check(req: DriftCheckRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    return run_drift_check(
        project_id=req.project_id,
        domain=req.domain,
        webhook_url=req.webhook_url,
        owner_id=str(uid) if uid else None,
    )


@router.get("/events", response_model=Dict[str, Any])
def get_drift_events(project_id: Optional[str] = None, status: Optional[str] = None) -> Dict[str, Any]:
    events = list_drift_events(project_id=project_id, status=status)
    if not is_mock_drift_enabled():
        events = [e for e in events if not e.get("mock")]
    target_info = get_project_drift_target_info(project_id) if project_id else {}
    return {
        "count": len(events),
        "events": events,
        "views_checked": target_info.get("views_checked", []),
        "checked_at": target_info.get("checked_at"),
    }


@router.delete("/events", response_model=Dict[str, Any])
def delete_drift_events(project_id: Optional[str] = None) -> Dict[str, Any]:
    cleared = clear_drift_events(project_id=project_id)
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
def approve_drift(event_id: str, req: DriftResolutionRequest) -> Dict[str, Any]:
    ev = resolve_drift_event(event_id, "approve", req.reason)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
    # For mock events, return success without triggering a sync
    if ev.get("mock"):
        return {"success": True, "event": ev}
    pid = ev.get("project_id")
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

