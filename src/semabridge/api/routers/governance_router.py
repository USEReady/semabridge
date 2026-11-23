"""REST API Router for Semantic Governance, Blast Radius & CI/CD Linting."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from semabridge.governance.blast_radius import BlastRadiusReport, BlastRadiusSimulator
from semabridge.governance.linter import LintReport, SemanticLinter
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.governance_router")

router = APIRouter(prefix="/api/governance", tags=["governance"])


class BlastRadiusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_name: str
    target_type: str = "COLUMN"  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str = "DROP"    # 'DROP', 'RENAME', 'MODIFY_TYPE', 'MODIFY_EXPRESSION'
    table_context: Optional[str] = None
    project_id: Optional[str] = None
    view: Optional[str] = None
    mock_mode: Optional[bool] = None


class LintOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_steward: bool = True
    require_description: bool = True
    enforce_snake_case_metrics: bool = True
    # Concept/attribute naming; None follows enforce_snake_case_metrics
    enforce_snake_case_concepts: Optional[bool] = None
    steward_severity: Optional[str] = None  # 'ERROR' | 'WARNING' | 'OFF'
    project_id: Optional[str] = None


def _resolve_project_allowed_domains(project_id: Optional[str]) -> Optional[List[str]]:
    """Domain names/ids of a project (``ontology_store.domains_for``).

    ``None`` = no project (unrestricted). A project without domains yields
    ``[project_id]`` -- a filter that matches nothing -- never every domain.
    """
    pid = (project_id or "").strip()
    if not pid:
        return None
    from semabridge.api.services.project_shared import resolve_canonical_project_id
    from semabridge.ontology.models import project_key
    c_pid = resolve_canonical_project_id(pid) or pid
    pkey = project_key(c_pid) or project_key(pid)
    doms = ontology_store.domains_for(c_pid) or ontology_store.domains_for(pid)
    res: List[str] = []
    seen: set = set()
    for d in (doms or []):
        for name in (d.domain, getattr(d, "id", None)):
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                res.append(name)
    for d in ontology_store.domains.values():
        src_pid = getattr(d, "source_project_id", None)
        if src_pid and project_key(src_pid) == pkey:
            for name in (d.domain, getattr(d, "id", None)):
                if name and name.casefold() not in seen:
                    seen.add(name.casefold())
                    res.append(name)
    if res:
        return res
    return [c_pid, pid]


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

    from semabridge.reconciliation.mock_parity import is_mock_drift_enabled

    is_mock = is_mock_drift_enabled()

    simulator = BlastRadiusSimulator(
        ontology=fed,
        verified_queries=verified_queries,
        certified_metrics=certified_names,
        mock_mode=bool(is_mock),
    )
    try:
        report = simulator.simulate_impact(
            target_name=req.target_name,
            target_type=req.target_type,
            change_type=req.change_type,
            table_context=req.table_context,
            allowed_domains=allowed_domains,
            mock_mode=bool(is_mock),
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
        enforce_snake_case_concepts=options.enforce_snake_case_concepts,
    )
    report = linter.lint_ontology(fed, allowed_domains=allowed_domains)
    return report
