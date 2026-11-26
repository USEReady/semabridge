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
    model_config = ConfigDict(extra="ignore")
    target_name: Optional[str] = None
    target_object: Optional[str] = None
    target_type: str = "COLUMN"  # 'COLUMN', 'TABLE', 'METRIC'
    change_type: str = "MODIFY_EXPRESSION"    # 'DROP', 'RENAME', 'MODIFY_TYPE', 'MODIFY_EXPRESSION', 'ADD', 'MODIFY_DESCRIPTION'
    category: Optional[str] = None
    finding_category: Optional[str] = None
    table_context: Optional[str] = None
    project_id: Optional[str] = None
    view: Optional[str] = None
    expression: Optional[str] = None
    proposed_expression: Optional[str] = None
    mock_mode: Optional[bool] = None
    platform: Optional[str] = None

    def get_target_name(self) -> str:
        return (self.target_name or self.target_object or "").strip()

    def get_category(self) -> Optional[str]:
        return self.category or self.finding_category

    def get_expression(self) -> Optional[str]:
        return self.expression or self.proposed_expression


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
    target_clean = req.get_target_name()
    if not target_clean:
        raise HTTPException(status_code=422, detail="Field 'target_name' or 'target_object' is required.")

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

    adapter = None
    resolved_platform = req.platform
    if not resolved_platform and req.project_id:
        try:
            from semabridge.api.project_config_store import project_config_store
            cfg = project_config_store.get(req.project_id.strip())
            if cfg:
                resolved_platform = (cfg.get("target_warehouse") or {}).get("platform")
        except Exception as cfg_exc:
            logger.warning(
                "Failed to resolve target warehouse platform from project config store for %s: %s",
                req.project_id,
                cfg_exc,
            )

    if resolved_platform:
        try:
            from semabridge.governance.drift.adapters.registry import get_adapter
            adapter = get_adapter(resolved_platform)
        except Exception as adapter_exc:
            logger.error(
                "Failed to instantiate drift adapter for resolved platform '%s': %s",
                resolved_platform,
                adapter_exc,
            )

    simulator = BlastRadiusSimulator(
        ontology=fed,
        verified_queries=verified_queries,
        certified_metrics=certified_names,
        mock_mode=bool(is_mock),
        adapter=adapter,
        platform=resolved_platform,
    )
    try:
        report = simulator.simulate_impact(
            target_name=target_clean,
            target_type=req.target_type,
            change_type=req.change_type,
            table_context=req.table_context,
            allowed_domains=allowed_domains,
            expression=req.get_expression(),
            project_id=req.project_id,
            view=req.view,
            mock_mode=bool(is_mock),
            category=req.get_category(),
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


class AIImpactRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    object_name: Optional[str] = None
    metric_name: Optional[str] = None
    kind: Optional[str] = "METRIC"
    table: Optional[str] = None
    project_id: Optional[str] = None
    view_name: Optional[str] = None
    baseline_description: Optional[str] = None
    live_description: Optional[str] = None
    questions: Optional[List[str]] = None
    max_questions: Optional[int] = None


@router.post("/drift/ai-impact-check", response_model=Dict[str, Any])
@router.post("/ai-impact-check", response_model=Dict[str, Any])
def run_governance_ai_impact_check(req: AIImpactRequest) -> Dict[str, Any]:
    """Simulate AI query resolution impact under baseline vs warehouse comment without warehouse writes."""
    from semabridge.governance.ai_impact_service import run_ai_impact_check
    target = req.object_name or req.metric_name
    if not target or not target.strip():
        raise HTTPException(status_code=400, detail="Object or metric name must be provided.")
    try:
        fed = ontology_store.get_federated_ontology()
        report = run_ai_impact_check(
            object_name=target.strip(),
            kind=req.kind or "METRIC",
            table=req.table,
            project_id=req.project_id,
            view_name=req.view_name,
            baseline_description=req.baseline_description,
            live_description=req.live_description,
            ontology=fed,
            custom_questions=req.questions,
            max_questions=req.max_questions,
        )
        return report.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
