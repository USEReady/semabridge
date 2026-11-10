"""REST API Router for Semantic Governance, Blast Radius & CI/CD Linting."""

from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from semabridge.governance.blast_radius import BlastRadiusReport, BlastRadiusSimulator, TargetType
from semabridge.governance.linter import LintReport, SemanticLinter
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.governance_router")

router = APIRouter(prefix="/api/governance", tags=["governance"])


class BlastRadiusRequest(BaseModel):
    target_name: str
    target_type: str = "COLUMN"  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str = "DROP"    # 'DROP', 'RENAME', 'MODIFY_EXPRESSION'
    table_context: Optional[str] = None


class LintOptions(BaseModel):
    require_steward: bool = True
    require_description: bool = True
    enforce_snake_case_metrics: bool = True


@router.post("/blast-radius", response_model=BlastRadiusReport)
def simulate_blast_radius(req: BlastRadiusRequest) -> BlastRadiusReport:
    """Calculate the downstream impact of changing a data layer column, table, or metric."""
    if not req.target_name or not req.target_name.strip():
        raise HTTPException(status_code=400, detail="Target name cannot be empty.")

    valid_types = {e.value for e in TargetType}
    if req.target_type.upper() not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target_type '{req.target_type}'. Must be one of {sorted(valid_types)}",
        )

    fed = ontology_store.get_federated_ontology()
    simulator = BlastRadiusSimulator(ontology=fed)
    try:
        report = simulator.simulate_impact(
            target_name=req.target_name,
            target_type=req.target_type,
            change_type=req.change_type,
            table_context=req.table_context,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return report


@router.post("/lint", response_model=LintReport)
def lint_enterprise_semantics(opts: Optional[LintOptions] = None) -> LintReport:
    """Run automated CI/CD semantic quality gate rules against the active enterprise ontology."""
    options = opts or LintOptions()
    fed = ontology_store.get_federated_ontology()
    linter = SemanticLinter(
        require_steward=options.require_steward,
        require_description=options.require_description,
        enforce_snake_case_metrics=options.enforce_snake_case_metrics,
    )
    report = linter.lint_ontology(fed)
    return report
