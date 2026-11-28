"""REST API Router for Databricks Genie Space Connector."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from semabridge.connectors.genie.connector import GenieConnector

logger = logging.getLogger("semabridge.api.genie_router")

router = APIRouter(prefix="/api/genie", tags=["genie_connector"])


class MapSpaceRequest(BaseModel):
    space_id: str
    metric_view: str
    project_id: Optional[str] = None
    host: Optional[str] = None
    token: Optional[str] = None


class PublishBenchmarksRequest(BaseModel):
    space_id: str
    pairs: Optional[List[Dict[str, Any]]] = None
    questions: Optional[List[Dict[str, Any]]] = None  # Convenient alias
    dry_run: bool = False
    project_id: Optional[str] = None
    host: Optional[str] = None
    token: Optional[str] = None


class EvaluateBenchmarksRequest(BaseModel):
    space_id: str
    benchmarks: List[Dict[str, Any]]
    timeout_seconds: int = 60
    project_id: Optional[str] = None
    run_id: Optional[str] = None
    case_id: Optional[str] = None
    host: Optional[str] = None
    token: Optional[str] = None


def _get_connector(
    host: Optional[str] = None,
    token: Optional[str] = None,
    project_id: Optional[str] = None,
) -> GenieConnector:
    cfg = {}
    if project_id:
        try:
            from semabridge.ai.project_connection import resolve_project_warehouse_config
            db_cfg = resolve_project_warehouse_config(project_id, "DATABRICKS")
            if db_cfg:
                resolved_host = getattr(db_cfg, "host", None) or getattr(db_cfg, "server_hostname", None)
                resolved_token = getattr(db_cfg, "token", None) or getattr(db_cfg, "access_token", None)
                if hasattr(resolved_token, "get_secret_value"):
                    resolved_token = resolved_token.get_secret_value()
                if resolved_host:
                    cfg["host"] = str(resolved_host)
                if resolved_token:
                    cfg["token"] = str(resolved_token)
        except Exception as exc:
            logger.debug("Could not auto-resolve Databricks config for project %s: %s", project_id, exc)

    if host:
        cfg["host"] = host
    if token:
        cfg["token"] = token
    conn = GenieConnector(cfg)
    conn.authenticate()
    return conn


@router.get("/health", response_model=Dict[str, Any])
def check_genie_health(
    project_id: Optional[str] = None,
    host: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate connectivity and write permissions to Databricks Genie APIs."""
    conn = _get_connector(host=host, token=token, project_id=project_id)
    res = conn.health_check()
    return {
        "connected": res.connected,
        "host": res.host,
        "spaces_count": res.spaces_count,
        "has_write_scope": res.has_write_scope,
        "error": res.error,
        "details": res.details,
    }


@router.get("/spaces", response_model=Dict[str, Any])
def list_genie_spaces(
    project_id: Optional[str] = None,
    host: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """List available Genie Spaces in the workspace."""
    conn = _get_connector(host=host, token=token, project_id=project_id)
    spaces = conn.list_spaces()
    return {
        "count": len(spaces),
        "spaces": [s.__dict__ for s in spaces],
    }


@router.get("/spaces/{space_id}", response_model=Dict[str, Any])
def get_genie_space_details(
    space_id: str,
    project_id: Optional[str] = None,
    host: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve details and configuration of a specific Genie Space."""
    conn = _get_connector(host=host, token=token, project_id=project_id)
    space = conn.get_space_details(space_id)
    if not space:
        raise HTTPException(status_code=404, detail=f"Genie Space '{space_id}' not found.")
    return space.__dict__


@router.post("/map", response_model=Dict[str, Any])
def map_genie_space_to_metric_view(
    req: MapSpaceRequest,
) -> Dict[str, Any]:
    """Map a Genie Space to a target metric view."""
    conn = _get_connector(host=req.host, token=req.token, project_id=req.project_id)
    try:
        return conn.map_space_to_metric_view(req.space_id, req.metric_view)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to map Genie Space: {str(exc)}")


@router.post("/publish", response_model=Dict[str, Any])
def publish_genie_benchmarks(
    req: PublishBenchmarksRequest,
) -> Dict[str, Any]:
    """Publish approved question-SQL pairs to a Genie Space."""
    conn = _get_connector(host=req.host, token=req.token, project_id=req.project_id)
    benchmark_pairs = req.pairs if req.pairs is not None else (req.questions or [])
    try:
        res = conn.publish_benchmarks(
            space_id=req.space_id,
            pairs=benchmark_pairs,
            dry_run=req.dry_run,
        )
        return res.__dict__
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to publish benchmarks: {str(exc)}")


@router.post("/evaluate", response_model=Dict[str, Any])
def evaluate_genie_benchmarks(
    req: EvaluateBenchmarksRequest,
) -> Dict[str, Any]:
    """Trigger Genie benchmark evaluation and retrieve accuracy outcomes."""
    conn = _get_connector(host=req.host, token=req.token, project_id=req.project_id)
    try:
        res = conn.evaluate_benchmarks(
            space_id=req.space_id,
            benchmarks=req.benchmarks,
            timeout_seconds=req.timeout_seconds,
            run_id=req.run_id,
            case_id=req.case_id,
        )

        # Link evaluation result to MetricComparisonRunRow if run_id provided
        if req.run_id:
            try:
                from semabridge.repository.orm.session_factory import get_session
                from semabridge.repository.orm.ai_models import MetricComparisonRunRow
                with get_session() as session:
                    run_row = session.get(MetricComparisonRunRow, req.run_id)
                    if run_row:
                        meta = dict(run_row.execution_metadata or {})
                        meta["genie_evaluation"] = {
                            "evaluation_id": res.evaluation_id,
                            "space_id": res.space_id,
                            "status": res.status,
                            "accuracy_score": res.accuracy_score,
                            "passed": res.passed_benchmarks,
                            "total": res.total_benchmarks,
                            "evaluated_at": res.evaluated_at,
                        }
                        run_row.execution_metadata = meta
                        session.commit()
            except Exception as e:
                logger.debug("Could not link Genie evaluation to run %s: %s", req.run_id, e)

        return {
            "evaluation_id": res.evaluation_id,
            "space_id": res.space_id,
            "status": res.status,
            "accuracy_score": res.accuracy_score,
            "total_benchmarks": res.total_benchmarks,
            "passed_benchmarks": res.passed_benchmarks,
            "failed_benchmarks": res.failed_benchmarks,
            "duration_ms": res.duration_ms,
            "error": res.error,
            "run_id": res.run_id,
            "case_id": res.case_id,
            "evaluated_at": res.evaluated_at,
            "results": [r.__dict__ for r in res.results],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to evaluate Genie benchmarks: {str(exc)}")
