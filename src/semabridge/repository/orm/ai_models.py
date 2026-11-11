"""ORM models for Cortex Verified Queries and query execution verification receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.repository.schema_compat import PROJECT_ID_LENGTH

_UTC_DT = DateTime(timezone=True)


class QueryExecutionHistory(Base):
    """Records executed SQL queries so Verified Queries can prove prior non-simulated execution."""

    __tablename__ = "ai_query_execution_history"
    __table_args__ = (
        Index("ix_ai_qeh_hash", "normalized_sql_hash"),
        Index("ix_ai_qeh_proj_hash", "project_id", "normalized_sql_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[Optional[str]] = mapped_column(String(PROJECT_ID_LENGTH), nullable=True)
    target: Mapped[str] = mapped_column(String(50), nullable=False, default="SNOWFLAKE")
    normalized_sql_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    executed_sql: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    executed_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


class VerifiedQuery(Base):
    """Project-scoped human-verified NL question and SQL pair backed by live execution."""

    __tablename__ = "ai_verified_queries"
    __table_args__ = (
        Index("ix_ai_vq_project", "project_id"),
        Index("ix_ai_vq_proj_qhash", "project_id", "question_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(PROJECT_ID_LENGTH), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sql: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_sql_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(50), nullable=False, default="SNOWFLAKE")
    domain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    verified_by: Mapped[str] = mapped_column(String(120), nullable=False, default="semabridge_analyst")
    verified_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)


class MetricCertification(Base):
    """Stores mathematical reconciliation certification status per metric."""

    __tablename__ = "ai_metric_certifications"
    __table_args__ = (
        Index("ix_ai_cert_proj_metric", "project_id", "metric_name"),
        Index("ix_ai_cert_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
    certified_by: Mapped[str] = mapped_column(String(120), nullable=False, default="semabridge_verifier")
    certified_at: Mapped[datetime] = mapped_column(_UTC_DT, nullable=False)

