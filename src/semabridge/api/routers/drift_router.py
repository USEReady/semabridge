"""REST API Router for Scheduled Match & Drift Detection with Approval Workflow."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from semabridge.governance.drift_service import (
    clear_drift_events,
    create_schedule,
    delete_schedule,
    list_drift_events,
    list_schedules,
    reset_simulated_metric_drift,
    resolve_drift_event,
    run_drift_check,
    seed_mock_drift_events,
    simulate_metric_drift,
)

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
    return {"count": len(events), "events": events}


@router.delete("/events", response_model=Dict[str, Any])
def delete_drift_events(project_id: Optional[str] = None) -> Dict[str, Any]:
    cleared = clear_drift_events(project_id=project_id)
    return {"success": True, "cleared_count": cleared}


@router.post("/events/seed-mock", response_model=Dict[str, Any])
def seed_mock_events(project_id: Optional[str] = "proj-tiral_comp") -> Dict[str, Any]:
    events = seed_mock_drift_events(project_id or "proj-tiral_comp")
    return {"success": True, "count": len(events), "events": events}


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

