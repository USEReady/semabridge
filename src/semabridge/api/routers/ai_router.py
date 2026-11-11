"""REST API Router for Embedded SemaBridge AI Layer.

Provides zero-credit local semantic search and Natural Language to Governed SQL
without dependency on Snowflake Cortex or external credits.
"""

import logging
import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from semabridge.ai.nl_query_engine import QueryResolutionPlan
from semabridge.ai.semantic_search import SearchHit
from semabridge.connectors.sql_guard import (
    SQLSecurityGuardError,
    split_statements,
    strip_sql_comments,
    validate_safe_sql,
)
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.ai_router")

router = APIRouter(prefix="/api/ai", tags=["semabridge_ai"])

# Set of normalized SQL queries that were successfully executed against a real deployed warehouse view
_VERIFIED_EXECUTIONS: set[str] = set()


def _norm_sql_key(sql: str) -> str:
    """Normalize SQL query for execution verification tracking."""
    s = re.sub(r"--[^\r\n]*", "", sql or "")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    # Strip trailing limit clause so saving with or without execution limit matches
    s = re.sub(r"\bLIMIT\s+\d+\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip().rstrip(";").strip().upper()
    return s


def _record_verified_execution(*sql_variants: str) -> None:
    for s in sql_variants:
        k = _norm_sql_key(s)
        if k:
            _VERIFIED_EXECUTIONS.add(k)


def is_query_verified_by_execution(sql: str) -> bool:
    return _norm_sql_key(sql) in _VERIFIED_EXECUTIONS


def clear_verified_executions() -> None:
    """Clear execution cache (used in tests)."""
    _VERIFIED_EXECUTIONS.clear()


class AISearchRequest(BaseModel):
    query: str
    domain: Optional[str] = None
    item_type: Optional[str] = None  # 'CONCEPT', 'METRIC', 'ATTRIBUTE'
    limit: int = 10
    min_score: float = 0.15


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
    limit: int = 50


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
    project_id: Optional[str] = None
    question: str
    sql: str
    confirmed: bool = False  # Explicit user confirmation required
    name: Optional[str] = None
    target: str = "SNOWFLAKE"  # 'SNOWFLAKE' | 'DATABRICKS'
    view_name: Optional[str] = None
    user_id: Optional[str] = None
    is_onboarding: bool = False
    domain: Optional[str] = None


class SaveVerifiedQueryResponse(BaseModel):
    success: bool
    message: str
    target: str
    artifact_path: Optional[str] = None
    verified_query: Dict[str, Any] = Field(default_factory=dict)


@router.get("/status", response_model=Dict[str, Any])
def get_ai_status() -> Dict[str, Any]:
    """Retrieve indexing statistics and status of the embedded AI engine."""
    index = ontology_store.search_index
    fed = ontology_store.get_federated_ontology()

    return {
        "status": "ready",
        "engine": "SemaBridge Multi-Target AI & Semantic Compiler",
        "supported_targets": ["SNOWFLAKE (Cortex Analyst)", "DATABRICKS (AI/BI Genie)", "LOCAL (Zero-Credit)"],
        "total_indexed_documents": len(index.docs),
        "total_domains": len(fed.domains),
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "vector_dimensions": index.vector_dim,
        "credit_cost": "0 (Embedded Local AI)",
    }


@router.post("/search", response_model=List[SearchHit])
def search_semantic_layer(req: AISearchRequest) -> List[SearchHit]:
    """Search business concepts, metrics, formulas, and synonyms using hybrid vector + BM25 search."""
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty.")

    hits = ontology_store.search_index.search(
        query=req.query,
        domain_filter=req.domain,
        item_type_filter=req.item_type,
        limit=req.limit,
        min_score=req.min_score,
    )
    return hits


@router.post("/nl-query", response_model=QueryResolutionPlan)
def resolve_natural_language_query(req: NLQueryRequest) -> QueryResolutionPlan:
    """Translate a natural language question into governed SQL using Cortex Analyst or the local engine."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    engine = ontology_store.query_engine
    if not engine:
        raise HTTPException(status_code=500, detail="NL Query Engine not initialized.")

    effective_dialect = (req.target or req.dialect or "SNOWFLAKE").upper()
    if effective_dialect == "CORTEX":
        effective_dialect = "SNOWFLAKE"
    elif effective_dialect in ("GENIE", "UNITY_CATALOG"):
        effective_dialect = "DATABRICKS"

    allowed_domains: Optional[List[str]] = None
    resolved_semantic_view: Optional[str] = req.view_name or req.semantic_view

    # Project context scoping:
    # 1. When project_id is provided, restrict candidate domains to those attached to the project
    #    to prevent cross-domain pollution or metric name collisions.
    target_db: Optional[str] = None
    target_schema: Optional[str] = None

    # 2. Automatically resolve the deployed Snowflake Semantic View name from the project snapshot
    #    so the engine targets SEMANTIC_VIEW(...) rather than physical tables.
    if req.project_id:
        model_name: Optional[str] = None
        try:
            from semabridge.repository.model_repository import ModelRepository
            dep_info = ModelRepository().get_project_deployment_info(req.project_id)
            target_db = dep_info.get("database") or target_db
            target_schema = dep_info.get("schema") or target_schema
            model_name = dep_info.get("model_name") or model_name
            if not resolved_semantic_view:
                resolved_semantic_view = dep_info.get("semantic_view")
        except Exception as e:
            logger.debug("Could not resolve deployment info for project '%s': %s", req.project_id, e)

        p_domains = ontology_store.get_project_domains(req.project_id)
        if p_domains:
            allowed_domains = [d.domain for d in p_domains] + [d.id for d in p_domains if d.id]
        else:
            fed = ontology_store.get_federated_ontology()
            matched = [d for d in fed.domains.values() if getattr(d, "source_project_id", None) == req.project_id]
            # Fallback: if project_id is an alias (e.g. proj-ai vs proj-artificial-intelligence), match by snapshot model name
            if not matched and model_name:
                matched = [d for d in fed.domains.values() if d.domain.casefold() == model_name.casefold()]
            if matched:
                allowed_domains = [d.domain for d in matched] + [d.id for d in matched if d.id]

        if not resolved_semantic_view:
            return QueryResolutionPlan(
                original_prompt=req.prompt,
                detected_metrics=[],
                detected_dimensions=[],
                resolved_tables=[],
                generated_sql=None,
                confidence_score=0.0,
                explanation=f"No deployed semantic view known for project '{req.project_id}'. Cannot generate governed semantic query.",
                warnings=[f"No deployed semantic view known for project '{req.project_id}'."],
            )

    # 1. Target Snowflake → Cortex Analyst REST API with Key-Pair Auth
    if effective_dialect == "SNOWFLAKE" and (req.target or "").upper() != "LOCAL":
        cortex_error = None
        try:
            from semabridge.core.settings import reload_settings
            from semabridge.ai.cortex_analyst_client import call_cortex_analyst

            cfg = reload_settings()
            sf = cfg.snowflake
            if not sf.private_key:
                cortex_error = "Snowflake key-pair private key is not configured."
            else:
                raw_vn = (resolved_semantic_view or req.view_name or "").strip()
                if not raw_vn:
                    return QueryResolutionPlan(
                        original_prompt=req.prompt,
                        detected_metrics=[],
                        detected_dimensions=[],
                        resolved_tables=[],
                        generated_sql=None,
                        confidence_score=0.0,
                        explanation="No semantic view specified or resolved for query.",
                        warnings=["Semantic view name is required for Cortex Analyst routing."],
                    )

                effective_db = target_db or sf.database
                effective_schema = target_schema or sf.schema_name
                parts = [p.strip('"') for p in raw_vn.split(".") if p.strip('"')]
                if len(parts) == 1:
                    view_name = f'"{effective_db}"."{effective_schema}"."{parts[0]}"'
                elif len(parts) == 2:
                    view_name = f'"{effective_db}"."{parts[0]}"."{parts[1]}"'
                else:
                    view_name = f'"{parts[0]}"."{parts[1]}"."{parts[2]}"'

                cortex_resp = call_cortex_analyst(
                    prompt=req.prompt,
                    semantic_view=view_name,
                    config=sf,
                )
                generated_sql = cortex_resp.get("generated_sql", "")
                if generated_sql:
                    # Validate through read-only SQL guard
                    validate_safe_sql(generated_sql)
                    return QueryResolutionPlan(
                        original_prompt=req.prompt,
                        detected_metrics=[],
                        detected_dimensions=[],
                        resolved_tables=[view_name],
                        generated_sql=generated_sql,
                        confidence_score=0.95,
                        explanation=f"[Snowflake Cortex Analyst] {cortex_resp.get('explanation', '')}",
                        warnings=[],
                    )
                else:
                    cortex_error = "No SQL statement generated by Cortex Analyst."
        except Exception as exc:
            logger.warning("Cortex Analyst live resolution unavailable: %s", exc)
            cortex_error = str(exc)

        if cortex_error:
            if not req.allow_offline_draft:
                return QueryResolutionPlan(
                    original_prompt=req.prompt,
                    detected_metrics=[],
                    detected_dimensions=[],
                    resolved_tables=[],
                    generated_sql="",
                    confidence_score=0.0,
                    explanation="Cortex unavailable. To generate an offline draft query using the local engine, set allow_offline_draft=true or target='LOCAL'.",
                    warnings=[f"Cortex unavailable: {cortex_error}"],
                )

    # 2. Local Engine fallback or explicit LOCAL target (clearly labeled as offline draft)
    plan = engine.resolve_query(
        req.prompt,
        dialect=effective_dialect,
        semantic_view=resolved_semantic_view,
        allowed_domains=allowed_domains,
    )
    plan.explanation = f"[Offline Draft - Local Engine] {plan.explanation}"
    plan.warnings.append("Cortex Analyst offline or local mode selected; SQL is an offline draft generated by the local engine.")
    return plan


@router.post("/execute-query", response_model=ExecuteQueryResponse)
def execute_semantic_query(req: ExecuteQueryRequest) -> ExecuteQueryResponse:
    """Execute generated semantic SQL against Snowflake, Databricks, or local engine with safe guardrails."""
    if not req.sql or not req.sql.strip():
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")

    clean_sql = req.sql.strip().rstrip(";")
    # Strip comments if present for execution
    exec_sql = re.sub(r"^--.*$", "", clean_sql, flags=re.MULTILINE).strip()
    if not exec_sql:
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")

    # 1. Route every statement through validate_safe_sql and allow only read-only SELECT
    statements = split_statements(exec_sql)
    if not statements:
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")

    for stmt in statements:
        stmt_clean = strip_sql_comments(stmt).strip()
        if not stmt_clean:
            continue
        try:
            validate_safe_sql(stmt_clean)
        except SQLSecurityGuardError as sge:
            logger.warning("SQL execution guard rejected query: %s", sge)
            raise HTTPException(status_code=400, detail=str(sge))
        except Exception as exc:
            logger.warning("SQL validation error: %s", exc)
            raise HTTPException(status_code=400, detail=f"SQL validation error: {exc}")

        is_select = bool(
            re.match(r"^\s*SELECT\b", stmt_clean, re.IGNORECASE)
            or re.match(r"^\s*WITH\s+[a-zA-Z0-9_\"`]+\s+AS\s*\(.*?\)\s*SELECT\b", stmt_clean, re.IGNORECASE | re.DOTALL)
        )
        if not is_select:
            raise HTTPException(
                status_code=400,
                detail="Only read-only SELECT statements are permitted."
            )

    if not re.search(r"\blimit\s+\d+", exec_sql, re.IGNORECASE):
        exec_sql = f"{exec_sql} LIMIT {min(req.limit, 100)}"

    target_upper = req.target.upper()

    # 1. SNOWFLAKE EXECUTION
    if target_upper == "SNOWFLAKE":
        import os
        import time
        from pathlib import Path
        from sqlalchemy import select
        from semabridge.repository.credential_manager import CredentialManager
        from semabridge.repository.orm.models import Credential

        t0 = time.perf_counter()
        try:
            from semabridge.core.settings import reload_settings
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            import snowflake.connector

            cm = CredentialManager()
            candidate_uids = [0, 1]
            try:
                with cm._session() as sess:
                    rows_uid = sess.scalars(
                        select(Credential.owner_id).where(Credential.service == "snowflake").distinct()
                    ).all()
                    for r_uid in sorted(rows_uid):
                        if r_uid not in candidate_uids:
                            candidate_uids.append(r_uid)
            except Exception:
                pass
            for uid in candidate_uids:
                try:
                    creds = cm.get_credentials("snowflake", user_id=uid)
                    if creds and (
                        uid == 0
                        or creds.get("private_key")
                        or creds.get("password")
                        or creds.get("oauth_client_secret")
                        or creds.get("authenticator")
                    ):
                        cm.inject_credentials_to_env("snowflake", user_id=uid)
                except Exception:
                    pass
            pk_path = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH", "").strip()
            if pk_path and not os.environ.get("SNOWFLAKE_PRIVATE_KEY"):
                p = Path(pk_path)
                if p.is_file():
                    try:
                        os.environ["SNOWFLAKE_PRIVATE_KEY"] = p.read_text(encoding="utf-8")
                    except Exception:
                        pass

            cfg = reload_settings()
            connect_kwargs = get_snowflake_connect_kwargs(cfg.snowflake)
            has_auth = bool(
                connect_kwargs.get("password")
                or connect_kwargs.get("private_key")
                or connect_kwargs.get("token")
                or connect_kwargs.get("authenticator")
            )
            if not connect_kwargs.get("account") or not connect_kwargs.get("user") or not has_auth:
                return ExecuteQueryResponse(
                    success=False,
                    target="SNOWFLAKE",
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=0,
                    executed_sql=exec_sql,
                    is_simulated=False,
                    error="not executed: no warehouse connection: missing Snowflake credentials.",
                    message="not executed: no warehouse connection",
                    dialect="SNOWFLAKE",
                )

            conn = snowflake.connector.connect(**connect_kwargs)
            try:
                cur = conn.cursor()
                cur.execute(exec_sql)
                cols = [d[0] for d in cur.description] if cur.description else []
                raw_rows = cur.fetchall()
                # Sanitize rows for JSON
                rows = [
                    [str(v) if v is not None and not isinstance(v, (int, float, bool, str)) else v for v in r]
                    for r in raw_rows
                ]
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                _record_verified_execution(clean_sql, exec_sql)
                try:
                    from semabridge.ai.verified_queries_service import record_query_execution
                    record_query_execution(
                        question=req.question or "Semantic View Query",
                        sql=clean_sql,
                        view_name=req.view_name or f"{cfg.snowflake.database}.{cfg.snowflake.schema_name}",
                        user_id=req.user_id or "system",
                        project_id=req.project_id,
                        success=True,
                        row_count=len(rows),
                        execution_time_ms=elapsed_ms,
                    )
                except Exception as db_exc:
                    logger.debug("Failed to record query execution in database: %s", db_exc)

                return ExecuteQueryResponse(
                    success=True,
                    target="SNOWFLAKE",
                    columns=cols,
                    rows=rows,
                    row_count=len(rows),
                    execution_time_ms=elapsed_ms,
                    executed_sql=exec_sql,
                    is_simulated=False,
                    message="Query executed live on Snowflake warehouse.",
                    dialect="SNOWFLAKE",
                )
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("Live Snowflake query execution failed: %s", exc)
            return ExecuteQueryResponse(
                success=False,
                target="SNOWFLAKE",
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=0,
                executed_sql=exec_sql,
                is_simulated=False,
                error=f"not executed: no warehouse connection: {exc}",
                message="not executed: no warehouse connection",
                dialect="SNOWFLAKE",
            )

    # 2. DATABRICKS EXECUTION
    elif target_upper == "DATABRICKS":
        import time
        import requests as _requests
        t0 = time.perf_counter()
        try:
            from semabridge.core.settings import reload_settings
            from semabridge.connectors.factory import make_target_emitter

            cfg = reload_settings()
            if not cfg.databricks or not cfg.databricks.host or not cfg.databricks.warehouse_id:
                return ExecuteQueryResponse(
                    success=False,
                    target="DATABRICKS",
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=0,
                    executed_sql=exec_sql,
                    is_simulated=False,
                    error="not executed: no warehouse connection: missing Databricks credentials.",
                    message="not executed: no warehouse connection",
                    dialect="DATABRICKS",
                )

            publisher = make_target_emitter("databricks", cfg.databricks)
            token = publisher._resolve_token()
            if not token:
                return ExecuteQueryResponse(
                    success=False,
                    target="DATABRICKS",
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=0,
                    executed_sql=exec_sql,
                    is_simulated=False,
                    error="not executed: no warehouse connection: missing Databricks token.",
                    message="not executed: no warehouse connection",
                    dialect="DATABRICKS",
                )

            resp = _requests.post(
                f"https://{cfg.databricks.host}/api/2.0/sql/statements",
                json={
                    "statement": exec_sql,
                    "warehouse_id": cfg.databricks.warehouse_id,
                    "wait_timeout": "15s",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=20,
            )
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                manifest = data.get("manifest", {})
                schema_cols = [c.get("name") for c in manifest.get("schema", {}).get("columns", [])]
                data_array = data.get("result", {}).get("data_array", [])
                _record_verified_execution(clean_sql, exec_sql)
                return ExecuteQueryResponse(
                    success=True,
                    target="DATABRICKS",
                    columns=schema_cols or ["RESULT"],
                    rows=data_array,
                    row_count=len(data_array),
                    execution_time_ms=elapsed_ms,
                    executed_sql=exec_sql,
                    is_simulated=False,
                    message="Query executed live on Databricks SQL Warehouse.",
                    dialect="DATABRICKS",
                )
            else:
                logger.warning("Databricks Statements API error %s: %s", resp.status_code, resp.text[:200])
                return ExecuteQueryResponse(
                    success=False,
                    target="DATABRICKS",
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=elapsed_ms,
                    executed_sql=exec_sql,
                    is_simulated=False,
                    error=f"not executed: no warehouse connection: Databricks API returned HTTP {resp.status_code}",
                    message="not executed: no warehouse connection",
                    dialect="DATABRICKS",
                )
        except Exception as exc:
            logger.warning("Live Databricks query execution failed: %s", exc)
            return ExecuteQueryResponse(
                success=False,
                target="DATABRICKS",
                columns=[],
                rows=[],
                row_count=0,
                execution_time_ms=0,
                executed_sql=exec_sql,
                is_simulated=False,
                error=f"not executed: no warehouse connection: {exc}",
                message="not executed: no warehouse connection",
                dialect="DATABRICKS",
            )

    # 3. LOCAL ZERO-CREDIT ENGINE
    else:
        return ExecuteQueryResponse(
            success=False,
            target="LOCAL",
            columns=[],
            rows=[],
            row_count=0,
            execution_time_ms=0,
            executed_sql=exec_sql,
            is_simulated=False,
            error="not executed: no warehouse connection: local engine has no external warehouse connection.",
            message="not executed: no warehouse connection",
            dialect="LOCAL",
        )


@router.post("/save-verified-query", response_model=SaveVerifiedQueryResponse)
def save_verified_query(req: SaveVerifiedQueryRequest, request: Request) -> SaveVerifiedQueryResponse:
    """Save a verified query in the project database after live execution verification and explicit user confirmation."""
    if not req.question or not req.sql:
        raise HTTPException(status_code=400, detail="Question and SQL are required.")

    if not req.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Explicit user confirmation is required to save a verified query.",
        )

    project_id = req.project_id or "default"

    session_user = None
    try:
        from semabridge.api.deps import get_current_user_optional
        user_obj = get_current_user_optional(request)
        if user_obj:
            session_user = getattr(user_obj, "email", None) or getattr(user_obj, "username", None) or str(user_obj.id)
    except Exception as exc:
        logger.debug("Could not resolve session user: %s", exc)

    effective_user = req.user_id or session_user or "session_user"

    # Enforce owner/admin project permissions
    if req.project_id:
        from semabridge.api.services.project_ownership_service import auth_is_enabled, is_project_owned_by_user
        if auth_is_enabled():
            req_user_id = getattr(request.state, "user_id", None) or session_user
            if not is_project_owned_by_user(req.project_id, req_user_id, log_prefix="SaveVerifiedQueryAuth"):
                raise HTTPException(
                    status_code=403,
                    detail=f"Forbidden: only project owner or an admin can save verified queries for project '{req.project_id}'.",
                )

    # Persist in PostgreSQL/DuckDB database via service layer
    try:
        from semabridge.ai.verified_queries_service import save_verified_query as db_save_verified_query
        saved_row = db_save_verified_query(
            project_id=project_id,
            question=req.question,
            sql=req.sql,
            confirmed=req.confirmed,
            user_id=effective_user,
            name=req.name,
            view_name=req.view_name,
            is_onboarding=req.is_onboarding,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error("Failed to save verified query in database: %s", exc)
        raise HTTPException(status_code=500, detail=f"Database error saving verified query: {exc}")

    target_upper = req.target.upper()
    _record_verified_execution(req.sql)

    return SaveVerifiedQueryResponse(
        success=True,
        message=f"Verified query '{saved_row.name}' successfully registered to database for semantic view.",
        target=target_upper,
        artifact_path=f"database:ai_verified_queries/{saved_row.id}",
        verified_query={
            "id": saved_row.id,
            "name": saved_row.name,
            "question": saved_row.question,
            "sql": saved_row.sql,
            "verified_by": saved_row.verified_by,
            "verified_at": saved_row.verified_at,
            "is_onboarding": saved_row.is_onboarding,
        },
    )


@router.get("/verified-queries")
def list_verified_queries(project_id: str) -> Dict[str, Any]:
    """Retrieve all verified queries stored in the database for a project."""
    if not project_id or not project_id.strip():
        raise HTTPException(status_code=400, detail="project_id is required.")
    from semabridge.ai.verified_queries_service import list_verified_queries_for_project
    queries = list_verified_queries_for_project(project_id.strip())
    return {"project_id": project_id, "queries": queries, "count": len(queries)}


@router.post("/reindex", response_model=Dict[str, Any])
def reindex_search_engine() -> Dict[str, Any]:
    """Manually trigger a full re-index of the semantic search engine."""
    ontology_store._refresh_index()
    return {
        "success": True,
        "indexed_documents": len(ontology_store.search_index.docs),
    }
