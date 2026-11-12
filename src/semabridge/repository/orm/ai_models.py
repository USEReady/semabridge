"""ORM models for AI query execution receipts, project-scoped verified queries,
and per-metric reconciliation certifications."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.repository.schema_compat import PROJECT_ID_LENGTH

_UTC_DT = DateTime(timezone=True)


def _new_id() -> str:
    return str(uuid.uuid4())


class AIQueryExecutionRow(Base):
    """Receipt of one query execution against a real warehouse.

    Only non-simulated successful receipts satisfy the verified-query gate.
    """

    __tablename__ = "ai_query_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[Optional[str]] = mapped_column(String(PROJECT_ID_LENGTH), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    view_name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    target: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="SNOWFLAKE")
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    executed_at: Mapped[datetime] = mapped_column(_UTC_DT, server_default=func.now(), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_simulated: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<AIQueryExecutionRow(id={self.id}, success={self.success}, rows={self.row_count})>"


class AIVerifiedQueryRow(Base):
    """Project-scoped, human-confirmed question/SQL pair backed by a live execution."""

    __tablename__ = "ai_verified_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    view_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    target: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="SNOWFLAKE")
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verified_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    verified_at: Mapped[int] = mapped_column(Integer, nullable=False)  # Unix epoch seconds
    is_onboarding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, server_default=func.now(), nullable=False)
    execution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return f"<AIVerifiedQueryRow(project_id={self.project_id}, name={self.name})>"


class MetricCertification(Base):
    """Per-metric BI-vs-warehouse reconciliation certification record."""

    __tablename__ = "ai_metric_certifications"
    __table_args__ = (
        Index("ix_ai_cert_proj_metric", "project_id", "metric_name"),
        Index("ix_ai_cert_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[Optional[str]] = mapped_column(String(PROJECT_ID_LENGTH), nullable=True)
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    metric_name: Mapped[str] = mapped_column(String(255), nullable=False)
    view_metric: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dimensions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    certification_badge: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    dax_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sql_query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dax_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sql_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mismatched_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_absolute_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_percentage_delta: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    absolute_tolerance: Mapped[float] = mapped_column(Float, nullable=False, default=0.01)
    relative_tolerance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0001)
    max_mismatch_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    certified_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    certified_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


# Backward-compatible aliases (names used by the ontology_validations branch).
QueryExecutionHistory = AIQueryExecutionRow
VerifiedQuery = AIVerifiedQueryRow
