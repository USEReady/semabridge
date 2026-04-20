"""
Semantic Routing ORM Models and Repository.

Provides persistent storage for semantic router decisions:
- RouterDecision: Table categorization (FACT, DIMENSION, BRIDGE)
- MeasureAnchorDecision: Measure-to-fact mapping with ambiguity flags
- FactDeploymentOutcome: Per-fact artifact generation and deployment results

All tables include audit fields (created_at, updated_at) and JSON columns for
complex nested data (candidate_facts, traversal_trace).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from semabridge.repository.orm.base import Base
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class RouterDecision(Base):
    """Table categorization decision in the semantic router.

    Stores the result of table-role detection: whether a table is a FACT,
    DIMENSION, or BRIDGE (ambiguous/many-to-many) table.

    Attributes:
        id: Auto-increment integer primary key.
        model_name: Semantic model name (e.g., 'SalesDatamart').
        table_name: SML dataset name being categorized.
        category: Detected category ('FACT', 'DIMENSION', 'BRIDGE', 'UNKNOWN').
        confidence: Confidence score (HIGH, MEDIUM, LOW, NONE).
        reason_code: Machine-readable reason for categorization (e.g., 'MANY_SIDE_OUTGOING', 'ISOLATED_TABLE').
        created_at: Timestamp when decision was made.
        updated_at: Timestamp of last update.
    """

    __tablename__ = "router_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    table_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # FACT, DIMENSION, BRIDGE, UNKNOWN
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)  # HIGH, MEDIUM, LOW, NONE
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_router_decisions_model_table", "model_name", "table_name", unique=True),
    )


class MeasureAnchorDecision(Base):
    """Measure-to-fact anchor decision.

    Records which fact table (if any) should anchor a given measure, along with
    ambiguity flags and candidate alternatives for review.

    Attributes:
        id: Auto-increment integer primary key.
        model_name: Semantic model name.
        measure_name: SML metric name.
        anchor_table: Detected or assigned fact table anchor (nullable if ambiguous).
        confidence: Confidence in anchor decision (HIGH, MEDIUM, LOW, NONE).
        is_ambiguous: Whether this measure is marked for review/skipping.
        reason_code: Machine-readable reason (e.g., 'UNRESOLVED_REFERENCE', 'MULTI_FACT_ANCHOR', 'NO_FACT_REACHABLE').
        candidate_facts: JSON list of fact tables that could anchor this measure (for review).
        created_at: Timestamp when decision was made.
        updated_at: Timestamp of last update.
    """

    __tablename__ = "measure_anchor_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    measure_name: Mapped[str] = mapped_column(String(255), nullable=False)
    anchor_table: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)  # HIGH, MEDIUM, LOW, NONE
    is_ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_facts: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON: list of fact names
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_measure_anchor_decisions_model_measure", "model_name", "measure_name", unique=True),
    )


class FactDeploymentOutcome(Base):
    """Per-fact metric-view artifact generation outcome.

    Records the result of per-fact metric-view generation and deployment,
    including dimension and measure counts, traversal details, and status.

    Attributes:
        id: Auto-increment integer primary key.
        model_name: Semantic model name.
        fact_table: The fact table anchoring this artifact.
        artifact_name: Generated metric-view artifact name (e.g., 'SalesDatamart_fact_Fact_metric_view').
        deployment_status: DEPLOYED, SKIPPED, FAILED, PLAN_ONLY.
        skipped_reason: Reason if skipped/failed (e.g., 'NO_MEASURES', 'INVALID_RELATIONSHIPS', 'CYCLE_DETECTED').
        measure_count: Number of measures included in artifact.
        dimension_count: Number of dimensions reachable from fact.
        traversal_trace: JSON containing traversal details (reachable_tables, cycle_breaks, max_depth_reached).
        created_at: Timestamp when outcome was recorded.
        updated_at: Timestamp of last update.
    """

    __tablename__ = "fact_deployment_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    fact_table: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    deployment_status: Mapped[str] = mapped_column(String(20), nullable=False)  # DEPLOYED, SKIPPED, FAILED, PLAN_ONLY
    skipped_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    measure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dimension_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    traversal_trace: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON: reachable_tables, cycle_breaks, max_depth_reached
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_fact_deployment_outcomes_model_fact", "model_name", "fact_table", unique=True),
    )


class SemanticRoutingRepository:
    """Repository for semantic routing decision persistence.

    Provides CRUD methods to save and query routing decisions, measure anchors,
    and deployment outcomes from PostgreSQL.
    """

    def __init__(self, session: Session) -> None:
        """Initialize repository with SQLAlchemy session.

        Args:
            session: SQLAlchemy Session for database operations.
        """
        self._session = session

    def save_routing_decision(self, decision: RouterDecision) -> None:
        """Save or update a table categorization decision.

        Args:
            decision: RouterDecision instance to persist.
        """
        try:
            # Merge to handle both insert and update
            self._session.merge(decision)
            self._session.commit()
            logger.debug(f"Saved routing decision for {decision.model_name}.{decision.table_name}")
        except Exception as e:
            self._session.rollback()
            logger.error(f"Failed to save routing decision: {e}")
            raise

    def save_measure_anchor(self, anchor: MeasureAnchorDecision) -> None:
        """Save or update a measure anchor decision.

        Args:
            anchor: MeasureAnchorDecision instance to persist.
        """
        try:
            self._session.merge(anchor)
            self._session.commit()
            logger.debug(f"Saved anchor decision for {anchor.model_name}.{anchor.measure_name}")
        except Exception as e:
            self._session.rollback()
            logger.error(f"Failed to save measure anchor decision: {e}")
            raise

    def save_deployment_outcome(self, outcome: FactDeploymentOutcome) -> None:
        """Save or update a fact deployment outcome.

        Args:
            outcome: FactDeploymentOutcome instance to persist.
        """
        try:
            self._session.merge(outcome)
            self._session.commit()
            logger.debug(f"Saved deployment outcome for {outcome.model_name}.{outcome.fact_table}")
        except Exception as e:
            self._session.rollback()
            logger.error(f"Failed to save deployment outcome: {e}")
            raise

    def get_routing_for_model(self, model_name: str) -> list[RouterDecision]:
        """Query all routing decisions for a model.

        Args:
            model_name: Semantic model name.

        Returns:
            List of RouterDecision records, ordered by table_name.
        """
        try:
            stmt = select(RouterDecision).where(RouterDecision.model_name == model_name).order_by(RouterDecision.table_name)
            results = self._session.execute(stmt).scalars().all()
            return list(results)
        except Exception as e:
            logger.error(f"Failed to query routing decisions for {model_name}: {e}")
            raise

    def get_measure_anchors_for_model(self, model_name: str) -> list[MeasureAnchorDecision]:
        """Query all measure anchor decisions for a model.

        Args:
            model_name: Semantic model name.

        Returns:
            List of MeasureAnchorDecision records, ordered by measure_name.
        """
        try:
            stmt = select(MeasureAnchorDecision).where(MeasureAnchorDecision.model_name == model_name).order_by(MeasureAnchorDecision.measure_name)
            results = self._session.execute(stmt).scalars().all()
            return list(results)
        except Exception as e:
            logger.error(f"Failed to query measure anchors for {model_name}: {e}")
            raise

    def get_deployment_outcomes_for_model(self, model_name: str) -> list[FactDeploymentOutcome]:
        """Query all deployment outcomes for a model.

        Args:
            model_name: Semantic model name.

        Returns:
            List of FactDeploymentOutcome records, ordered by fact_table.
        """
        try:
            stmt = select(FactDeploymentOutcome).where(FactDeploymentOutcome.model_name == model_name).order_by(FactDeploymentOutcome.fact_table)
            results = self._session.execute(stmt).scalars().all()
            return list(results)
        except Exception as e:
            logger.error(f"Failed to query deployment outcomes for {model_name}: {e}")
            raise

    def get_ambiguous_measures_for_model(self, model_name: str) -> list[MeasureAnchorDecision]:
        """Query all ambiguous measures flagged for review in a model.

        Args:
            model_name: Semantic model name.

        Returns:
            List of MeasureAnchorDecision records where is_ambiguous=True.
        """
        try:
            stmt = (
                select(MeasureAnchorDecision)
                .where(
                    (MeasureAnchorDecision.model_name == model_name)
                    & MeasureAnchorDecision.is_ambiguous
                )
                .order_by(MeasureAnchorDecision.measure_name)
            )
            results = self._session.execute(stmt).scalars().all()
            return list(results)
        except Exception as e:
            logger.error(f"Failed to query ambiguous measures for {model_name}: {e}")
            raise
