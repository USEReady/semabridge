"""Execution receipts and project-scoped verified queries.

Verified queries live only in the database, per project. They are deployed
into the semantic view's ``AI_VERIFIED_QUERIES`` clause by the DDL builder,
so they survive re-syncs and reach Snowflake (no local YAML side files).

A verified query can only be saved when:
  * the user explicitly confirmed the answer (``confirmed=True``), and
  * the same normalized SQL has a successful, non-simulated execution
    receipt for the same project (0-row results are allowed).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import inspect, or_, select, text

from semabridge.repository.orm.ai_models import (
    AIQueryExecutionRow,
    AIVerifiedQueryRow,
    MetricCertification,
)
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import db_manager

logger = logging.getLogger("semabridge.ai.verified_queries_service")

_AI_TABLES = (AIQueryExecutionRow.__table__, AIVerifiedQueryRow.__table__, MetricCertification.__table__)


def _add_missing_columns(engine) -> None:
    """Add nullable columns introduced after a table was first created."""
    insp = inspect(engine)
    for table in _AI_TABLES:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing or not col.nullable:
                continue
            col_type = col.type.compile(dialect=engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}'))
            logger.info("Added column %s.%s", table.name, col.name)


def _ensure_tables_exist() -> None:
    """Create the AI tables (and late-added nullable columns) when missing."""
    try:
        engine = db_manager.get_engine()
        Base.metadata.create_all(bind=engine, tables=list(_AI_TABLES), checkfirst=True)
        _add_missing_columns(engine)
    except Exception as exc:
        logger.debug("AI table creation check skipped/failed: %s", exc)


# Backward-compatible name used by older callers.
_ensure_ai_tables = _ensure_tables_exist


# ---------------------------------------------------------------------------
# SQL normalization
# ---------------------------------------------------------------------------

def _scan_quoted(raw: str, start: int, quote: str) -> int:
    """Return the index just past the quoted token starting at ``start``."""
    j = start + 1
    n = len(raw)
    while j < n:
        if raw[j] == quote:
            if j + 1 < n and raw[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def normalize_sql_for_verification(sql: str) -> str:
    """Normalize SQL for receipt matching.

    Comments are removed, whitespace collapsed, keywords/unquoted identifiers
    upper-cased, and a trailing ``LIMIT n`` / ``;`` dropped. Quoted literals and
    quoted identifiers keep their exact case, so ``'East'`` != ``'EAST'``.
    """
    raw = (sql or "").strip()
    out: List[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch in ("'", '"'):
            j = _scan_quoted(raw, i, ch)
            out.append(raw[i:j])
            i = j
            continue
        if raw.startswith("--", i):
            j = raw.find("\n", i + 2)
            i = n if j == -1 else j + 1
            out.append(" ")
            continue
        if raw.startswith("/*", i):
            j = raw.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue
        if ch.isspace():
            if not out or not out[-1].endswith(" "):
                out.append(" ")
            i += 1
            continue
        out.append(ch.upper())
        i += 1
    normalized = re.sub(r"[\s;]+$", "", "".join(out).strip())
    normalized = re.sub(r"\s+LIMIT\s+\d+\s*$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized


def compute_sql_hash(sql: str) -> str:
    return hashlib.sha256(normalize_sql_for_verification(sql).encode("utf-8")).hexdigest()


def make_verified_query_name(question: str, explicit_name: Optional[str] = None) -> str:
    """Build a stable identifier; a short hash avoids truncation collisions."""
    if explicit_name and explicit_name.strip():
        cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", explicit_name.strip()).strip("_")[:100]
        if cleaned:
            return cleaned
    q_clean = (question or "").strip()
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", q_clean.lower()).strip("_")[:48] or "query"
    short_hash = hashlib.sha256(q_clean.encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{short_hash}"


def _clean_project_id(project_id: Optional[str]) -> Optional[str]:
    pid = (project_id or "").strip()
    return pid or None


# ---------------------------------------------------------------------------
# Execution receipts
# ---------------------------------------------------------------------------

def record_query_execution(
    sql: str,
    target: str = "SNOWFLAKE",
    row_count: int = 0,
    success: bool = False,
    is_simulated: bool = False,
    project_id: Optional[str] = None,
    *,
    question: str = "",
    view_name: Optional[str] = None,
    user_id: Optional[str] = None,
    execution_time_ms: int = 0,
    error_message: Optional[str] = None,
) -> Optional[AIQueryExecutionRow]:
    """Persist an execution receipt for any warehouse target."""
    norm_sql = normalize_sql_for_verification(sql)
    if not norm_sql:
        return None
    _ensure_tables_exist()
    row = AIQueryExecutionRow(
        id=str(uuid.uuid4()),
        project_id=_clean_project_id(project_id),
        question=(question or "").strip(),
        sql=(sql or "").strip(),
        normalized_sql=norm_sql,
        view_name=view_name or "",
        target=(target or "SNOWFLAKE").upper(),
        user_id=str(user_id or ""),
        success=bool(success),
        is_simulated=bool(is_simulated),
        row_count=int(row_count or 0),
        execution_time_ms=int(execution_time_ms or 0),
        error_message=error_message,
    )
    with db_manager.get_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
    logger.info("Recorded %s execution %s (success=%s, rows=%d)", row.target, row.id, row.success, row.row_count)
    return row


def has_successful_execution(sql: str, project_id: Optional[str] = None) -> bool:
    """True only when this SQL succeeded live (non-simulated) for the project."""
    norm_sql = normalize_sql_for_verification(sql)
    if not norm_sql:
        return False
    _ensure_tables_exist()
    pid = _clean_project_id(project_id)
    stmt = select(AIQueryExecutionRow.id).where(
        AIQueryExecutionRow.normalized_sql == norm_sql,
        AIQueryExecutionRow.success.is_(True),
        or_(AIQueryExecutionRow.is_simulated.is_(False), AIQueryExecutionRow.is_simulated.is_(None)),
    )
    if pid:
        stmt = stmt.where(AIQueryExecutionRow.project_id == pid)
    else:
        stmt = stmt.where(AIQueryExecutionRow.project_id.is_(None))
    with db_manager.get_session() as session:
        return session.execute(stmt.limit(1)).first() is not None


# ---------------------------------------------------------------------------
# Verified queries
# ---------------------------------------------------------------------------

def _row_to_dict(r: AIVerifiedQueryRow) -> Dict[str, Any]:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "name": r.name,
        "question": r.question,
        "sql": r.sql,
        "view_name": r.view_name,
        "target": r.target or "SNOWFLAKE",
        "domain": r.domain,
        "verified_by": r.verified_by,
        "verified_at": r.verified_at,
        "is_onboarding": bool(r.is_onboarding),
    }


def save_verified_query(
    project_id: str,
    question: str,
    sql: str,
    confirmed: bool,
    user_id: Optional[str] = None,
    name: Optional[str] = None,
    view_name: Optional[str] = None,
    is_onboarding: bool = False,
    target: str = "SNOWFLAKE",
    domain: Optional[str] = None,
) -> AIVerifiedQueryRow:
    """Save (or update) a verified query after confirmation + execution checks."""
    pid = _clean_project_id(project_id)
    if not pid:
        raise ValueError("project_id is required to save a verified query.")
    if not (question or "").strip() or not (sql or "").strip():
        raise ValueError("Question and SQL are required.")
    if not confirmed:
        raise ValueError("Explicit user confirmation is required to save a verified query.")
    if not has_successful_execution(sql, pid):
        raise ValueError(
            "Query cannot be saved as verified: query must first be executed successfully "
            "against a real deployed warehouse view (no successful execution record found in database)."
        )

    _ensure_tables_exist()
    clean_q = question.strip()
    safe_name = make_verified_query_name(clean_q, explicit_name=name)
    now = int(time.time())
    with db_manager.get_session() as session:
        existing = session.execute(
            select(AIVerifiedQueryRow).where(
                AIVerifiedQueryRow.project_id == pid,
                or_(AIVerifiedQueryRow.name == safe_name, AIVerifiedQueryRow.question == clean_q),
            )
        ).scalars().first()
        row = existing or AIVerifiedQueryRow(id=str(uuid.uuid4()), project_id=pid, name=safe_name)
        row.question = clean_q
        row.sql = sql.strip()
        row.view_name = view_name or row.view_name
        row.target = (target or "SNOWFLAKE").upper()
        row.domain = domain or row.domain
        row.verified_by = str(user_id or row.verified_by or "")
        row.verified_at = now
        row.is_onboarding = bool(is_onboarding)
        if existing is None:
            session.add(row)
        session.commit()
        session.refresh(row)
        session.expunge(row)
    logger.info("Saved verified query %s for project %s", row.name, pid)
    return row


def save_verified_query_record(
    *,
    question: str,
    sql: str,
    target: str = "SNOWFLAKE",
    project_id: Optional[str] = None,
    name: Optional[str] = None,
    domain: Optional[str] = None,
    verified_by: Optional[str] = None,
    view_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Register a verified query produced by an automated certification.

    Used by reconciliation after a CERTIFIED parity check, which itself is the
    explicit confirmation and executes the SQL live first.
    """
    row = save_verified_query(
        project_id=project_id or "",
        question=question,
        sql=sql,
        confirmed=True,
        user_id=verified_by,
        name=name,
        view_name=view_name,
        target=target,
        domain=domain,
    )
    return {"verified_item": _row_to_dict(row), "artifact_path": f"database:ai_verified_queries/{row.id}"}


def list_verified_queries_for_project(project_id: str) -> List[Dict[str, Any]]:
    """Return verified queries strictly scoped to ``project_id``."""
    pid = _clean_project_id(project_id)
    if not pid:
        return []
    _ensure_tables_exist()
    with db_manager.get_session() as session:
        rows = session.execute(
            select(AIVerifiedQueryRow)
            .where(AIVerifiedQueryRow.project_id == pid)
            .order_by(AIVerifiedQueryRow.created_at.asc())
        ).scalars().all()
        return [_row_to_dict(r) for r in rows]


def delete_verified_query(project_id: str, query_id_or_name: str) -> bool:
    """Delete a verified query by ID or name."""
    pid = _clean_project_id(project_id)
    if not pid:
        return False
    _ensure_tables_exist()
    with db_manager.get_session() as session:
        row = session.execute(
            select(AIVerifiedQueryRow).where(
                AIVerifiedQueryRow.project_id == pid,
                or_(AIVerifiedQueryRow.id == query_id_or_name, AIVerifiedQueryRow.name == query_id_or_name),
            )
        ).scalars().first()
        if not row:
            return False
        session.delete(row)
        session.commit()
        return True
