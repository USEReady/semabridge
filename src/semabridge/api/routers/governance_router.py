"""REST API Router for Semantic Governance, Blast Radius & CI/CD Linting."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from semabridge.governance.blast_radius import BlastRadiusReport, BlastRadiusSimulator
from semabridge.governance.linter import LintReport, SemanticLinter
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.governance_router")

router = APIRouter(prefix="/api/governance", tags=["governance"])


class BlastRadiusRequest(BaseModel):
    target_name: str
    target_type: str = "COLUMN"  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str = "DROP"    # 'DROP', 'RENAME', 'MODIFY_TYPE', 'MODIFY_EXPRESSION'
    table_context: Optional[str] = None
    project_id: Optional[str] = None


class LintOptions(BaseModel):
    require_steward: bool = True
    require_description: bool = True
    enforce_snake_case_metrics: bool = True
    steward_severity: Optional[str] = None  # 'ERROR' | 'WARNING' | 'OFF'
    project_id: Optional[str] = None


def _resolve_project_allowed_domains(project_id: Optional[str]) -> Optional[List[str]]:
    """Resolve attached or model-matched domain names/ids for a project_id."""
    pid = (project_id or "").strip()
    if not pid:
        return None
    pid_cf = pid.casefold()
    p_domains = ontology_store.get_project_domains(pid)
    if p_domains:
        return [d.domain for d in p_domains] + [d.id for d in p_domains if d.id]

    fed = ontology_store.get_federated_ontology()
    matched = [
        d
        for d in fed.domains.values()
        if (getattr(d, "source_project_id", None) or "").strip().casefold() == pid_cf
        or (getattr(d, "id", None) or "").strip().casefold() == pid_cf
        or d.domain.strip().casefold() == pid_cf
    ]
    if not matched:
        try:
            from semabridge.repository.model_repository import ModelRepository

            dep_info = ModelRepository().get_project_deployment_info(pid)
            model_name = (dep_info.get("model_name") or "").strip()
            sem_view = (dep_info.get("semantic_view") or dep_info.get("view_name") or "").strip()
            if model_name or sem_view:
                matched = [
                    d
                    for d in fed.domains.values()
                    if (model_name and d.domain.strip().casefold() == model_name.casefold())
                    or (
                        sem_view
                        and (getattr(d, "semantic_view_name", None) or "").strip().casefold() == sem_view.casefold()
                    )
                ]
        except Exception:
            pass
    if matched:
        return [d.domain for d in matched] + [d.id for d in matched if d.id]
    return [pid]


@router.post("/blast-radius", response_model=BlastRadiusReport)
def simulate_blast_radius(req: BlastRadiusRequest) -> BlastRadiusReport:
    """Calculate the downstream impact of changing a data layer column, table, or metric."""
    if not req.target_name or not req.target_name.strip():
        raise HTTPException(status_code=400, detail="Target name cannot be empty.")

    fed = ontology_store.get_federated_ontology()
    allowed_domains = _resolve_project_allowed_domains(req.project_id)
    verified_queries: List[Dict[str, Any]] = []
    certified_names: set[str] = set()

    if req.project_id and req.project_id.strip():
        pid = req.project_id.strip()
        try:
            from semabridge.ai.verified_queries_service import list_verified_queries_for_project
            verified_queries = list_verified_queries_for_project(pid)
        except Exception:
            verified_queries = []
        try:
            from semabridge.reconciliation.certification_service import get_certified_metric_names
            certified_names = get_certified_metric_names(pid)
        except Exception:
            certified_names = set()

    simulator = BlastRadiusSimulator(
        ontology=fed,
        verified_queries=verified_queries,
        certified_metrics=certified_names,
    )
    try:
        report = simulator.simulate_impact(
            target_name=req.target_name,
            target_type=req.target_type,
            change_type=req.change_type,
            table_context=req.table_context,
            allowed_domains=allowed_domains,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    return report


@router.post("/lint", response_model=LintReport)
def lint_enterprise_semantics(opts: Optional[LintOptions] = None) -> LintReport:
    """Run automated CI/CD semantic quality gate rules against the active enterprise or project ontology."""
    options = opts or LintOptions()
    fed = ontology_store.get_federated_ontology()
    allowed_domains = _resolve_project_allowed_domains(options.project_id)
    linter = SemanticLinter(
        require_steward=options.require_steward,
        require_description=options.require_description,
        enforce_snake_case_metrics=options.enforce_snake_case_metrics,
        steward_severity=options.steward_severity,
    )
    report = linter.lint_ontology(fed, allowed_domains=allowed_domains)
    return report
