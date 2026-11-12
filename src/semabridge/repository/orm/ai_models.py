"""ORM models for AI query execution tracking and project-scoped verified queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class MetricCertification(Base):
    """Stores mathematical reconciliation certification status per metric."""

    __tablename__ = "ai_metric_certifications"
    __table_args__ = (
        Index("ix_ai_cert_proj_metric", "project_id", "metric_name"),
        Index("ix_ai_cert_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
    certified_by: Mapped[str] = mapped_column(String(120), nullable=False, default="semabridge_verifier")
    certified_at: Mapped[datetime] = mapped_column(_UTC_DT, server_default=func.now(), nullable=False)

    def __repr__(self) -> str:
        return f"<MetricCertification(metric={self.metric_name}, status={self.status})>"


class AIQueryExecutionRow(Base):

    """Database record of an AI query execution against a warehouse or local engine."""

    __tablename__ = "ai_query_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    view_name: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    executed_at: Mapped[datetime] = mapped_column(_UTC_DT, server_default=func.now(), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<AIQueryExecutionRow(id={self.id}, success={self.success}, rows={self.row_count})>"


class AIVerifiedQueryRow(Base):
    """Project-scoped verified query record stored in the database."""

    __tablename__ = "ai_verified_queries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    view_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verified_by: Mapped[str] = mapped_column(String(255), nullable=False, default="session_user")
    verified_at: Mapped[int] = mapped_column(Integer, nullable=False)  # Unix epoch seconds
    is_onboarding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(_UTC_DT, server_default=func.now(), nullable=False)
    execution_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return f"<AIVerifiedQueryRow(project_id={self.project_id}, name={self.name})>"


# Backward compatibility aliases
QueryExecutionHistory = AIQueryExecutionRow
VerifiedQuery = AIVerifiedQueryRow


