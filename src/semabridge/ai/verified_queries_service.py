"""Service layer for AI query execution auditing and project-scoped verified query governance."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from semabridge.repository.orm.ai_models import AIQueryExecutionRow, AIVerifiedQueryRow
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import db_manager

logger = logging.getLogger("semabridge.ai.verified_queries_service")


def _ensure_tables_exist() -> None:
    """Ensure the ai_query_executions and ai_verified_queries tables are created."""
    try:
        engine = db_manager.get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[AIQueryExecutionRow.__table__, AIVerifiedQueryRow.__table__],
            checkfirst=True,
        )
    except Exception as exc:
        logger.debug("Table checkfirst creation skipped/failed: %s", exc)


def normalize_sql_for_verification(sql: str) -> str:
    """Normalize SQL query for execution verification comparison."""
    s = re.sub(r"--[^\r\n]*", "", sql or "")
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.DOTALL)
    # Strip trailing limit clause
    s = re.sub(r"\bLIMIT\s+\d+\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip().rstrip(";").strip().upper()
    return s


def record_query_execution(
    question: str,
    sql: str,
    view_name: str,
    user_id: str = "system",
    project_id: Optional[str] = None,
    success: bool = False,
    row_count: int = 0,
    execution_time_ms: int = 0,
    error_message: Optional[str] = None,
) -> AIQueryExecutionRow:
    """Persist a live execution record in the database."""
    _ensure_tables_exist()
    norm_sql = normalize_sql_for_verification(sql)
    row = AIQueryExecutionRow(
        id=str(uuid.uuid4()),
        project_id=project_id,
        question=question or "Semantic View Query",
        sql=sql,
        normalized_sql=norm_sql,
        view_name=view_name or "governed_view",
        user_id=user_id or "system",
        success=success,
        row_count=row_count,
        execution_time_ms=execution_time_ms,
        error_message=error_message,
    )
    with db_manager.get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info(
            "Recorded AI query execution %s (success=%s, rows=%d) in database",
            row.id,
            row.success,
            row.row_count,
        )
        return row


def has_successful_execution(sql: str, project_id: Optional[str] = None) -> bool:
    """Check database if SQL was executed successfully against a real warehouse."""
    _ensure_tables_exist()
    norm_sql = normalize_sql_for_verification(sql)
    if not norm_sql:
        return False

    with db_manager.get_session() as session:
        stmt = select(AIQueryExecutionRow).where(
            AIQueryExecutionRow.normalized_sql == norm_sql,
            AIQueryExecutionRow.success == True,
        )
        if project_id:
            stmt = stmt.where(
                (AIQueryExecutionRow.project_id == project_id)
                | (AIQueryExecutionRow.project_id == None)
            )
        result = session.execute(stmt).scalars().first()
        return result is not None


def save_verified_query(
    project_id: str,
    question: str,
    sql: str,
    confirmed: bool,
    user_id: Optional[str] = None,
    name: Optional[str] = None,
    view_name: Optional[str] = None,
    is_onboarding: bool = False,
) -> AIVerifiedQueryRow:
    """Save a verified query in the database after execution and confirmation check."""
    if not confirmed:
        raise ValueError("Explicit user confirmation is required to save a verified query.")

    if not has_successful_execution(sql, project_id):
        raise ValueError(
            "Query cannot be saved as verified: query must first be executed successfully "
            "against a real deployed warehouse view (no successful execution record found in database)."
        )

    _ensure_tables_exist()
    safe_name = name or re.sub(r"[^a-zA-Z0-9_]", "_", question.lower().strip())[:40].strip("_")
    if not safe_name:
        safe_name = f"query_{int(time.time())}"

    # Check if a verified query with this name or sql already exists for project
    with db_manager.get_session() as session:
        existing = session.execute(
            select(AIVerifiedQueryRow).where(
                AIVerifiedQueryRow.project_id == project_id,
                (AIVerifiedQueryRow.name == safe_name) | (AIVerifiedQueryRow.question == question),
            )
        ).scalars().first()

        if existing:
            existing.sql = sql
            existing.question = question
            existing.view_name = view_name or existing.view_name
            existing.verified_by = user_id or existing.verified_by
            existing.verified_at = int(time.time())
            existing.is_onboarding = is_onboarding
            session.commit()
            session.refresh(existing)
            logger.info("Updated existing verified query %s for project %s", safe_name, project_id)
            return existing

        row = AIVerifiedQueryRow(
            id=str(uuid.uuid4()),
            project_id=project_id,
            name=safe_name,
            question=question,
            sql=sql,
            view_name=view_name,
            verified_by=user_id or "session_user",
            verified_at=int(time.time()),
            is_onboarding=is_onboarding,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info("Saved new verified query %s for project %s in database", safe_name, project_id)
        return row


def list_verified_queries_for_project(project_id: str) -> List[Dict[str, Any]]:
    """Retrieve all verified queries for a project from the database."""
    _ensure_tables_exist()
    with db_manager.get_session() as session:
        rows = session.execute(
            select(AIVerifiedQueryRow)
            .where(AIVerifiedQueryRow.project_id == project_id)
            .order_by(AIVerifiedQueryRow.created_at.asc())
        ).scalars().all()

        return [
            {
                "id": r.id,
                "project_id": r.project_id,
                "name": r.name,
                "question": r.question,
                "sql": r.sql,
                "view_name": r.view_name,
                "verified_by": r.verified_by,
                "verified_at": r.verified_at,
                "is_onboarding": r.is_onboarding,
            }
            for r in rows
        ]


def delete_verified_query(project_id: str, query_id_or_name: str) -> bool:
    """Delete a verified query by ID or name from database."""
    _ensure_tables_exist()
    with db_manager.get_session() as session:
        row = session.execute(
            select(AIVerifiedQueryRow).where(
                AIVerifiedQueryRow.project_id == project_id,
                (AIVerifiedQueryRow.id == query_id_or_name) | (AIVerifiedQueryRow.name == query_id_or_name),
            )
        ).scalars().first()
        if row:
            session.delete(row)
            session.commit()
            return True
        return False

