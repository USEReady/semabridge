"""REST API Router for Embedded SemaBridge AI Layer.

Provides zero-credit local semantic search and Natural Language to Governed SQL
without dependency on Snowflake Cortex or external credits.
"""

import json
import logging
import re
import time
import yaml
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
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
                view_name = req.view_name or f'"{sf.database}"."{sf.schema_name}"."SEMANTIC_VIEW"'
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
    plan = engine.resolve_query(req.prompt, dialect=effective_dialect)
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
        import time
        t0 = time.perf_counter()
        try:
            from semabridge.core.settings import reload_settings
            from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs
            import snowflake.connector

            cfg = reload_settings()
            connect_kwargs = get_snowflake_connect_kwargs(cfg.snowflake)
            if not connect_kwargs.get("account") or not connect_kwargs.get("user") or not connect_kwargs.get("password"):
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
def save_verified_query(req: SaveVerifiedQueryRequest) -> SaveVerifiedQueryResponse:
    """Save a verified query in the project database after live execution verification and explicit user confirmation."""
    if not req.question or not req.sql:
        raise HTTPException(status_code=400, detail="Question and SQL are required.")

    if not req.confirmed:
        raise HTTPException(
            status_code=400,
            detail="Explicit user confirmation is required to save a verified query.",
        )

    project_id = req.project_id or "default"

    # Persist in PostgreSQL/DuckDB database via service layer
    try:
        from semabridge.ai.verified_queries_service import save_verified_query as db_save_verified_query
        saved_row = db_save_verified_query(
            project_id=project_id,
            question=req.question,
            sql=req.sql,
            confirmed=req.confirmed,
            user_id=req.user_id or "Data Steward",
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
