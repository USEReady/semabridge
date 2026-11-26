"""REST API Router for BI-Truth Test Suite."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from semabridge.ai.bi_truth_service import (
    _sanitize_cell_value,
    accept_truth_case_benchmark,
    add_manual_truth_case,
    clear_truth_cases,
    create_truth_schedule,
    delete_truth_schedule,
    generate_golden_questions_from_pbix_dir,
    generate_truth_cases,
    import_truth_cases_from_content,
    list_truth_cases,
    list_truth_runs,
    list_truth_schedules,
    run_truth_suite,
    set_batch_truth_case_status,
    set_truth_case_critical_status,
    set_truth_case_status,
)

router = APIRouter(prefix="/api/bi-truth", tags=["bi_truth_suite"])


class GenerateTruthRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    pbix_dir: Optional[str] = None
    view: Optional[str] = None
    limit: int = 50
    replace_drafts: bool = False


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
    status: str = "draft"


class ImportDocTextRequest(BaseModel):
    content: str
    filename: Optional[str] = "input.csv"
    project_id: Optional[str] = None
    domain: Optional[str] = None
    status: str = "draft"


class RunTruthSuiteRequest(BaseModel):
    project_id: Optional[str] = None
    view: Optional[str] = None


@router.post("/generate", response_model=Dict[str, Any])
def generate_cases(req: GenerateTruthRequest) -> Dict[str, Any]:
    try:
        if req.pbix_dir:
            added = generate_golden_questions_from_pbix_dir(
                pbix_dir=req.pbix_dir,
                limit=req.limit,
                project_id=req.project_id,
            )
            skipped = []
        else:
            added = generate_truth_cases(
                project_id=req.project_id,
                domain=req.domain,
                limit=req.limit,
                replace_drafts=req.replace_drafts,
                view=req.view,
            )
            skipped = getattr(added, "skipped", [])
        return _sanitize_cell_value({
            "added_count": len(added),
            "skipped_count": getattr(added, "skipped_count", len(skipped)),
            "cases": list(added),
            "skipped": skipped,
            "view": getattr(added, "view", None),
            "semantic_view": getattr(added, "semantic_view", None),
        })
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate test cases: {str(e)}")


@router.get("/cases", response_model=Dict[str, Any])
def get_cases(project_id: Optional[str] = None) -> Dict[str, Any]:
    cases = list_truth_cases(project_id=project_id)
    return _sanitize_cell_value({"count": len(cases), "cases": cases})


@router.post("/cases", response_model=Dict[str, Any])
def create_case(req: CreateManualTruthCaseRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    try:
        new_case = add_manual_truth_case(
            project_id=req.project_id,
            domain=req.domain,
            question=req.question,
            metric_name=req.metric_name,
            expected_sql=req.expected_sql,
            expected_dax=req.expected_dax,
            status=req.status,
            owner_id=str(uid) if uid else None,
        )
        return _sanitize_cell_value({"success": True, "case": new_case})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/cases/import-text", response_model=Dict[str, Any])
def import_cases_text(req: ImportDocTextRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    added = import_truth_cases_from_content(
        content=req.content,
        filename=req.filename or "input.csv",
        project_id=req.project_id,
        domain=req.domain,
        status=req.status,
        owner_id=str(uid) if uid else None,
    )
    return _sanitize_cell_value({"success": True, "added_count": len(added), "cases": added})


@router.post("/cases/upload", response_model=Dict[str, Any])
async def upload_cases_file(
    request: Request,
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(default=None),
    domain: Optional[str] = Form(default=None),
    status: str = Form(default="draft"),
) -> Dict[str, Any]:
    raw_bytes = await file.read()
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("latin-1", errors="ignore")

    uid = getattr(request.state, "user_id", None)
    added = import_truth_cases_from_content(
        content=text,
        filename=file.filename or "upload.csv",
        project_id=project_id,
        domain=domain,
        status=status,
        owner_id=str(uid) if uid else None,
    )
    return {"success": True, "added_count": len(added), "cases": added}


class AcceptBenchmarkRequest(BaseModel):
    project_id: Optional[str] = None
    expected_value: Optional[Any] = None
    new_expected_value: Optional[Any] = None
    note: Optional[str] = None
    expected_row_count: Optional[int] = None
    new_row_count: Optional[int] = None


class CriticalToggleRequest(BaseModel):
    is_critical: bool
    tolerance: Optional[float] = None


class WindowItem(BaseModel):
    window_key: Optional[str] = None
    key: Optional[str] = None
    name: Optional[str] = None
    window_label: Optional[str] = None
    label: Optional[str] = None
    filter_clause: Optional[str] = None
    filter: Optional[str] = None
    expected_value: Optional[Any] = None
    expected_row_count: Optional[int] = None


class UpdateWindowsRequest(BaseModel):
    windows: List[Dict[str, Any]] = []


class CreateScheduleRequest(BaseModel):
    project_id: str
    cron_expression: str
    webhook_url: Optional[str] = None


@router.get("/gate/{project_id}", response_model=Dict[str, Any])
def get_gate_status(project_id: str) -> Dict[str, Any]:
    from semabridge.ai.bi_truth_service import evaluate_project_deploy_gate
    verdict = evaluate_project_deploy_gate(project_id)
    return verdict.to_dict() if hasattr(verdict, "to_dict") else verdict


@router.patch("/cases/{case_id}/critical", response_model=Dict[str, Any])
def toggle_case_critical(case_id: str, req: CriticalToggleRequest) -> Dict[str, Any]:
    updated = set_truth_case_critical_status(case_id, req.is_critical, tolerance=req.tolerance)
    if not updated:
        raise HTTPException(status_code=404, detail="Truth test case not found.")
    return {"success": True, "case": updated}


@router.patch("/cases/{case_id}/windows", response_model=Dict[str, Any])
def update_case_windows(case_id: str, req: UpdateWindowsRequest) -> Dict[str, Any]:
    from semabridge.ai.bi_truth_service import set_truth_case_windows
    try:
        updated = set_truth_case_windows(case_id, [w if isinstance(w, dict) else w.model_dump() for w in req.windows])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not updated:
        raise HTTPException(status_code=404, detail="Truth test case not found.")
    return {"success": True, "case": updated}


@router.get("/cases/{case_id}/dimensions", response_model=Dict[str, Any])
def get_case_dimensions(case_id: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    from semabridge.ai.bi_truth_service import list_truth_case_dimensions
    dims = list_truth_case_dimensions(case_id, project_id=project_id)
    return {"dimensions": dims}


@router.get("/schedules", response_model=Dict[str, Any])
def get_schedules(project_id: Optional[str] = None) -> Dict[str, Any]:
    scheds = list_truth_schedules(project_id=project_id)
    return {"count": len(scheds), "schedules": scheds}


@router.post("/schedules", response_model=Dict[str, Any])
def create_schedule(req: CreateScheduleRequest) -> Dict[str, Any]:
    sched = create_truth_schedule(
        project_id=req.project_id,
        cron_expression=req.cron_expression,
        webhook_url=req.webhook_url,
    )
    return {"success": True, "schedule": sched}


@router.delete("/schedules/{schedule_id}", response_model=Dict[str, Any])
def remove_schedule(schedule_id: str) -> Dict[str, Any]:
    deleted = delete_truth_schedule(schedule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Schedule '{schedule_id}' not found.")
    return {"success": True}


@router.patch("/cases/{case_id}", response_model=Dict[str, Any])
def update_case(case_id: str, req: UpdateTruthCaseRequest) -> Dict[str, Any]:
    updated = set_truth_case_status(case_id, req.status, question=req.question)
    if not updated:
        raise HTTPException(status_code=404, detail="Truth test case not found.")
    return {"success": True, "case": updated}


@router.post("/cases/{case_id}/accept-benchmark", response_model=Dict[str, Any])
def accept_case_benchmark(
    case_id: str,
    req: AcceptBenchmarkRequest,
    request: Request,
) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    val = req.new_expected_value if req.new_expected_value is not None else req.expected_value
    rc = req.new_row_count if req.new_row_count is not None else req.expected_row_count
    if val is None:
        raise HTTPException(
            status_code=422,
            detail="Field 'new_expected_value' (or 'expected_value') is required to accept a benchmark.",
        )
    try:
        updated = accept_truth_case_benchmark(
            project_id=req.project_id or "",
            case_id=case_id,
            new_expected_value=val,
            user_id=str(uid) if uid else None,
            note=req.note,
            new_row_count=rc,
        )
        return {"success": True, "case": updated}
    except (KeyError, ValueError):
        raise HTTPException(status_code=404, detail=f"Truth case '{case_id}' not found.")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=str(ex))


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
    try:
        res = run_truth_suite(
            project_id=req.project_id,
            owner_id=str(uid) if uid else None,
            view=req.view,
        )
        return _sanitize_cell_value(res)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Truth suite run failed: {str(exc)}")


@router.get("/runs", response_model=Dict[str, Any])
def get_runs(project_id: Optional[str] = None) -> Dict[str, Any]:
    runs = list_truth_runs(project_id=project_id)
    return _sanitize_cell_value({"count": len(runs), "runs": runs})

