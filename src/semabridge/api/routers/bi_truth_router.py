"""REST API Router for BI-Truth Test Suite."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from semabridge.ai.bi_truth_service import (
    add_manual_truth_case,
    clear_truth_cases,
    generate_golden_questions_from_pbix_dir,
    generate_truth_cases,
    import_truth_cases_from_content,
    list_truth_cases,
    list_truth_runs,
    run_truth_suite,
    set_batch_truth_case_status,
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


class BatchUpdateTruthCasesRequest(BaseModel):
    status: str  # draft | approved | rejected
    project_id: Optional[str] = None
    case_ids: Optional[list[str]] = None


class CreateManualTruthCaseRequest(BaseModel):
    question: str
    project_id: Optional[str] = None
    domain: Optional[str] = None
    metric_name: Optional[str] = None
    expected_sql: Optional[str] = None
    expected_dax: Optional[str] = None
    status: str = "approved"


class ImportDocTextRequest(BaseModel):
    content: str
    filename: Optional[str] = "input.csv"
    project_id: Optional[str] = None
    domain: Optional[str] = None
    status: str = "approved"


class RunTruthSuiteRequest(BaseModel):
    project_id: Optional[str] = None


@router.post("/generate", response_model=Dict[str, Any])
def generate_cases(req: GenerateTruthRequest) -> Dict[str, Any]:
    try:
        if req.pbix_dir:
            added = generate_golden_questions_from_pbix_dir(
                pbix_dir=req.pbix_dir,
                limit=req.limit,
                project_id=req.project_id,
            )
        else:
            added = generate_truth_cases(project_id=req.project_id, domain=req.domain, limit=req.limit)
        return {"added_count": len(added), "cases": added}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate test cases: {str(e)}")


@router.get("/cases", response_model=Dict[str, Any])
def get_cases(project_id: Optional[str] = None) -> Dict[str, Any]:
    cases = list_truth_cases(project_id=project_id)
    return {"count": len(cases), "cases": cases}


@router.post("/cases", response_model=Dict[str, Any])
def create_case(req: CreateManualTruthCaseRequest) -> Dict[str, Any]:
    try:
        new_case = add_manual_truth_case(
            project_id=req.project_id,
            domain=req.domain,
            question=req.question,
            metric_name=req.metric_name,
            expected_sql=req.expected_sql,
            expected_dax=req.expected_dax,
            status=req.status,
        )
        return {"success": True, "case": new_case}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/import-text", response_model=Dict[str, Any])
def import_cases_text(req: ImportDocTextRequest) -> Dict[str, Any]:
    added = import_truth_cases_from_content(
        content=req.content,
        filename=req.filename or "input.csv",
        project_id=req.project_id,
        domain=req.domain,
        status=req.status,
    )
    return {"success": True, "added_count": len(added), "cases": added}


@router.post("/cases/upload", response_model=Dict[str, Any])
async def upload_cases_file(
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(default=None),
    domain: Optional[str] = Form(default=None),
    status: str = Form(default="approved"),
) -> Dict[str, Any]:
    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1", errors="ignore")

    added = import_truth_cases_from_content(
        content=text,
        filename=file.filename or "upload.csv",
        project_id=project_id,
        domain=domain,
        status=status,
    )
    return {"success": True, "added_count": len(added), "cases": added}


@router.patch("/cases/{case_id}", response_model=Dict[str, Any])
def update_case(case_id: str, req: UpdateTruthCaseRequest) -> Dict[str, Any]:
    updated = set_truth_case_status(case_id, req.status, question=req.question)
    if not updated:
        raise HTTPException(status_code=404, detail="Truth test case not found.")
    return {"success": True, "case": updated}


@router.post("/cases/batch-status", response_model=Dict[str, Any])
def batch_update_cases(req: BatchUpdateTruthCasesRequest) -> Dict[str, Any]:
    updated_count = set_batch_truth_case_status(
        status=req.status,
        project_id=req.project_id,
        case_ids=req.case_ids,
    )
    return {"success": True, "updated_count": updated_count}


@router.delete("/cases", response_model=Dict[str, Any])
def delete_cases(project_id: Optional[str] = None) -> Dict[str, Any]:
    cleared = clear_truth_cases(project_id=project_id)
    return {"cleared_count": cleared}


@router.post("/run", response_model=Dict[str, Any])
def execute_truth_suite(req: RunTruthSuiteRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    return run_truth_suite(project_id=req.project_id, owner_id=str(uid) if uid else None)


@router.get("/runs", response_model=Dict[str, Any])
def get_runs(project_id: Optional[str] = None) -> Dict[str, Any]:
    runs = list_truth_runs(project_id=project_id)
    return {"count": len(runs), "runs": runs}

