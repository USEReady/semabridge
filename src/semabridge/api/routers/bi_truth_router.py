"""REST API Router for BI-Truth Test Suite."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
import sqlalchemy as sa
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db
from semabridge.ai.metric_execution.service import MetricComparisonService
from semabridge.repository.orm.ai_models import MetricComparisonConfigRow, MetricComparisonRunRow

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


class InjectTruthCasesRequest(BaseModel):
    project_id: str
    case_ids: Optional[List[str]] = None
    dry_run: bool = False
    view: Optional[str] = None


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


@router.post("/inject", response_model=Dict[str, Any])
def inject_truth_cases(req: InjectTruthCasesRequest, request: Request) -> Dict[str, Any]:
    """Inject qualifying verified query pairs into downstream Cortex Analyst or Genie model."""
    uid = getattr(request.state, "user_id", None)
    from semabridge.ai.bi_truth_service import inject_verified_queries_for_project
    try:
        res = inject_verified_queries_for_project(
            project_id=req.project_id,
            case_ids=req.case_ids,
            dry_run=req.dry_run,
            user=str(uid) if uid else "user",
            view_name=req.view,
        )
        return res
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Injection failed: {str(exc)}")


@router.get("/inject/history", response_model=Dict[str, Any])
def get_bi_truth_injection_history(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve verified query injection history for a project."""
    from semabridge.ai.bi_truth_service import list_truth_runs
    runs = list_truth_runs(project_id) if project_id else []
    return {"history": runs}


# =====================================================================
# Source-to-Target Real-Time Metric Comparison Endpoints
# =====================================================================

class RunMetricComparisonRequest(BaseModel):
    project_id: str = "default"
    metric_name: Optional[str] = None
    metric_id: Optional[str] = None
    source_platform: str = "fabric"  # fabric | power_bi | mock
    target_platform: str = "snowflake"  # snowflake | databricks
    target_view: Optional[str] = ""
    target_semantic_view: Optional[str] = None
    source_config: Dict[str, Any] = {}
    target_config: Dict[str, Any] = {}
    slice_filters: Optional[Dict[str, Any]] = None
    tolerance_pct: Optional[float] = None
    tolerance_percentage: Optional[float] = None
    floor: Optional[float] = None
    null_policy: str = "STRICT_NULL"  # STRICT_NULL | NULL_EQUALS_ZERO
    mock_mode: bool = False
    execution_mode: Optional[str] = None  # LIVE | MOCK
    timeout_seconds: int = 60


class UpdateMetricComparisonConfigRequest(BaseModel):
    project_id: str
    metric_name: Optional[str] = None
    metric_id: Optional[str] = None
    source_platform: Optional[str] = None
    target_platform: Optional[str] = None
    target_semantic_view: Optional[str] = None
    tolerance_pct: Optional[float] = None
    tolerance_percentage: Optional[float] = None
    absolute_floor: float = 0.01
    null_policy: str = "STRICT_NULL"
    execution_timeout: int = 60
    configuration: Optional[Dict[str, Any]] = None


def _serialize_run_row(r: MetricComparisonRunRow) -> Dict[str, Any]:
    return {
        "id": r.id,
        "run_id": r.id,
        "project_id": r.project_id,
        "configuration_id": getattr(r, "configuration_id", None),
        "metric_id": r.metric_name,
        "metric_name": r.metric_name,
        "source_platform": r.source_platform,
        "target_platform": r.target_platform,
        "target_view": r.target_view,
        "target_semantic_view": r.target_view,
        "execution_mode": getattr(r, "execution_mode", "MOCK" if r.mock_mode else "LIVE"),
        "source_query": r.source_query,
        "target_query": r.target_query,
        "generated_dax": r.source_query,
        "generated_sql": r.target_query,
        "source_value": r.source_value,
        "target_value": r.target_value,
        "variance_pct": r.variance_pct,
        "percentage_variance": r.variance_pct,
        "absolute_difference": r.absolute_difference,
        "absolute_variance": r.absolute_difference,
        "tolerance_pct": r.tolerance_pct,
        "tolerance_percentage": r.tolerance_pct,
        "verdict": r.verdict,
        "source_execution_status": getattr(r, "source_execution_status", "SUCCESS"),
        "target_execution_status": getattr(r, "target_execution_status", "SUCCESS"),
        "source_execution_duration_ms": getattr(r, "source_execution_duration_ms", 0.0),
        "target_execution_duration_ms": getattr(r, "target_execution_duration_ms", 0.0),
        "reason": r.reason,
        "error": r.reason if r.verdict != "PASS" else None,
        "execution_ms": r.execution_ms,
        "mock_mode": r.mock_mode,
        "executed_at": r.executed_at.isoformat() if r.executed_at else None,
        "created_at": r.executed_at.isoformat() if r.executed_at else None,
        "completed_at": r.completed_at.isoformat() if getattr(r, "completed_at", None) else None,
    }


@router.get("/metric-comparison/health", response_model=Dict[str, Any])
def get_metric_comparison_health(
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Return health status of the Metric Comparator integration and required dependencies."""
    db_status = "connected"
    try:
        db.execute(sa.text("SELECT 1"))
    except Exception as ex:
        db_status = f"unhealthy: {str(ex)}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "service": "metric-comparator",
        "database": db_status,
        "adapters": {
            "fabric": True,
            "power_bi": True,
            "snowflake": True,
            "databricks": True,
            "mock": True,
        },
        "supported_null_policies": ["STRICT_NULL", "NULL_EQUALS_ZERO"],
        "timestamp": time.time(),
    }


@router.get("/metric-comparison/metrics", response_model=Dict[str, Any])
def get_metric_comparison_metrics(
    project_id: str = Query(..., description="Project ID to discover metrics for"),
    view: Optional[str] = Query(None, description="Optional target view name"),
) -> Dict[str, Any]:
    """Retrieve available metrics using semantic model metadata for the project."""
    from semabridge.api.routers.reconciliation_router import get_project_reconciliation_metrics
    data = get_project_reconciliation_metrics(project_id=project_id, view=view)
    return {
        "project_id": project_id,
        "count": len(data.get("metrics", [])),
        "metrics": data.get("metrics", []),
        "target_view": data.get("target_view") or data.get("view_name") or "",
    }


@router.post("/metric-comparison/run", response_model=Dict[str, Any])
async def run_metric_comparison_endpoint(
    req: RunMetricComparisonRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Run real-time source DAX vs target warehouse SQL comparison."""
    metric = req.metric_name or req.metric_id
    if not metric:
        raise HTTPException(status_code=422, detail="metric_name or metric_id is required")

    tgt_view = req.target_view or req.target_semantic_view or ""
    tol = req.tolerance_pct if req.tolerance_pct is not None else req.tolerance_percentage
    is_mock = req.mock_mode or (bool(req.execution_mode) and str(req.execution_mode).upper() == "MOCK")

    svc = MetricComparisonService(db_session=db)
    try:
        res = await svc.run_comparison_async(
            project_id=req.project_id,
            metric_name=metric,
            source_platform=req.source_platform,
            target_platform=req.target_platform,
            target_view=tgt_view,
            source_config=req.source_config,
            target_config=req.target_config,
            slice_filters=req.slice_filters,
            tolerance_pct=tol,
            floor=req.floor,
            null_policy=req.null_policy,
            mock_mode=is_mock,
            timeout_seconds=req.timeout_seconds,
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Metric comparison failed: {str(exc)}")


@router.get("/metric-comparison/history", response_model=Dict[str, Any])
def get_metric_comparison_history(
    project_id: Optional[str] = None,
    metric_name: Optional[str] = None,
    metric_id: Optional[str] = None,
    verdict: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve history of metric comparison executions."""
    query = db.query(MetricComparisonRunRow)
    if project_id:
        query = query.filter(MetricComparisonRunRow.project_id == project_id)
    m = metric_name or metric_id
    if m:
        query = query.filter(MetricComparisonRunRow.metric_name == m)
    if verdict:
        query = query.filter(MetricComparisonRunRow.verdict == verdict.upper())

    offset = max(0, (page - 1) * limit)
    rows = query.order_by(MetricComparisonRunRow.executed_at.desc()).offset(offset).limit(limit).all()

    history = [_serialize_run_row(r) for r in rows]
    return {
        "count": len(history),
        "page": page,
        "limit": limit,
        "runs": history,
    }


@router.get("/metric-comparison/config", response_model=Dict[str, Any])
def get_metric_comparison_config(
    project_id: str,
    metric_name: Optional[str] = None,
    metric_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve comparison configuration (tolerance, floor, null policy)."""
    target_metric = metric_name or metric_id

    # 1. Metric-level config if requested
    metric_cfg = None
    if target_metric:
        row = (
            db.query(MetricComparisonConfigRow)
            .filter(
                MetricComparisonConfigRow.project_id == project_id,
                MetricComparisonConfigRow.metric_name == target_metric,
            )
            .first()
        )
        if row:
            metric_cfg = {
                "tolerance_pct": row.tolerance_pct,
                "tolerance_percentage": row.tolerance_pct,
                "absolute_floor": row.absolute_floor,
                "null_policy": row.null_policy,
                "source_platform": row.source_platform,
                "target_platform": row.target_platform,
                "target_semantic_view": row.target_semantic_view,
                "execution_timeout": row.execution_timeout,
            }

    # 2. Project-level default
    proj_row = (
        db.query(MetricComparisonConfigRow)
        .filter(
            MetricComparisonConfigRow.project_id == project_id,
            MetricComparisonConfigRow.metric_name.is_(None),
        )
        .first()
    )
    proj_cfg = {
        "tolerance_pct": proj_row.tolerance_pct if proj_row else 0.01,
        "tolerance_percentage": proj_row.tolerance_pct if proj_row else 0.01,
        "absolute_floor": proj_row.absolute_floor if proj_row else 0.01,
        "null_policy": proj_row.null_policy if proj_row else "STRICT_NULL",
        "source_platform": proj_row.source_platform if proj_row else "fabric",
        "target_platform": proj_row.target_platform if proj_row else "snowflake",
        "target_semantic_view": proj_row.target_semantic_view if proj_row else "",
        "execution_timeout": proj_row.execution_timeout if proj_row else 60,
    }

    return {
        "project_id": project_id,
        "metric_name": target_metric,
        "metric_id": target_metric,
        "effective": metric_cfg or proj_cfg,
        "project_default": proj_cfg,
        "metric_override": metric_cfg,
    }


@router.put("/metric-comparison/config", response_model=Dict[str, Any])
def update_metric_comparison_config(
    req: UpdateMetricComparisonConfigRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Upsert project-level or metric-level comparison configuration."""
    target_metric = req.metric_name or req.metric_id
    tol = req.tolerance_pct if req.tolerance_pct is not None else (req.tolerance_percentage if req.tolerance_percentage is not None else 0.01)

    row = (
        db.query(MetricComparisonConfigRow)
        .filter(
            MetricComparisonConfigRow.project_id == req.project_id,
            MetricComparisonConfigRow.metric_name == target_metric,
        )
        .first()
    )
    if not row:
        row = MetricComparisonConfigRow(
            project_id=req.project_id,
            metric_name=target_metric,
            tolerance_pct=tol,
            absolute_floor=req.absolute_floor,
            null_policy=req.null_policy,
            source_platform=req.source_platform or "fabric",
            target_platform=req.target_platform or "snowflake",
            target_semantic_view=req.target_semantic_view or "",
            execution_timeout=req.execution_timeout or 60,
            configuration=json.dumps(req.configuration) if req.configuration else None,
        )
        db.add(row)
    else:
        row.tolerance_pct = tol
        row.absolute_floor = req.absolute_floor
        row.null_policy = req.null_policy
        if req.source_platform:
            row.source_platform = req.source_platform
        if req.target_platform:
            row.target_platform = req.target_platform
        if req.target_semantic_view is not None:
            row.target_semantic_view = req.target_semantic_view
        if req.execution_timeout:
            row.execution_timeout = req.execution_timeout
        if req.configuration is not None:
            row.configuration = json.dumps(req.configuration)

    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "project_id": row.project_id,
        "metric_name": row.metric_name,
        "metric_id": row.metric_name,
        "tolerance_pct": row.tolerance_pct,
        "tolerance_percentage": row.tolerance_pct,
        "absolute_floor": row.absolute_floor,
        "null_policy": row.null_policy,
        "source_platform": row.source_platform,
        "target_platform": row.target_platform,
        "target_semantic_view": row.target_semantic_view,
        "execution_timeout": row.execution_timeout,
    }


@router.get("/metric-comparison/history/{run_id}", response_model=Dict[str, Any])
@router.get("/metric-comparison/{run_id}", response_model=Dict[str, Any])
def get_metric_comparison_detail(
    run_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve complete execution details for a specific comparison run."""
    row = db.query(MetricComparisonRunRow).filter(MetricComparisonRunRow.id == run_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Comparison run not found")
    return _serialize_run_row(row)


