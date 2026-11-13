"""REST API Router for the SemaBridge AI layer (AI Studio).

* ``/nl-query``      — Snowflake targets use Cortex Analyst against the selected
  project's deployed, fully qualified semantic view. When Cortex is unavailable
  the caller gets an explicit "Cortex unavailable" answer; the local engine is
  only used for an offline draft when the user asks for one.
* ``/execute-query`` — every statement passes the SQL guard; only read-only
  SELECT / WITH … SELECT runs, on the project's own warehouse connection.
  Nothing is ever fabricated: without a warehouse the answer is "not executed".
* ``/save-verified-query`` — requires an explicit user confirmation and a prior
  successful live execution; stored per project in the database.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from semabridge.ai.nl_query_engine import QueryResolutionPlan
from semabridge.ai.project_connection import (
    ProjectScope,
    resolve_project_scope,
    resolve_project_warehouse_config,
)
from semabridge.ai.semantic_search import SearchHit
from semabridge.ai.verified_queries_service import (
    list_verified_queries_for_project,
    record_query_execution,
    save_verified_query as db_save_verified_query,
)
from semabridge.connectors.sql_guard import (
    SQLSecurityGuardError,
    split_statements,
    strip_sql_comments,
    validate_safe_sql,
    validate_read_only_sql,
)
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.ai_router")

router = APIRouter(prefix="/api/ai", tags=["semabridge_ai"])

_NOT_EXECUTED = "not executed: no warehouse connection"
_SELECT_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_WITH_SELECT_RE = re.compile(r"^\s*WITH\b.*\)\s*SELECT\b", re.IGNORECASE | re.DOTALL)


def _strip_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


class AISearchRequest(BaseModel):
    query: str
    project_id: Optional[str] = None
    domain: Optional[str] = None
    item_type: Optional[str] = None  # 'CONCEPT', 'METRIC', 'ATTRIBUTE'
    limit: int = Field(default=10, ge=1, le=200)
    min_score: float = Field(default=0.15, ge=0.0)


class NLQueryRequest(BaseModel):
    prompt: str
    dialect: str = "SNOWFLAKE"  # 'SNOWFLAKE', 'DATABRICKS', 'DUCKDB'
    target: Optional[str] = None  # 'SNOWFLAKE' | 'DATABRICKS' | 'LOCAL'
    project_id: Optional[str] = None
    view_name: Optional[str] = None
    semantic_view: Optional[str] = None
    allow_offline_draft: bool = False


class ExecuteQueryRequest(BaseModel):
    sql: str
    target: str = "SNOWFLAKE"  # 'SNOWFLAKE' | 'DATABRICKS' | 'LOCAL'
    project_id: Optional[str] = None
    question: Optional[str] = None
    view_name: Optional[str] = None
    user_id: Optional[str] = None
    limit: int = Field(default=50, ge=1)
    # Kept for request compatibility; simulated data is never produced.
    allow_simulated: bool = False


class ExecuteQueryResponse(BaseModel):
    success: bool
    target: str
    columns: List[str] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time_ms: int = 0
    executed_sql: str = ""
    error: Optional[str] = None
    is_simulated: bool = False
    message: Optional[str] = None
    dialect: str = "SNOWFLAKE"


class SaveVerifiedQueryRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str
    question: str
    sql: str
    confirmed: bool = False  # explicit "Is this answer correct?" confirmation
    name: Optional[str] = None
    target: str = "SNOWFLAKE"  # 'SNOWFLAKE' | 'DATABRICKS'
    view_name: Optional[str] = None
    is_onboarding: bool = False
    domain: Optional[str] = None
    require_certified: bool = False

    @field_validator("project_id", "question", "sql")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class SaveVerifiedQueryResponse(BaseModel):
    success: bool
    message: str
    target: str
    artifact_path: Optional[str] = None
    verified_query: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Request identity / authorization helpers
# ---------------------------------------------------------------------------

def _request_user_id(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    uid = getattr(request.state, "user_id", None)
    return str(uid) if uid else None


def _split_identifier(name: str) -> List[str]:
    """Split a (possibly quoted) dotted identifier; dots inside quotes are kept."""
    parts: List[str] = []
    buf: List[str] = []
    in_q = False
    for ch in str(name or "").strip():
        if ch == '"':
            in_q = not in_q
            buf.append(ch)
        elif ch == "." and not in_q:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _qualify_view_name(view: Optional[str], project_id: Optional[str]) -> Optional[str]:
    """Qualify a caller-supplied view name with the project's deployment db/schema.

    ``VIEW`` -> ``DB.SCHEMA.VIEW`` and ``SCHEMA.VIEW`` -> ``DB.SCHEMA.VIEW``;
    already fully qualified names, or projects without a known deployment
    target, are returned unchanged.
    """
    text = str(view or "").strip()
    if not text or not project_id:
        return view
    parts = _split_identifier(text)
    if len(parts) >= 3:
        return text
    try:
        from semabridge.repository.model_repository import ModelRepository

        info = ModelRepository().get_project_deployment_info(project_id)
    except Exception as exc:
        logger.warning("Could not resolve deployment target of project %s to qualify view %r: %s", project_id, text, exc)
        return text
    db, schema = info.get("database"), info.get("schema")

    def _q(p: str) -> str:
        p = str(p).strip()
        if p.startswith('"'):
            return p
        return f'"{p}"' if re.search(r"[^A-Za-z0-9_$]", p) else p

    if len(parts) == 1 and db and schema:
        return ".".join([_q(db), _q(schema), parts[0]])
    if len(parts) == 2 and db:
        return ".".join([_q(db), *parts])
    logger.info("View %r for project %s left unqualified: no deployment database/schema known", text, project_id)
    return text


def _scope_for(project_id: Optional[str], explicit_view: Optional[str] = None):
    """resolve_project_scope + qualification of a caller-supplied view name."""
    scope = resolve_project_scope(project_id, explicit_view=explicit_view)
    if explicit_view and scope.semantic_view:
        scope.semantic_view = _qualify_view_name(scope.semantic_view, project_id)
    return scope


def _request_user_label(request: Optional[Request]) -> str:
    """Real authenticated user name (falls back to the id)."""
    uid = _request_user_id(request)
    if not uid:
        return ""
    try:
        from semabridge.api.deps import get_current_user_optional

        user = get_current_user_optional(request)
        if user is not None:
            return str(getattr(user, "username", None) or getattr(user, "email", None) or uid)
    except Exception as exc:
        logger.warning("Could not resolve user name for id %s (using the id): %s", uid, exc)
    return uid


def _require_project_access(project_id: Optional[str], request: Optional[Request]) -> None:
    if not project_id or request is None:
        return
    from semabridge.api.services.project_ownership_service import is_project_owned_by_user

    if not is_project_owned_by_user(project_id, _request_user_id(request), log_prefix="AIRouter"):
        raise HTTPException(status_code=403, detail="You do not have access to this project.")


# ---------------------------------------------------------------------------
# Status / search
# ---------------------------------------------------------------------------

@router.get("/status", response_model=Dict[str, Any])
def get_ai_status() -> Dict[str, Any]:
    """Retrieve indexing statistics and status of the embedded AI engine."""
    index = ontology_store.search_index
    fed = ontology_store.get_federated_ontology()
    return {
        "status": "ready",
        "engine": "SemaBridge Multi-Target AI & Semantic Compiler",
        "supported_targets": ["SNOWFLAKE (Cortex Analyst)", "DATABRICKS (AI/BI Genie)", "LOCAL (offline draft)"],
        "total_indexed_documents": len(index.docs),
        "total_domains": len(fed.domains),
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "vector_dimensions": index.vector_dim,
    }


@router.post("/search", response_model=List[SearchHit])
def search_semantic_layer(req: AISearchRequest, request: Request) -> List[SearchHit]:
    """Hybrid search over concepts, metrics and attributes, scoped to the selected project."""
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")
    _require_project_access(req.project_id, request)
    scope = resolve_project_scope(req.project_id)
    return ontology_store.search_index.search(
        query=req.query,
        domain_filter=req.domain,
        item_type_filter=req.item_type,
        limit=req.limit,
        min_score=req.min_score,
        allowed_domains=scope.allowed_domains,
    )


# ---------------------------------------------------------------------------
# Natural language → SQL
# ---------------------------------------------------------------------------

def _normalize_dialect(req: NLQueryRequest) -> str:
    dialect = (req.target or req.dialect or "SNOWFLAKE").upper()
    if dialect == "CORTEX":
        return "SNOWFLAKE"
    if dialect in ("GENIE", "UNITY_CATALOG"):
        return "DATABRICKS"
    return dialect


def _empty_plan(prompt: str, explanation: str, warnings: List[str], tables: Optional[List[str]] = None) -> QueryResolutionPlan:
    return QueryResolutionPlan(
        original_prompt=prompt,
        detected_metrics=[],
        detected_dimensions=[],
        resolved_tables=tables or [],
        generated_sql="",
        confidence_score=0.0,
        explanation=explanation,
        warnings=warnings,
    )


def _try_cortex(prompt: str, scope: ProjectScope) -> tuple[Optional[QueryResolutionPlan], Optional[str]]:
    """Ask Cortex Analyst; returns (plan, error)."""
    if not scope.semantic_view:
        if not scope.project_id:
            return None, "select a project in Scope to query its deployed Snowflake semantic view"
        reason = scope.deploy_error or "the project has no deployed semantic view"
        return None, f"semantic view unknown: {reason}"
    try:
        from semabridge.ai.cortex_analyst_client import call_cortex_analyst

        sf = resolve_project_warehouse_config(scope.project_id, "SNOWFLAKE")
        if sf is None or not getattr(sf, "private_key", None):
            return None, "the project's Snowflake connection has no key-pair private key"
        resp = call_cortex_analyst(prompt=prompt, semantic_view=scope.semantic_view, config=sf)
        sql = (resp.get("generated_sql") or "").strip()
        if not sql:
            return None, resp.get("explanation") or "Cortex Analyst returned no SQL statement"
        validate_safe_sql(sql)
        plan = QueryResolutionPlan(
            original_prompt=prompt,
            detected_metrics=[],
            detected_dimensions=[],
            resolved_tables=[scope.semantic_view],
            generated_sql=sql,
            confidence_score=0.95,
            explanation=f"[Snowflake Cortex Analyst] {resp.get('explanation', '')}".strip(),
            warnings=[],
        )
        return plan, None
    except Exception as exc:
        logger.warning("Cortex Analyst unavailable: %s", exc)
        return None, str(exc)


def _local_plan(engine, prompt: str, dialect: str, scope: ProjectScope, offline_label: bool) -> QueryResolutionPlan:
    plan = engine.resolve_query(
        prompt,
        dialect=dialect,
        semantic_view=scope.semantic_view,
        allowed_domains=scope.allowed_domains,
        deployment_error=scope.deploy_error,
    )
    if offline_label:
        plan.explanation = f"[Offline Draft - Local Engine] {plan.explanation}"
        plan.warnings.append("Offline draft generated by the local engine at the user's request; not produced by Cortex Analyst.")
    return plan


def is_query_verified_by_execution(sql: str, project_id: Optional[str] = None) -> bool:
    """Return True when ``sql`` has a successful live execution receipt in the database."""
    from semabridge.ai.verified_queries_service import has_successful_execution

    return has_successful_execution(sql, project_id=project_id)


def clear_verified_executions(project_id: Optional[str] = None) -> None:
    """Clear recorded execution receipts (test helper)."""
    try:
        from semabridge.repository.orm.ai_models import AIQueryExecutionRow
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            q = session.query(AIQueryExecutionRow)
            if project_id:
                q = q.filter(AIQueryExecutionRow.project_id == project_id)
            q.delete()
            session.commit()
    except Exception as exc:
        logger.warning("Could not clear verified execution receipts (project=%s): %s", project_id, exc)


@router.post("/nl-query", response_model=QueryResolutionPlan)
def resolve_natural_language_query(req: NLQueryRequest, request: Request) -> QueryResolutionPlan:
    """Translate a question into governed SQL for the selected project."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")
    engine = ontology_store.query_engine
    if not engine:
        raise HTTPException(status_code=500, detail="NL Query Engine not initialized.")
    _require_project_access(req.project_id, request)

    dialect = _normalize_dialect(req)
    scope = _scope_for(req.project_id, explicit_view=req.semantic_view or req.view_name)

    explicit_local = (req.target or "").upper() == "LOCAL"
    if dialect == "SNOWFLAKE" and not explicit_local:
        plan, cortex_error = _try_cortex(req.prompt, scope)
        if plan is not None:
            return plan
        if not req.allow_offline_draft:
            return _empty_plan(
                req.prompt,
                "Cortex unavailable. You can generate an offline draft with the local engine.",
                [f"Cortex unavailable: {cortex_error}"],
                tables=[scope.semantic_view] if scope.semantic_view else [],
            )
        if scope.allowed_domains is not None and not scope.allowed_domains:
            return _empty_plan(req.prompt, "The selected project has no ontology domains attached or generated yet.", ["no project domains"])
        return _local_plan(engine, req.prompt, dialect, scope, offline_label=True)

    if scope.allowed_domains is not None and not scope.allowed_domains:
        return _empty_plan(req.prompt, "The selected project has no ontology domains attached or generated yet.", ["no project domains"])
    return _local_plan(engine, req.prompt, dialect, scope, offline_label=explicit_local or req.allow_offline_draft)


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def _guard_statements(exec_sql: str) -> None:
    """Every statement must pass the SQL guard and be a read-only SELECT."""
    statements = [strip_sql_comments(s).strip() for s in split_statements(exec_sql)]
    statements = [s for s in statements if s]
    if not statements:
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")
    for stmt in statements:
        try:
            validate_safe_sql(stmt)
            validate_read_only_sql(stmt)
        except SQLSecurityGuardError as sge:
            logger.warning("SQL execution guard rejected query: %s", sge)
            raise HTTPException(status_code=400, detail=str(sge))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"SQL validation error: {exc}")
        if not (_SELECT_RE.match(stmt) or _WITH_SELECT_RE.match(stmt)):
            raise HTTPException(status_code=400, detail="Only read-only SELECT statements are permitted.")


def _not_executed(target: str, sql: str, reason: str, elapsed_ms: int = 0) -> ExecuteQueryResponse:
    return ExecuteQueryResponse(
        success=False,
        target=target,
        execution_time_ms=elapsed_ms,
        executed_sql=sql,
        is_simulated=False,
        error=f"{_NOT_EXECUTED}: {reason}",
        message=_NOT_EXECUTED,
        dialect=target,
    )


def _failed(target: str, sql: str, exc: Exception, elapsed_ms: int) -> ExecuteQueryResponse:
    return ExecuteQueryResponse(
        success=False,
        target=target,
        execution_time_ms=elapsed_ms,
        executed_sql=sql,
        is_simulated=False,
        error=str(exc),
        message=f"{target.title()} query execution failed: {exc}",
        dialect=target,
    )


def _jsonable_rows(raw_rows) -> List[List[Any]]:
    return [
        [v if v is None or isinstance(v, (int, float, bool, str)) else str(v) for v in r]
        for r in raw_rows
    ]


def _record(req: ExecuteQueryRequest, target: str, clean_sql: str, row_count: int, elapsed_ms: int, user_id: Optional[str]) -> None:
    try:
        record_query_execution(
            clean_sql,
            target=target,
            row_count=row_count,
            success=True,
            is_simulated=False,
            project_id=req.project_id,
            question=req.question or "",
            view_name=req.view_name,
            user_id=user_id or req.user_id,
            execution_time_ms=elapsed_ms,
        )
    except Exception as exc:
        logger.warning("Failed to record %s query execution: %s", target, exc)


def _execute_snowflake(
    req: ExecuteQueryRequest,
    clean_sql: str,
    exec_sql: str,
    user_id: Optional[str],
    owner_id: Optional[str] = None,
) -> ExecuteQueryResponse:
    t0 = time.perf_counter()
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

    try:
        sf = resolve_project_warehouse_config(req.project_id, "SNOWFLAKE", owner_id=owner_id)
        if sf is None:
            return _not_executed("SNOWFLAKE", exec_sql, "no Snowflake connection configured.")
        connect_kwargs = get_snowflake_connect_kwargs(sf)
        if not connect_kwargs.get("account") or not connect_kwargs.get("user"):
            return _not_executed("SNOWFLAKE", exec_sql, "missing Snowflake account or user.")
    except ValueError as cfg_exc:
        return _not_executed("SNOWFLAKE", exec_sql, str(cfg_exc))

    try:
        import snowflake.connector

        conn = snowflake.connector.connect(**connect_kwargs)
        try:
            cur = conn.cursor()
            cur.execute(exec_sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = _jsonable_rows(cur.fetchall())
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Live Snowflake query execution failed: %s", exc)
        return _failed("SNOWFLAKE", exec_sql, exc, int((time.perf_counter() - t0) * 1000))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    _record(req, "SNOWFLAKE", clean_sql, len(rows), elapsed_ms, user_id)
    return ExecuteQueryResponse(
        success=True, target="SNOWFLAKE", columns=cols, rows=rows, row_count=len(rows),
        execution_time_ms=elapsed_ms, executed_sql=exec_sql, is_simulated=False,
        message="Query executed live on Snowflake warehouse.", dialect="SNOWFLAKE",
    )


def _execute_databricks(
    req: ExecuteQueryRequest,
    clean_sql: str,
    exec_sql: str,
    user_id: Optional[str],
    owner_id: Optional[str] = None,
) -> ExecuteQueryResponse:
    import requests as _requests

    t0 = time.perf_counter()
    try:
        from semabridge.connectors.factory import make_target_emitter

        db_cfg = resolve_project_warehouse_config(req.project_id, "DATABRICKS", owner_id=owner_id)
        if not db_cfg or not db_cfg.host or not db_cfg.warehouse_id:
            return _not_executed("DATABRICKS", exec_sql, "missing Databricks host or warehouse.")
        token = make_target_emitter("databricks", db_cfg)._resolve_token()
        if not token:
            return _not_executed("DATABRICKS", exec_sql, "missing Databricks token.")
        resp = _requests.post(
            f"https://{db_cfg.host}/api/2.0/sql/statements",
            json={"statement": exec_sql, "warehouse_id": db_cfg.warehouse_id, "wait_timeout": "15s"},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=20,
        )
    except Exception as exc:
        logger.warning("Live Databricks query execution failed: %s", exc)
        return _failed("DATABRICKS", exec_sql, exc, int((time.perf_counter() - t0) * 1000))

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if resp.status_code != 200:
        logger.warning("Databricks Statements API error %s: %s", resp.status_code, resp.text[:200])
        return _failed("DATABRICKS", exec_sql, RuntimeError(f"Databricks API returned HTTP {resp.status_code}"), elapsed_ms)
    data = resp.json()
    state = (data.get("status") or {}).get("state")
    if state and state not in ("SUCCEEDED",):
        err = ((data.get("status") or {}).get("error") or {}).get("message") or f"statement state {state}"
        return _failed("DATABRICKS", exec_sql, RuntimeError(err), elapsed_ms)
    cols = [c.get("name") for c in (data.get("manifest") or {}).get("schema", {}).get("columns", [])]
    rows = (data.get("result") or {}).get("data_array", []) or []
    _record(req, "DATABRICKS", clean_sql, len(rows), elapsed_ms, user_id)
    return ExecuteQueryResponse(
        success=True, target="DATABRICKS", columns=cols, rows=rows, row_count=len(rows),
        execution_time_ms=elapsed_ms, executed_sql=exec_sql, is_simulated=False,
        message="Query executed live on Databricks SQL Warehouse.", dialect="DATABRICKS",
    )


def execute_semantic_query(
    req: ExecuteQueryRequest,
    user_id: Optional[str] = None,
    owner_id: Optional[str] = None,
) -> ExecuteQueryResponse:
    """Guard and execute SQL on the project's warehouse (no fabricated data)."""
    if not req.sql or not req.sql.strip():
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")
    clean_sql = req.sql.strip().rstrip(";").strip()
    exec_sql = strip_sql_comments(clean_sql).strip()
    if not exec_sql:
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")
    _guard_statements(exec_sql)
    if not re.search(r"\blimit\s+\d+\s*$", exec_sql, re.IGNORECASE):
        exec_sql = f"{exec_sql} LIMIT {int(req.limit)}"

    target = (req.target or "SNOWFLAKE").upper()
    if target in ("SNOWFLAKE", "CORTEX"):
        return _execute_snowflake(req, clean_sql, exec_sql, user_id, owner_id=owner_id)
    if target in ("DATABRICKS", "GENIE"):
        return _execute_databricks(req, clean_sql, exec_sql, user_id, owner_id=owner_id)
    return _not_executed("LOCAL", exec_sql, "the local engine has no external warehouse connection.")


@router.post("/execute-query", response_model=ExecuteQueryResponse)
def execute_query_endpoint(req: ExecuteQueryRequest, request: Request) -> ExecuteQueryResponse:
    """Execute read-only semantic SQL on the selected project's warehouse."""
    _require_project_access(req.project_id, request)
    if req.limit > 1000:
        req.limit = 1000
    return execute_semantic_query(
        req,
        user_id=_request_user_label(request) or None,
        owner_id=_request_user_id(request),
    )


# ---------------------------------------------------------------------------
# Verified queries
# ---------------------------------------------------------------------------

def _check_certification(req: SaveVerifiedQueryRequest) -> None:
    from semabridge.reconciliation.certification_service import check_sql_certification_status

    status = check_sql_certification_status(req.sql, project_id=req.project_id)
    if status["failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Only certified metrics can become verified queries. Reconciliation failed for: {', '.join(status['failed'])}.",
        )
    if status["uncertified"] and (req.require_certified or status["has_any_certified"]):
        raise HTTPException(
            status_code=400,
            detail=f"Only certified metrics can become verified queries. Uncertified metric(s): {', '.join(status['uncertified'])}.",
        )


@router.post("/save-verified-query", response_model=SaveVerifiedQueryResponse)
def save_verified_query(req: SaveVerifiedQueryRequest, request: Request) -> SaveVerifiedQueryResponse:
    """Save a verified query after a real run and explicit user confirmation."""
    if not req.confirmed:
        raise HTTPException(status_code=400, detail="Explicit user confirmation is required to save a verified query.")
    _require_project_access(req.project_id, request)
    _check_certification(req)
    scope = _scope_for(req.project_id, explicit_view=req.view_name)
    try:
        row = db_save_verified_query(
            project_id=req.project_id,
            question=req.question,
            sql=req.sql,
            confirmed=True,
            user_id=_request_user_label(request),
            name=req.name,
            view_name=scope.semantic_view,
            is_onboarding=req.is_onboarding,
            target=req.target,
            domain=req.domain,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error("Failed to save verified query: %s", exc)
        raise HTTPException(status_code=500, detail=f"Database error saving verified query: {exc}")

    return SaveVerifiedQueryResponse(
        success=True,
        message=(
            f"Verified query '{row.name}' saved for project '{req.project_id}'. "
            "It will be deployed in the semantic view's AI_VERIFIED_QUERIES clause on the next Upsert."
        ),
        target=req.target.upper(),
        artifact_path=f"database:ai_verified_queries/{row.id}",
        verified_query={
            "id": row.id,
            "name": row.name,
            "question": row.question,
            "sql": row.sql,
            "verified_by": row.verified_by,
            "verified_at": row.verified_at,
            "is_onboarding": row.is_onboarding,
        },
    )


@router.get("/verified-queries", response_model=Dict[str, Any])
def get_verified_queries(request: Request, project_id: Optional[str] = None) -> Dict[str, Any]:
    """List verified queries for exactly one project."""
    pid = (project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="project_id query parameter is required.")
    _require_project_access(pid, request)
    queries = list_verified_queries_for_project(pid)
    return {"project_id": pid, "count": len(queries), "queries": queries}


@router.post("/reindex", response_model=Dict[str, Any])
def reindex_search_engine() -> Dict[str, Any]:
    """Manually trigger a full re-index of the semantic search engine."""
    ontology_store._refresh_index()
    return {"success": True, "indexed_documents": len(ontology_store.search_index.docs)}
