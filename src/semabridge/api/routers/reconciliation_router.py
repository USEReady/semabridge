"""REST API Router for Automated Mathematical Data Reconciliation."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from semabridge.reconciliation.golden_verifier import (
    GoldenQueryVerifier,
    VerificationResult,
    VerificationTolerance,
)

logger = logging.getLogger("semabridge.api.reconciliation_router")

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


class GenerateQueriesRequest(BaseModel):
    metric_name: str
    metric_sql_expression: str
    target_table_or_view: str
    dimensions: List[str] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)


class ReconcileDataRequest(BaseModel):
    metric_name: str
    dax_results: List[Dict[str, Any]]
    sql_results: List[Dict[str, Any]]
    dimension_keys: List[str] = Field(default_factory=list)
    dax_query: str = ""
    sql_query: str = ""
    tolerance: Optional[VerificationTolerance] = None


@router.post("/generate-queries", response_model=Dict[str, str])
def generate_reconciliation_queries(req: GenerateQueriesRequest) -> Dict[str, str]:
    """Generate side-by-side DAX and SQL queries for golden metric reconciliation."""
    verifier = GoldenQueryVerifier()
    dax_query = verifier.generate_dax_query(
        metric_name=req.metric_name,
        dimensions=req.dimensions,
        filters=req.filters,
    )
    sql_query = verifier.generate_sql_query(
        metric_name=req.metric_name,
        metric_sql_expression=req.metric_sql_expression,
        target_table_or_view=req.target_table_or_view,
        dimensions=req.dimensions,
        filters=req.filters,
    )
    return {
        "dax_query": dax_query,
        "sql_query": sql_query,
    }


@router.post("/verify", response_model=VerificationResult)
def verify_metric_results(req: ReconcileDataRequest) -> VerificationResult:
    """Compare DAX vs SQL result sets and compute mathematical parity metrics."""
    verifier = GoldenQueryVerifier(default_tolerance=req.tolerance)
    result = verifier.reconcile_data(
        metric_name=req.metric_name,
        dax_rows=req.dax_results,
        sql_rows=req.sql_results,
        dimension_keys=req.dimension_keys,
        dax_query=req.dax_query,
        sql_query=req.sql_query,
        custom_tolerance=req.tolerance,
    )
    return result
