"""Cortex & Genie Verified Queries persistence, SQL normalization, and execution verification."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from sqlalchemy import and_, or_, select

from semabridge.repository.orm.ai_models import QueryExecutionHistory, VerifiedQuery
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import db_manager

logger = logging.getLogger("semabridge.ai.verified_queries_service")


def _ensure_ai_tables() -> None:
    """Create verified-query ORM tables if they do not yet exist in the active DB."""
    try:
        engine = db_manager.get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[QueryExecutionHistory.__table__, VerifiedQuery.__table__],
            checkfirst=True,
        )
    except Exception as exc:
        logger.debug("Skipping create_all for AI tables: %s", exc)


def normalize_sql_for_verification(sql: str) -> str:
    """Normalize SQL formatting and keyword casing while preserving string literal case.

    Preserving single-quoted literal contents ensures filters like ``'East'`` vs
    ``'EAST'`` are treated as distinct queries, while uppercasing keywords and
    unquoted identifiers lets minor formatting differences match cleanly.
    """
    raw = (sql or "").strip()
    if not raw:
        return ""

    out: List[str] = []
    i = 0
    n = len(raw)

    while i < n:
        ch = raw[i]

        # Single-quoted literal: preserve exact casing and internal whitespace
        if ch == "'":
            j = i + 1
            buf = ["'"]
            while j < n:
                buf.append(raw[j])
                if raw[j] == "'":
                    if j + 1 < n and raw[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append("".join(buf))
            i = j
            continue

        # Double-quoted identifier: preserve exact casing inside quotes
        if ch == '"':
            j = i + 1
            buf = ['"']
            while j < n:
                buf.append(raw[j])
                if raw[j] == '"':
                    if j + 1 < n and raw[j + 1] == '"':
                        buf.append('"')
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            out.append("".join(buf))
            i = j
            continue

        # Line comment -- ...
        if ch == "-" and i + 1 < n and raw[i + 1] == "-":
            j = raw.find("\n", i + 2)
            i = n if j == -1 else j + 1
            out.append(" ")
            continue

        # Block comment /* ... */
        if ch == "/" and i + 1 < n and raw[i + 1] == "*":
            j = raw.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue

        # Collapse unquoted whitespace runs
        if ch.isspace():
            if not out or not out[-1].endswith(" "):
                out.append(" ")
            i += 1
            continue

        out.append(ch.upper())
        i += 1

    normalized = "".join(out).strip()
    # Collapse spaces cleanly without touching quoted strings (out segments alternate cleanly)
    normalized = re.sub(r";+$", "", normalized).strip()
    # Strip trailing LIMIT N added transparently by execute-query
    normalized = re.sub(r"\s+LIMIT\s+\d+\s*$", "", normalized, flags=re.IGNORECASE).strip()
    return normalized


def compute_sql_hash(sql: str) -> str:
    norm = normalize_sql_for_verification(sql)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def compute_question_hash(question: str) -> str:
    return hashlib.sha256(question.strip().encode("utf-8")).hexdigest()


def make_verified_query_name(question: str, explicit_name: Optional[str] = None) -> str:
    """Build a collision-free identifier for verified_queries without 40-char truncation collisions."""
    if explicit_name and explicit_name.strip():
        return re.sub(r"[^a-zA-Z0-9_]+", "_", explicit_name.strip()).strip("_")[:100] or "verified_query"
    q_clean = question.strip()
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", q_clean.lower()).strip("_")[:48] or "query"
    short_hash = hashlib.sha256(q_clean.encode("utf-8")).hexdigest()[:8]
    return f"{slug}_{short_hash}"


def record_query_execution(
    sql: str,
    target: str,
    row_count: int,
    success: bool,
    is_simulated: bool,
    project_id: Optional[str] = None,
) -> Optional[str]:
    """Persist an execution receipt; only non-simulated successes satisfy verified-query checks."""
    norm_sql = normalize_sql_for_verification(sql)
    if not norm_sql:
        return None
    _ensure_ai_tables()
    rec_id = str(uuid.uuid4())
    pid = project_id.strip() if project_id and project_id.strip() else None
    try:
        with db_manager.get_session() as session:
            row = QueryExecutionHistory(
                id=rec_id,
                project_id=pid,
                target=(target or "SNOWFLAKE").upper(),
                normalized_sql_hash=hashlib.sha256(norm_sql.encode("utf-8")).hexdigest(),
                executed_sql=sql.strip(),
                row_count=int(row_count),
                success=bool(success),
                is_simulated=bool(is_simulated),
                executed_at=datetime.now(timezone.utc),
            )
            session.add(row)
            session.commit()
        return rec_id
    except Exception as exc:
        logger.warning("Failed to record query execution receipt: %s", exc)
        return None


def has_successful_execution(sql: str, project_id: Optional[str] = None) -> bool:
    """Return True only when this normalized SQL succeeded live (non-simulated) for the project."""
    sql_h = compute_sql_hash(sql)
    if not sql.strip():
        return False
    _ensure_ai_tables()
    pid = project_id.strip() if project_id and project_id.strip() else None
    try:
        with db_manager.get_session() as session:
            conds = [
                QueryExecutionHistory.normalized_sql_hash == sql_h,
                QueryExecutionHistory.success.is_(True),
                QueryExecutionHistory.is_simulated.is_(False),
            ]
            if pid:
                conds.append(
                    or_(
                        QueryExecutionHistory.project_id == pid,
                        QueryExecutionHistory.project_id.is_(None),
                    )
                )
            else:
                conds.append(QueryExecutionHistory.project_id.is_(None))

            hit = session.scalar(
                select(QueryExecutionHistory.id).where(and_(*conds)).limit(1)
            )
            return hit is not None
    except Exception as exc:
        logger.warning("Execution verification check failed: %s", exc)
        return False


def _discover_default_project_slug() -> Optional[str]:
    reverse_dir = Path("output/reverse")
    if reverse_dir.is_dir():
        dirs = [d for d in reverse_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
        if dirs:
            dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
            return dirs[0].name
    return None


def save_verified_query_record(
    *,
    question: str,
    sql: str,
    target: str = "SNOWFLAKE",
    project_id: Optional[str] = None,
    name: Optional[str] = None,
    domain: Optional[str] = None,
    verified_by: str = "semabridge_analyst",
) -> Dict[str, Any]:
    """Save a project-isolated verified query to the DB and update Cortex YAML / Genie artifacts."""
    clean_q = question.strip()
    clean_sql = sql.strip()
    project_slug = (project_id or "").strip() or _discover_default_project_slug() or "default"
    q_hash = compute_question_hash(clean_q)
    sql_h = compute_sql_hash(clean_sql)
    safe_name = make_verified_query_name(clean_q, explicit_name=name)
    now_dt = datetime.now(timezone.utc)
    timestamp = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    _ensure_ai_tables()
    with db_manager.get_session() as session:
        existing = session.scalar(
            select(VerifiedQuery).where(
                and_(
                    VerifiedQuery.project_id == project_slug,
                    VerifiedQuery.question_hash == q_hash,
                )
            )
        )
        if existing is not None:
            existing.name = safe_name if name else existing.name
            existing.sql = clean_sql
            existing.normalized_sql_hash = sql_h
            existing.target = target.upper()
            existing.domain = domain
            existing.verified_by = verified_by
            existing.verified_at = now_dt
            safe_name = existing.name
        else:
            rec = VerifiedQuery(
                id=str(uuid.uuid4()),
                project_id=project_slug,
                name=safe_name,
                question=clean_q,
                question_hash=q_hash,
                sql=clean_sql,
                normalized_sql_hash=sql_h,
                target=target.upper(),
                domain=domain,
                verified_by=verified_by,
                verified_at=now_dt,
            )
            session.add(rec)
        session.commit()

    verified_item: Dict[str, Any] = {
        "name": safe_name,
        "question": clean_q,
        "sql": clean_sql,
        "project_id": project_slug,
        "target": target.upper(),
        "domain": domain,
        "verified_at": timestamp,
        "verified_by": verified_by,
    }

    saved_paths: List[str] = []
    target_upper = target.upper()

    if target_upper in ("SNOWFLAKE", "CORTEX"):
        yaml_path = Path("output/reverse") / project_slug / "cortex_analyst.yaml"
        if yaml_path.is_file():
            try:
                parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                if "semantic_model" in parsed and isinstance(parsed["semantic_model"], dict):
                    v_list = parsed["semantic_model"].setdefault("verified_queries", [])
                else:
                    v_list = parsed.setdefault("verified_queries", [])
                yaml_entry = {
                    "name": safe_name,
                    "question": clean_q,
                    "sql": clean_sql,
                    "verified_at": timestamp,
                    "verified_by": verified_by,
                }
                existing_idx = next(
                    (i for i, q in enumerate(v_list) if str(q.get("question", "")).strip() == clean_q),
                    None,
                )
                if existing_idx is not None:
                    v_list[existing_idx] = yaml_entry
                else:
                    v_list.append(yaml_entry)
                yaml_path.write_text(yaml.safe_dump(parsed, sort_keys=False), encoding="utf-8")
                saved_paths.append(str(yaml_path))
            except Exception as exc:
                logger.warning("Could not update cortex_analyst.yaml at %s: %s", yaml_path, exc)

    if target_upper in ("DATABRICKS", "GENIE"):
        benchmarks_dir = Path("Config/benchmarks")
        benchmarks_dir.mkdir(parents=True, exist_ok=True)
        bench_file = benchmarks_dir / f"databricks_genie_{project_slug}.json"
        try:
            current_bench: List[Dict[str, Any]] = []
            if bench_file.is_file():
                current_bench = json.loads(bench_file.read_text(encoding="utf-8"))
            bench_entry = {
                "name": safe_name,
                "question": clean_q,
                "sql": clean_sql,
                "verified_at": timestamp,
                "verified_by": verified_by,
            }
            existing_idx = next(
                (i for i, q in enumerate(current_bench) if str(q.get("question", "")).strip() == clean_q),
                None,
            )
            if existing_idx is not None:
                current_bench[existing_idx] = bench_entry
            else:
                current_bench.append(bench_entry)
            bench_file.write_text(json.dumps(current_bench, indent=2), encoding="utf-8")
            saved_paths.append(str(bench_file))
        except Exception as exc:
            logger.warning("Could not save Databricks benchmark: %s", exc)

    return {
        "verified_item": verified_item,
        "artifact_path": saved_paths[0] if saved_paths else None,
        "project_slug": project_slug,
    }


def list_verified_queries_for_project(project_id: str) -> List[Dict[str, Any]]:
    """Return verified queries strictly scoped to the given project_id."""
    pid = (project_id or "").strip()
    if not pid:
        return []

    _ensure_ai_tables()
    results: List[Dict[str, Any]] = []
    seen_questions: set[str] = set()

    try:
        with db_manager.get_session() as session:
            rows = session.scalars(
                select(VerifiedQuery)
                .where(VerifiedQuery.project_id == pid)
                .order_by(VerifiedQuery.verified_at.desc())
            ).all()
            for r in rows:
                seen_questions.add(r.question.strip())
                results.append(
                    {
                        "id": r.id,
                        "project_id": r.project_id,
                        "name": r.name,
                        "question": r.question,
                        "sql": r.sql,
                        "target": r.target,
                        "domain": r.domain,
                        "verified_by": r.verified_by,
                        "verified_at": r.verified_at.strftime("%Y-%m-%dT%H:%M:%SZ") if r.verified_at else None,
                    }
                )
    except Exception as exc:
        logger.warning("Failed reading verified queries from DB for project '%s': %s", pid, exc)

    # Also include entries from project's cortex_analyst.yaml that were emitted cleanly on deploy
    yaml_path = Path("output/reverse") / pid / "cortex_analyst.yaml"
    if yaml_path.is_file():
        try:
            parsed = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            v_list = (
                parsed.get("semantic_model", {}).get("verified_queries", [])
                if isinstance(parsed.get("semantic_model"), dict)
                else parsed.get("verified_queries", [])
            )
            for item in v_list or []:
                if not isinstance(item, dict):
                    continue
                q_text = str(item.get("question", "")).strip()
                if q_text and q_text not in seen_questions:
                    seen_questions.add(q_text)
                    results.append(
                        {
                            "project_id": pid,
                            "name": item.get("name") or make_verified_query_name(q_text),
                            "question": q_text,
                            "sql": str(item.get("sql", "")),
                            "target": "SNOWFLAKE",
                            "verified_by": item.get("verified_by", "cortex_yaml"),
                            "verified_at": item.get("verified_at"),
                        }
                    )
        except Exception as exc:
            logger.debug("Could not read cortex_analyst.yaml verified_queries for %s: %s", pid, exc)

    return results

