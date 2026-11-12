"""REST API Router for BI-Truth Test Suite."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from semabridge.ai.bi_truth_service import (
    generate_golden_questions_from_pbix_dir,
    generate_truth_cases,
    list_truth_cases,
    list_truth_runs,
    run_truth_suite,
    set_truth_case_status,
)

router = APIRouter(prefix="/api/bi-truth", tags=["bi_truth_suite"])


class GenerateTruthRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    pbix_dir: Optional[str] = None
    limit: int = 50


class UpdateTruthCaseRequest(BaseModel):
    status: str  # draft | approved | rejected
    question: Optional[str] = None


class RunTruthSuiteRequest(BaseModel):
    project_id: Optional[str] = None


@router.post("/generate", response_model=Dict[str, Any])
def generate_cases(req: GenerateTruthRequest) -> Dict[str, Any]:
    if req.pbix_dir:
        added = generate_golden_questions_from_pbix_dir(
            pbix_dir=req.pbix_dir,
            limit=req.limit,
            project_id=req.project_id,
        )
    else:
        added = generate_truth_cases(project_id=req.project_id, domain=req.domain)
    return {"added_count": len(added), "cases": added}


@router.get("/cases", response_model=Dict[str, Any])
def get_cases(project_id: Optional[str] = None) -> Dict[str, Any]:
    cases = list_truth_cases(project_id=project_id)
    return {"count": len(cases), "cases": cases}


@router.patch("/cases/{case_id}", response_model=Dict[str, Any])
def update_case(case_id: str, req: UpdateTruthCaseRequest) -> Dict[str, Any]:
    updated = set_truth_case_status(case_id, req.status, question=req.question)
    if not updated:
        raise HTTPException(status_code=404, detail="Truth test case not found.")
    return {"success": True, "case": updated}


@router.post("/run", response_model=Dict[str, Any])
def execute_truth_suite(req: RunTruthSuiteRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    return run_truth_suite(project_id=req.project_id, owner_id=str(uid) if uid else None)


@router.get("/runs", response_model=Dict[str, Any])
def get_runs(project_id: Optional[str] = None) -> Dict[str, Any]:
    runs = list_truth_runs(project_id=project_id)
    return {"count": len(runs), "runs": runs}

