"""REST API Router for Scheduled Match & Drift Detection with Approval Workflow."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from semabridge.governance.drift_service import (
    create_schedule,
    delete_schedule,
    list_drift_events,
    list_schedules,
    resolve_drift_event,
    run_drift_check,
)

router = APIRouter(prefix="/api/drift", tags=["drift_monitoring"])


class DriftCheckRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    webhook_url: Optional[str] = None


class DriftResolutionRequest(BaseModel):
    reason: str = ""


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


@router.post("/events/{event_id}/approve", response_model=Dict[str, Any])
def approve_drift(event_id: str, req: DriftResolutionRequest) -> Dict[str, Any]:
    ev = resolve_drift_event(event_id, "approve", req.reason)
    if not ev:
        raise HTTPException(status_code=404, detail="Drift event not found.")
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

