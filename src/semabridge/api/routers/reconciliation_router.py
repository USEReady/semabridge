"""REST API Router for Automated Mathematical Data Reconciliation."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from semabridge.utils.reserved_prefixes import normalize_separator_ident, strip_reserved_column_prefix

from semabridge.reconciliation.certification_service import (
    get_latest_certifications,
    save_certification_record,
)
from semabridge.reconciliation.dax_executor import (
    DaxExecutionError,
    execute_dax,
    list_available_backends,
)
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
    target_kind: Optional[str] = Field(
        default=None,
        description="Optional target type override: 'table' or 'semantic_view'.",
    )
    project_id: Optional[str] = Field(
        default=None,
        description="Optional project ID to look up deployed semantic views.",  # hardcoding-ok: API schema doc
    )


class ReconcileDataRequest(BaseModel):
    metric_name: str
    dax_results: List[Dict[str, Any]]
    sql_results: List[Dict[str, Any]]
    dimension_keys: List[str] = Field(default_factory=list)
    dax_query: str = ""
    sql_query: str = ""
    tolerance: Optional[VerificationTolerance] = None
    project_id: Optional[str] = None
    domain: Optional[str] = None
    view_metric: Optional[str] = None
    certified_by: str = "semabridge_verifier"
    persist_certification: bool = True


class RunDaxRequest(BaseModel):
    query: str
    backend: Optional[str] = None  # 'pbi_desktop' | 'rest_api' | 'xmla'
    port: Optional[int] = None
    dataset_id: Optional[str] = None
    workspace: Optional[str] = None
    workspace_id: Optional[str] = None


class CertifyMetricRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    metric_name: str
    view_metric: Optional[str] = None
    metric_sql_expression: str = ""
    target_table_or_view: str
    target_kind: Optional[str] = "semantic_view"
    dimensions: List[str] = Field(default_factory=list)
    pbi_dimension_columns: List[str] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)
    dax_backend: Optional[str] = None
    dax_port: Optional[int] = None
    dax_dataset_id: Optional[str] = None
    dax_workspace: Optional[str] = None
    dax_workspace_id: Optional[str] = None
    dax_results: Optional[List[Dict[str, Any]]] = None
    tolerance: Optional[VerificationTolerance] = None
    certified_by: str = "semabridge_verifier"
    register_verified_query: bool = False
    verified_question: Optional[str] = None


def _resolve_target_kind(req: GenerateQueriesRequest) -> Optional[str]:
    """Resolve target kind explicitly or via deployment/ontology metadata (never name suffix heuristics)."""
    if req.target_kind and req.target_kind.strip():
        return req.target_kind.strip().lower()

    target_norm = req.target_table_or_view.strip().strip('"').upper()
    if not target_norm:
        return None

    def _matches_view_name(candidate: Optional[str]) -> bool:
        if not candidate:
            return False
        cand_norm = str(candidate).strip().strip('"').upper()
        if cand_norm == target_norm:
            return True
        return cand_norm.split(".")[-1] == target_norm.split(".")[-1] and "." not in req.target_table_or_view

    if req.project_id:
        try:
            from semabridge.repository.model_repository import ModelRepository

            repo = ModelRepository()
            dep_info = repo.get_project_deployment_info(req.project_id)
            if _matches_view_name(dep_info.get("semantic_view")) or _matches_view_name(dep_info.get("view_name")):
                return "semantic_view"
        except Exception as exc:
            logger.debug("Deployment info lookup skipped for project %s: %s", req.project_id, exc)

        try:
            from semabridge.ontology.store import ontology_store

            ont = ontology_store.load_ontology(req.project_id)
            if ont is not None:
                for dom in getattr(ont, "domains", []) or []:
                    if _matches_view_name(getattr(dom, "semantic_view_name", None)):
                        return "semantic_view"
                    for m in getattr(dom, "metrics", []) or []:
                        if _matches_view_name(getattr(m, "semantic_view_name", None)):
                            return "semantic_view"
        except Exception as exc:
            logger.debug("Ontology store lookup skipped for project %s: %s", req.project_id, exc)
    else:
        try:
            from semabridge.ontology.store import ontology_store

            fed = ontology_store.get_federated_ontology()
            for dom in getattr(fed, "domains", {}).values():
                if _matches_view_name(getattr(dom, "semantic_view_name", None)):
                    return "semantic_view"
                for m in getattr(dom, "metrics", []) or []:
                    if _matches_view_name(getattr(m, "semantic_view_name", None)):
                        return "semantic_view"
        except Exception as exc:
            logger.debug("Federated ontology view lookup skipped: %s", exc)

    return None


_QUALIFIED_IDENT_RE = re.compile(
    r'^(?:[A-Za-z_][A-Za-z0-9_$]*|"[^"]+")\.(?:[A-Za-z_][A-Za-z0-9_$%]*|"[^"]+")$'
)
_DIM_IDENT_RE = re.compile(
    r'^(?:(?:[A-Za-z0-9_\'$]+|"[^"]+")\.)?(?:[A-Za-z0-9_\]$%]+|"[^"]+")$'
)
_FORBIDDEN_SQL_STMT_RE = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|MERGE)\b",
    re.IGNORECASE,
)


def _get_project_domains_for_validation(project_id: Optional[str]) -> List[Any]:
    pid = (project_id or "").strip()
    if not pid:
        return []
    try:
        from semabridge.ontology.store import ontology_store

        p_domains = ontology_store.get_project_domains(pid)
        if p_domains:
            return list(p_domains)
        pid_cf = pid.casefold()
        fed = ontology_store.get_federated_ontology()
        matched = [
            d
            for d in fed.domains.values()
            if (getattr(d, "source_project_id", None) or "").strip().casefold() == pid_cf
            or (getattr(d, "id", None) or "").strip().casefold() == pid_cf
            or d.domain.strip().casefold() == pid_cf
        ]
        if matched:
            return matched
        try:
            from semabridge.repository.model_repository import ModelRepository

            model_name = (ModelRepository().get_project_deployment_info(pid).get("model_name") or "").strip()
            if model_name:
                return [d for d in fed.domains.values() if d.domain.strip().casefold() == model_name.casefold()]
        except Exception:
            pass
    except Exception:
        return []
    return []


def _validate_generate_queries_request(req: GenerateQueriesRequest, resolved_kind: Optional[str]) -> None:
    """Validate formula and dimension inputs strictly before building reconciliation queries."""
    expr = (req.metric_sql_expression or "").strip()
    target = req.target_table_or_view.strip()
    if ";" in target or any(ch.isspace() for ch in target.replace('"', "")):
        raise HTTPException(
            status_code=400,
            detail="target_table_or_view must be a valid table or semantic view identifier without spaces.",
        )

    dims = [d.strip() for d in (req.dimensions or []) if d and d.strip()]
    project_domains = _get_project_domains_for_validation(req.project_id)

    # Build concept -> attribute & metric lookup from project domains when available
    concept_attrs: Dict[str, Set[str]] = {}
    project_metrics: Dict[str, Set[str]] = {}
    for dom in project_domains:
        for c in getattr(dom, "concepts", []) or []:
            c_keys = {
                normalize_separator_ident(c.name),
                normalize_separator_ident(getattr(c, "logical_name", None)),
            } - {""}
            col_names: Set[str] = set()
            for a in getattr(c, "attributes", []) or []:
                if getattr(a, "name", None):
                    col_names.add(a.name)
                if getattr(a, "logical_name", None):
                    col_names.add(a.logical_name)
            for ck in c_keys:
                concept_attrs.setdefault(ck, set()).update(col_names)
        for m in getattr(dom, "metrics", []) or []:
            m_c = normalize_separator_ident(getattr(m, "concept", None) or "")
            for cand in (m.name, getattr(m, "logical_name", None), m.label):
                if cand:
                    project_metrics.setdefault(m_c, set()).add(normalize_separator_ident(cand, pct_word=True))

    if (resolved_kind or "").strip().lower() == "semantic_view":
        sv_metric = expr or req.metric_name.strip()
        if not _QUALIFIED_IDENT_RE.fullmatch(sv_metric):
            raise HTTPException(
                status_code=400,
                detail=(
                    "For a semantic view target, SQL Formula must be a TABLE.METRIC identifier "
                    "(e.g. ENTITY.METRIC_NAME), not free text or a raw expression."
                ),
            )
        m_tbl, m_col = [p.strip('"') for p in sv_metric.split(".", 1)]
        if project_metrics:
            tbl_metrics = project_metrics.get(normalize_separator_ident(m_tbl), set())
            all_metrics = {mk for s in project_metrics.values() for mk in s}
            m_norm = normalize_separator_ident(m_col, pct_word=True)
            if m_norm not in (tbl_metrics or all_metrics):
                raise HTTPException(
                    status_code=400,
                    detail=f"Metric '{sv_metric}' was not found in the selected project's semantic model.",
                )

        for d in dims:
            if not _QUALIFIED_IDENT_RE.fullmatch(d):
                raise HTTPException(
                    status_code=400,
                    detail=f"Dimension '{d}' must be a valid TABLE.COLUMN identifier (no free text).",
                )
            d_tbl, d_col = [p.strip('"') for p in d.split(".", 1)]
            if concept_attrs:
                attrs = concept_attrs.get(normalize_separator_ident(d_tbl))
                if attrs is None:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Dimension table '{d_tbl}' in '{d}' does not exist in the selected project.",
                    )
                d_norm = normalize_separator_ident(d_col)
                norm_attrs = {normalize_separator_ident(a) for a in attrs}
                if d_norm not in norm_attrs and not strip_reserved_column_prefix(d_col, attrs):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Dimension column '{d}' does not exist on table '{d_tbl}' in the selected project.",
                    )
        return

    # Physical table validation: require a single valid SQL expression (reject free text or multi-statements)
    if not expr:
        raise HTTPException(status_code=400, detail="SQL formula expression must not be blank for a physical table target.")
    if ";" in expr or "--" in expr or "/*" in expr or _FORBIDDEN_SQL_STMT_RE.search(expr):
        raise HTTPException(
            status_code=400,
            detail="Formula must be a single SQL expression (no semicolons, comments, or full SQL statements).",
        )

    # Check balanced quotes and parentheses, and reject multi-word English prose
    depth = 0
    in_sq = False
    in_dq = False
    unquoted_chars: List[str] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if in_sq:
            if ch == "'" and i + 1 < len(expr) and expr[i + 1] == "'":
                i += 2
                continue
            if ch == "'":
                in_sq = False
        elif in_dq:
            if ch == '"' and i + 1 < len(expr) and expr[i + 1] == '"':
                i += 2
                continue
            if ch == '"':
                in_dq = False
        else:
            if ch == "'":
                in_sq = True
            elif ch == '"':
                in_dq = True
            else:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth < 0:
                        raise HTTPException(status_code=400, detail=f"Unbalanced ')' in SQL formula '{expr}'.")
                unquoted_chars.append(ch)
        i += 1
    if in_sq or in_dq or depth != 0:
        raise HTTPException(status_code=400, detail=f"Unbalanced quotes or parentheses in SQL formula '{expr}'.")

    unquoted_str = "".join(unquoted_chars)
    has_sql_syntax = bool(re.search(r"[().+\-*/=<>[\]]|\b(?:CASE|WHEN|THEN|ELSE|END|AND|OR|NOT|IS|IN)\b", unquoted_str, re.I))
    bare_words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", unquoted_str)
    if not has_sql_syntax and len(bare_words) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid SQL formula expression '{expr}': free text is not allowed; provide a valid SQL expression.",
        )

    for d in dims:
        if not _DIM_IDENT_RE.fullmatch(d):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dimension '{d}': expected COLUMN or TABLE.COLUMN identifier.",
            )
        if concept_attrs and "." in d:
            d_tbl, d_col = [p.strip('"') for p in d.split(".", 1)]
            attrs = concept_attrs.get(normalize_separator_ident(d_tbl))
            if attrs is not None:
                norm_attrs = {normalize_separator_ident(a) for a in attrs}
                if normalize_separator_ident(d_col) not in norm_attrs and not strip_reserved_column_prefix(d_col, attrs):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Dimension column '{d}' does not exist on table '{d_tbl}' in the selected project.",
                    )


@router.post("/generate-queries", response_model=Dict[str, str])
def generate_reconciliation_queries(req: GenerateQueriesRequest) -> Dict[str, str]:
    """Generate side-by-side DAX and SQL queries for golden metric reconciliation."""
    if not req.metric_name.strip() or not req.target_table_or_view.strip():
        raise HTTPException(status_code=400, detail="metric_name and target_table_or_view must not be blank.")
    verifier = GoldenQueryVerifier()
    resolved_kind = _resolve_target_kind(req)
    _validate_generate_queries_request(req, resolved_kind)
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
        target_kind=resolved_kind,
    )
    return {
        "dax_query": dax_query,
        "sql_query": sql_query,
        "target_kind": resolved_kind or "table",
    }


@router.post("/verify", response_model=VerificationResult)
def verify_metric_results(req: ReconcileDataRequest) -> VerificationResult:
    """Compare DAX vs SQL result sets, compute parity metrics, and persist project certifications."""
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
    if req.persist_certification and (req.project_id or req.domain or req.view_metric):
        tol = req.tolerance or verifier.tolerance
        save_certification_record(
            metric_name=req.metric_name,
            status=result.status,
            certification_badge=result.certification_badge,
            project_id=req.project_id,
            domain=req.domain,
            view_metric=req.view_metric,
            dimensions=req.dimension_keys,
            dax_query=req.dax_query,
            sql_query=req.sql_query,
            dax_row_count=len(req.dax_results),
            sql_row_count=len(req.sql_results),
            matched_rows=result.matched_rows,
            mismatched_rows=result.mismatched_rows,
            max_absolute_delta=result.max_absolute_delta,
            max_percentage_delta=result.max_percentage_delta,
            absolute_tolerance=tol.absolute_tolerance,
            relative_tolerance=tol.relative_tolerance,
            max_mismatch_percentage=tol.max_mismatch_percentage,
            certified_by=req.certified_by,
        )
    return result


@router.get("/dax-backends", response_model=Dict[str, Any])
def get_dax_backends() -> Dict[str, Any]:
    """Inspect availability of local Power BI Desktop, REST API, and XMLA DAX backends."""
    return list_available_backends()


@router.post("/run-dax", response_model=Dict[str, Any])
def execute_dax_endpoint(req: RunDaxRequest) -> Dict[str, Any]:
    """Execute a DAX query via pbi_desktop, rest_api, or xmla and return normalized rows."""
    try:
        res = execute_dax(
            req.query,
            backend=req.backend,
            port=req.port,
            dataset_id=req.dataset_id,
            workspace=req.workspace,
            workspace_id=req.workspace_id,
        )
        return {
            "success": res.success,
            "backend": res.backend,
            "columns": res.columns,
            "rows": res.rows,
            "row_count": len(res.rows),
            "execution_ms": res.execution_ms,
            "metadata": res.metadata,  # hardcoding-ok: response key
        }
    except DaxExecutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/certifications", response_model=Dict[str, Any])
def list_metric_certifications(
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> Dict[str, Any]:
    """List latest reconciliation certification records scoped optionally by project or domain."""
    records = get_latest_certifications(project_id=project_id, domain=domain)
    return {
        "project_id": project_id,
        "domain": domain,
        "count": len(records),
        "certifications": records,
    }


@router.post("/certify", response_model=Dict[str, Any])
def certify_metric_end_to_end(req: CertifyMetricRequest) -> Dict[str, Any]:
    """Execute SQL (Snowflake SEMANTIC_VIEW) + DAX (automated or supplied), verify parity, and persist certification."""
    from semabridge.ai.verified_queries_service import save_verified_query_record
    from semabridge.api.routers.ai_router import ExecuteQueryRequest, execute_semantic_query

    if not req.metric_name.strip() or not req.target_table_or_view.strip():
        raise HTTPException(status_code=400, detail="metric_name and target_table_or_view are required.")

    view_metric = (req.view_metric or req.metric_name).strip()
    verifier = GoldenQueryVerifier(default_tolerance=req.tolerance)
    sql_metric_token = view_metric if (req.target_kind or "").lower() == "semantic_view" else req.metric_name

    sql_query = verifier.generate_sql_query(
        metric_name=sql_metric_token,
        metric_sql_expression=req.metric_sql_expression or req.metric_name,
        target_table_or_view=req.target_table_or_view,
        dimensions=req.dimensions,
        filters=req.filters,
        target_kind=req.target_kind,
    )
    pbi_dims = req.pbi_dimension_columns or req.dimensions
    dax_query = verifier.generate_dax_query(
        metric_name=req.metric_name,
        dimensions=pbi_dims,
        filters=req.filters,
    )

    # 1. Execute Snowflake / SQL query live (non-simulated)
    exec_resp = execute_semantic_query(
        ExecuteQueryRequest(
            sql=sql_query,
            target="SNOWFLAKE",
            project_id=req.project_id,
            limit=100000,
            allow_simulated=False,
        )
    )
    if not exec_resp.success or exec_resp.is_simulated:
        raise HTTPException(
            status_code=400,
            detail=f"Live Snowflake execution failed: {exec_resp.error or exec_resp.message or 'unknown error'}",
        )

    raw_sf_rows = [dict(zip(exec_resp.columns, r)) for r in exec_resp.rows]
    vm_col = view_metric.split(".")[-1].upper()
    sql_rows: List[Dict[str, Any]] = []
    for row in raw_sf_rows:
        up = {str(k).upper(): v for k, v in row.items()}
        mapped: Dict[str, Any] = {req.metric_name: up.get(vm_col)}
        for vd, pc in zip(req.dimensions, pbi_dims):
            mapped[pc] = up.get(vd.split(".")[-1].upper())
        sql_rows.append(mapped)

    # 2. Obtain DAX rows via supplied export or automated run_dax backend
    dax_backend_used = "manual_rows"
    if req.dax_results is not None:
        dax_rows = req.dax_results
    else:
        try:
            dax_exec = execute_dax(
                dax_query,
                backend=req.dax_backend,
                port=req.dax_port,
                dataset_id=req.dax_dataset_id,
                workspace=req.dax_workspace,
                workspace_id=req.dax_workspace_id,
            )
            dax_rows = dax_exec.rows
            dax_backend_used = dax_exec.backend
        except DaxExecutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 3. Verify mathematical parity and persist certification record
    result = verifier.reconcile_data(
        metric_name=req.metric_name,
        dax_rows=dax_rows,
        sql_rows=sql_rows,
        dimension_keys=pbi_dims,
        dax_query=dax_query,
        sql_query=sql_query,
        custom_tolerance=req.tolerance,
    )
    tol = req.tolerance or verifier.tolerance
    cert_record = save_certification_record(
        metric_name=req.metric_name,
        status=result.status,
        certification_badge=result.certification_badge,
        project_id=req.project_id,
        domain=req.domain,
        view_metric=view_metric,
        dimensions=pbi_dims,
        dax_query=dax_query,
        sql_query=sql_query,
        dax_row_count=len(dax_rows),
        sql_row_count=len(sql_rows),
        matched_rows=result.matched_rows,
        mismatched_rows=result.mismatched_rows,
        max_absolute_delta=result.max_absolute_delta,
        max_percentage_delta=result.max_percentage_delta,
        absolute_tolerance=tol.absolute_tolerance,
        relative_tolerance=tol.relative_tolerance,
        max_mismatch_percentage=tol.max_mismatch_percentage,
        certified_by=req.certified_by,
    )

    verified_entry = None
    if req.register_verified_query and result.status == "CERTIFIED":
        question_text = (req.verified_question or "").strip() or (
            f"What is {req.metric_name}" + (f" by {', '.join(pbi_dims)}" if pbi_dims else "") + "?"
        )
        verified_entry = save_verified_query_record(
            question=question_text,
            sql=sql_query,
            target="SNOWFLAKE",
            project_id=req.project_id,
            domain=req.domain,
        )

    return {
        "verification": result.model_dump(),
        "certification": cert_record,
        "dax_backend": dax_backend_used,
        "verified_query_registered": verified_entry is not None,
        "verified_query": verified_entry,
    }
