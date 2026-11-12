"""Persistence and lookup service for per-metric reconciliation certifications."""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import desc

from semabridge.repository.orm.ai_models import MetricCertification
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import db_manager

logger = logging.getLogger("semabridge.reconciliation.certification_service")

_SV_METRICS_RE = re.compile(
    r"\bMETRICS\s+(.*?)(?=\bDIMENSIONS\b|\bWHERE\b|\))",
    re.IGNORECASE | re.DOTALL,
)


def _ensure_cert_table() -> None:
    """Create the metric certification ORM table if it does not yet exist."""
    try:
        engine = db_manager.get_engine()
        Base.metadata.create_all(
            bind=engine,
            tables=[MetricCertification.__table__],
            checkfirst=True,
        )
    except Exception as exc:
        logger.debug("Skipping create_all for ai_metric_certifications: %s", exc)


def _norm_metric_key(name: Optional[str]) -> str:
    return re.sub(r"[\s_\-]+", "", str(name or "").strip().casefold())


def extract_semantic_view_metrics(sql: str) -> List[str]:
    """Extract logical metric tokens referenced in ``SEMANTIC_VIEW(... METRICS ...)`` SQL."""
    m = _SV_METRICS_RE.search(sql or "")
    if not m:
        return []
    raw_items = m.group(1).split(",")
    metrics: List[str] = []
    for item in raw_items:
        tok = item.strip().strip('"').strip("`")
        if tok:
            metrics.append(tok)
    return metrics


def save_certification_record(
    *,
    metric_name: str,
    status: str,
    certification_badge: str = "",
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
    view_metric: Optional[str] = None,
    dimensions: Optional[List[str]] = None,
    dax_query: str = "",
    sql_query: str = "",
    dax_row_count: int = 0,
    sql_row_count: int = 0,
    matched_rows: int = 0,
    mismatched_rows: int = 0,
    max_absolute_delta: float = 0.0,
    max_percentage_delta: float = 0.0,
    absolute_tolerance: float = 0.01,
    relative_tolerance: float = 0.0001,
    max_mismatch_percentage: float = 0.0,
    certified_by: str = "semabridge_verifier",
) -> Optional[Dict[str, Any]]:
    """Persist a reconciliation certification outcome for a metric."""
    m_clean = (metric_name or "").strip()
    if not m_clean:
        return None
    _ensure_cert_table()
    rec_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    pid = project_id.strip() if project_id and project_id.strip() else None
    dom = domain.strip() if domain and domain.strip() else None
    vm = view_metric.strip() if view_metric and view_metric.strip() else None

    try:
        with db_manager.get_session() as session:
            row = MetricCertification(
                id=rec_id,
                project_id=pid,
                domain=dom,
                metric_name=m_clean,
                view_metric=vm,
                dimensions_json=json.dumps(list(dimensions or [])),
                status=str(status).upper(),
                certification_badge=certification_badge or str(status).upper(),
                dax_query=dax_query or "",
                sql_query=sql_query or "",
                dax_row_count=int(dax_row_count),
                sql_row_count=int(sql_row_count),
                matched_rows=int(matched_rows),
                mismatched_rows=int(mismatched_rows),
                max_absolute_delta=float(max_absolute_delta),
                max_percentage_delta=float(max_percentage_delta),
                absolute_tolerance=float(absolute_tolerance),
                relative_tolerance=float(relative_tolerance),
                max_mismatch_percentage=float(max_mismatch_percentage),
                certified_by=(certified_by or "semabridge_verifier").strip() or "semabridge_verifier",
                certified_at=now,
            )
            session.add(row)
            session.commit()
            return _row_to_dict(row)
    except Exception as exc:
        logger.warning("Failed to save certification record for '%s': %s", m_clean, exc)
        return None


def _row_to_dict(row: MetricCertification) -> Dict[str, Any]:
    try:
        dims = json.loads(row.dimensions_json or "[]")
    except Exception:
        dims = []
    return {
        "id": row.id,
        "project_id": row.project_id,
        "domain": row.domain,
        "metric_name": row.metric_name,
        "view_metric": row.view_metric,
        "dimensions": dims,
        "status": row.status,
        "certification_badge": row.certification_badge,
        "dax_query": row.dax_query,
        "sql_query": row.sql_query,
        "dax_row_count": row.dax_row_count,
        "sql_row_count": row.sql_row_count,
        "matched_rows": row.matched_rows,
        "mismatched_rows": row.mismatched_rows,
        "max_absolute_delta": row.max_absolute_delta,
        "max_percentage_delta": row.max_percentage_delta,
        "tolerance": {
            "absolute_tolerance": row.absolute_tolerance,
            "relative_tolerance": row.relative_tolerance,
            "max_mismatch_percentage": row.max_mismatch_percentage,
        },
        "certified_by": row.certified_by,
        "certified_at": row.certified_at.isoformat() if row.certified_at else None,
    }


def get_latest_certifications(
    project_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return the latest certification record per metric, sorted alphabetically."""
    _ensure_cert_table()
    pid = project_id.strip() if project_id and project_id.strip() else None
    dom = domain.strip() if domain and domain.strip() else None
    try:
        with db_manager.get_session() as session:
            q = session.query(MetricCertification)
            if pid:
                q = q.filter(MetricCertification.project_id == pid)
            if dom:
                q = q.filter(MetricCertification.domain == dom)
            rows = q.order_by(desc(MetricCertification.certified_at)).all()
            latest: Dict[tuple[str, str], Dict[str, Any]] = {}
            for row in rows:
                key = ((row.project_id or "").casefold(), _norm_metric_key(row.metric_name))
                if key not in latest:
                    latest[key] = _row_to_dict(row)
            return sorted(latest.values(), key=lambda r: r["metric_name"].casefold())
    except Exception as exc:
        logger.warning("Failed to list metric certifications: %s", exc)
        return []


def get_certified_metric_names(project_id: Optional[str] = None) -> Set[str]:
    """Return names/view_metrics whose latest status is ``CERTIFIED``."""
    names: Set[str] = set()
    for rec in get_latest_certifications(project_id=project_id):
        if rec.get("status") == "CERTIFIED":
            if rec.get("metric_name"):
                names.add(str(rec["metric_name"]))
            if rec.get("view_metric"):
                names.add(str(rec["view_metric"]))
                names.add(str(rec["view_metric"]).split(".")[-1])
    return names


def get_failed_certifications(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return latest certification records that are ``FAILED`` or ``ERROR``."""
    return [
        rec
        for rec in get_latest_certifications(project_id=project_id)
        if rec.get("status") in ("FAILED", "ERROR")
    ]


def check_sql_certification_status(sql: str, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate certification state for metrics referenced by a candidate Verified Query SQL."""
    latest = get_latest_certifications(project_id=project_id)
    if not latest and project_id:
        # Also include global (unscoped) certifications if the project has none yet
        latest = get_latest_certifications(project_id=None)

    has_any_certified = any(r.get("status") == "CERTIFIED" for r in latest)
    by_norm: Dict[str, Dict[str, Any]] = {}
    for rec in latest:
        for candidate in (
            rec.get("metric_name"),
            rec.get("view_metric"),
            str(rec.get("view_metric") or "").split(".")[-1],
        ):
            nk = _norm_metric_key(candidate)
            if nk and nk not in by_norm:
                by_norm[nk] = rec

    sql_metrics = extract_semantic_view_metrics(sql)
    failed: List[str] = []
    uncertified: List[str] = []
    certified: List[str] = []

    for sm in sql_metrics:
        candidates = {_norm_metric_key(sm), _norm_metric_key(sm.split(".")[-1])}
        matched_rec = next((by_norm[k] for k in candidates if k in by_norm), None)
        if matched_rec is None:
            uncertified.append(sm)
        elif matched_rec["status"] == "CERTIFIED":
            certified.append(sm)
        elif matched_rec["status"] in ("FAILED", "ERROR"):
            failed.append(f"{sm} ({matched_rec['status']})")
        else:
            uncertified.append(f"{sm} ({matched_rec['status']})")

    return {
        "sql_metrics": sql_metrics,
        "has_any_certified": has_any_certified,
        "certified": certified,
        "failed": failed,
        "uncertified": uncertified,
    }

