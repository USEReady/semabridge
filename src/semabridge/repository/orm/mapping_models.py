"""Field mapping ORM models: ModelMappingRow, SynonymOverride."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func, true
from sqlalchemy.orm import Mapped, mapped_column

from semabridge.repository.orm.base import Base

_UTC_DT = DateTime(timezone=True)


class ModelMappingRow(Base):
    """Source ↔ target mapping registry.

    Mirrors the ``model_mappings`` table from SYNC_SCHEMA_DDL.
    """

    __tablename__ = "model_mappings"

    mapping_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(_UTC_DT, nullable=True)
    last_osi_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    def __repr__(self) -> str:
        return (
            f"<ModelMappingRow(mapping_id={self.mapping_id!r}, "
            f"model_name={self.model_name!r})>"
        )


class SynonymOverride(Base):
    """Project-scoped UI synonym overrides for columns and measures."""

    __tablename__ = "synonym_overrides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    synonyms_json: Mapped[str] = mapped_column("synonyms", Text, nullable=False, default="[]")
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "model_name", "table_name", "column_name"),
    )

    @property
    def synonyms(self) -> List[str]:
        try:
            values = json.loads(self.synonyms_json or "[]")
        except Exception:
            return []
        if not isinstance(values, list):
            return []
        return [str(value).strip() for value in values if str(value or "").strip()]

    @synonyms.setter
    def synonyms(self, values: List[str]) -> None:
        cleaned = [str(value).strip() for value in (values or []) if str(value or "").strip()]
        self.synonyms_json = json.dumps(cleaned, ensure_ascii=False)


class PrecomputeAggregationOverride(Base):
    """Project-scoped override for the aggregate function (AVG/SUM/MIN/MAX/
    MODE) used to collapse a cross-table precomputed column to one value per
    join key, when the model's own relationship metadata can't confirm the
    lookup is on a unique key. Same shape as SynonymOverride, keyed on the
    column being looked up rather than the fact table it's precomputed into
    (one column's "how to summarize this when joined non-uniquely" choice
    is a property of the column, not of any one target fact table)."""

    __tablename__ = "precompute_aggregation_overrides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregation_strategy: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        _UTC_DT, server_default=func.now(), onupdate=func.now(), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("project_id", "model_name", "table_name", "column_name"),
    )
