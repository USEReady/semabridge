"""
Tests for semantic routing ORM models and repository.

Verifies that the RouterDecision, MeasureAnchorDecision, and FactDeploymentOutcome
ORM models can be created, persisted, and queried correctly.
"""

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from semabridge.repository.orm.base import Base
from semabridge.repository.semantic_routing_repository import (
    FactDeploymentOutcome,
    MeasureAnchorDecision,
    RouterDecision,
    SemanticRoutingRepository,
)


@pytest.fixture
def in_memory_db():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    yield session_local()
    engine.dispose()


class TestRouterDecisionModel:
    """Tests for RouterDecision ORM model."""

    def test_router_decision_creation(self, in_memory_db):
        """Verify RouterDecision can be created and persisted."""
        decision = RouterDecision(
            model_name="SalesDatamart",
            table_name="Fact",
            category="FACT",
            confidence="HIGH",
            reason_code="MANY_SIDE_OUTGOING"
        )
        in_memory_db.add(decision)
        in_memory_db.commit()

        # Verify it was saved
        stmt = select(RouterDecision).where(RouterDecision.table_name == "Fact")
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.model_name == "SalesDatamart"
        assert result.table_name == "Fact"
        assert result.category == "FACT"
        assert result.confidence == "HIGH"

    def test_router_decision_with_all_categories(self, in_memory_db):
        """Verify all table categories can be stored."""
        categories = ["FACT", "DIMENSION", "BRIDGE", "UNKNOWN"]
        for i, category in enumerate(categories):
            decision = RouterDecision(
                model_name=f"Model{i}",
                table_name=f"Table{i}",
                category=category,
                confidence="MEDIUM",
                reason_code="TEST"
            )
            in_memory_db.add(decision)
        in_memory_db.commit()

        stmt = select(RouterDecision)
        results = in_memory_db.execute(stmt).scalars().all()
        assert len(results) == 4
        stored_categories = {r.category for r in results}
        assert stored_categories == set(categories)

    def test_router_decision_unique_constraint(self, in_memory_db):
        """Verify unique constraint on (model_name, table_name)."""
        decision1 = RouterDecision(
            model_name="SalesDatamart",
            table_name="Fact",
            category="FACT",
            confidence="HIGH",
            reason_code="TEST"
        )
        in_memory_db.add(decision1)
        in_memory_db.commit()

        # Try to add duplicate
        decision2 = RouterDecision(
            model_name="SalesDatamart",
            table_name="Fact",
            category="DIMENSION",
            confidence="LOW",
            reason_code="TEST2"
        )
        in_memory_db.add(decision2)
        with pytest.raises(Exception):  # IntegrityError
            in_memory_db.commit()


class TestMeasureAnchorDecisionModel:
    """Tests for MeasureAnchorDecision ORM model."""

    def test_measure_anchor_creation(self, in_memory_db):
        """Verify MeasureAnchorDecision can be created and persisted."""
        anchor = MeasureAnchorDecision(
            model_name="SalesDatamart",
            measure_name="Total Revenue",
            anchor_table="Fact",
            confidence="HIGH",
            is_ambiguous=False,
            reason_code="SINGLE_ANCHOR",
            candidate_facts=json.dumps(["Fact"])
        )
        in_memory_db.add(anchor)
        in_memory_db.commit()

        stmt = select(MeasureAnchorDecision).where(MeasureAnchorDecision.measure_name == "Total Revenue")
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.model_name == "SalesDatamart"
        assert result.anchor_table == "Fact"
        assert result.is_ambiguous is False

    def test_measure_anchor_candidate_facts_json(self, in_memory_db):
        """Verify candidate_facts JSON is correctly stored and retrieved."""
        candidates = ["Fact", "SalesFact", "AggregatedFact"]
        anchor = MeasureAnchorDecision(
            model_name="Model1",
            measure_name="Revenue",
            anchor_table=None,  # Ambiguous, so no anchor
            confidence="LOW",
            is_ambiguous=True,
            reason_code="MULTI_FACT_ANCHOR",
            candidate_facts=json.dumps(candidates)
        )
        in_memory_db.add(anchor)
        in_memory_db.commit()

        stmt = select(MeasureAnchorDecision)
        result = in_memory_db.execute(stmt).scalar_one()
        stored_candidates = json.loads(result.candidate_facts)
        assert stored_candidates == candidates

    def test_measure_anchor_ambiguous_flag(self, in_memory_db):
        """Verify is_ambiguous flag marks measures for review."""
        unambiguous = MeasureAnchorDecision(
            model_name="Model1",
            measure_name="Clear Revenue",
            anchor_table="Fact",
            confidence="HIGH",
            is_ambiguous=False,
            reason_code="SINGLE_ANCHOR",
            candidate_facts="[]"
        )
        ambiguous = MeasureAnchorDecision(
            model_name="Model1",
            measure_name="Unclear Amount",
            anchor_table=None,
            confidence="NONE",
            is_ambiguous=True,
            reason_code="UNRESOLVED_REFERENCE",
            candidate_facts="[]"
        )
        in_memory_db.add(unambiguous)
        in_memory_db.add(ambiguous)
        in_memory_db.commit()

        stmt = select(MeasureAnchorDecision).where(MeasureAnchorDecision.is_ambiguous)
        ambiguous_results = in_memory_db.execute(stmt).scalars().all()
        assert len(ambiguous_results) == 1
        assert ambiguous_results[0].measure_name == "Unclear Amount"

    def test_measure_anchor_unique_constraint(self, in_memory_db):
        """Verify unique constraint on (model_name, measure_name)."""
        anchor1 = MeasureAnchorDecision(
            model_name="Model1",
            measure_name="Revenue",
            anchor_table="Fact",
            confidence="HIGH",
            is_ambiguous=False,
            reason_code="TEST",
            candidate_facts="[]"
        )
        in_memory_db.add(anchor1)
        in_memory_db.commit()

        anchor2 = MeasureAnchorDecision(
            model_name="Model1",
            measure_name="Revenue",
            anchor_table="AnotherFact",
            confidence="LOW",
            is_ambiguous=False,
            reason_code="TEST2",
            candidate_facts="[]"
        )
        in_memory_db.add(anchor2)
        with pytest.raises(Exception):
            in_memory_db.commit()


class TestFactDeploymentOutcomeModel:
    """Tests for FactDeploymentOutcome ORM model."""

    def test_fact_deployment_outcome_creation(self, in_memory_db):
        """Verify FactDeploymentOutcome can be created and persisted."""
        outcome = FactDeploymentOutcome(
            model_name="SalesDatamart",
            fact_table="Fact",
            artifact_name="SalesDatamart_fact_Fact_metric_view",
            deployment_status="DEPLOYED",
            measure_count=5,
            dimension_count=3,
            traversal_trace=json.dumps({"reachable_tables": ["Date", "Customer", "Product"], "cycle_breaks": []})
        )
        in_memory_db.add(outcome)
        in_memory_db.commit()

        stmt = select(FactDeploymentOutcome).where(FactDeploymentOutcome.fact_table == "Fact")
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.model_name == "SalesDatamart"
        assert result.deployment_status == "DEPLOYED"
        assert result.measure_count == 5
        assert result.dimension_count == 3

    def test_fact_deployment_outcome_all_statuses(self, in_memory_db):
        """Verify all deployment statuses can be stored."""
        statuses = ["DEPLOYED", "SKIPPED", "FAILED", "PLAN_ONLY"]
        for i, status in enumerate(statuses):
            outcome = FactDeploymentOutcome(
                model_name=f"Model{i}",
                fact_table=f"Fact{i}",
                artifact_name=f"artifact_{i}",
                deployment_status=status,
                measure_count=i,
                dimension_count=i
            )
            in_memory_db.add(outcome)
        in_memory_db.commit()

        stmt = select(FactDeploymentOutcome)
        results = in_memory_db.execute(stmt).scalars().all()
        assert len(results) == 4
        stored_statuses = {r.deployment_status for r in results}
        assert stored_statuses == set(statuses)

    def test_fact_deployment_outcome_with_skip_reason(self, in_memory_db):
        """Verify skipped reason can be stored."""
        outcome = FactDeploymentOutcome(
            model_name="Model1",
            fact_table="Fact",
            artifact_name="artifact",
            deployment_status="SKIPPED",
            skipped_reason="NO_MEASURES",
            measure_count=0,
            dimension_count=2
        )
        in_memory_db.add(outcome)
        in_memory_db.commit()

        stmt = select(FactDeploymentOutcome)
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.skipped_reason == "NO_MEASURES"

    def test_fact_deployment_outcome_traversal_trace_json(self, in_memory_db):
        """Verify traversal_trace JSON is stored and retrieved."""
        trace = {
            "reachable_tables": ["Date", "Customer", "Product"],
            "cycle_breaks": ["Product -> Date"],
            "max_depth_reached": 5
        }
        outcome = FactDeploymentOutcome(
            model_name="Model1",
            fact_table="Fact",
            artifact_name="artifact",
            deployment_status="DEPLOYED",
            measure_count=2,
            dimension_count=3,
            traversal_trace=json.dumps(trace)
        )
        in_memory_db.add(outcome)
        in_memory_db.commit()

        stmt = select(FactDeploymentOutcome)
        result = in_memory_db.execute(stmt).scalar_one()
        stored_trace = json.loads(result.traversal_trace)
        assert stored_trace == trace

    def test_fact_deployment_outcome_unique_constraint(self, in_memory_db):
        """Verify unique constraint on (model_name, fact_table)."""
        outcome1 = FactDeploymentOutcome(
            model_name="Model1",
            fact_table="Fact",
            artifact_name="artifact1",
            deployment_status="DEPLOYED",
            measure_count=1,
            dimension_count=1
        )
        in_memory_db.add(outcome1)
        in_memory_db.commit()

        outcome2 = FactDeploymentOutcome(
            model_name="Model1",
            fact_table="Fact",
            artifact_name="artifact2",
            deployment_status="SKIPPED",
            measure_count=0,
            dimension_count=0
        )
        in_memory_db.add(outcome2)
        with pytest.raises(Exception):
            in_memory_db.commit()


class TestSemanticRoutingRepository:
    """Tests for SemanticRoutingRepository class."""

    def test_repository_save_routing_decision(self, in_memory_db):
        """Verify repository can save routing decisions."""
        repo = SemanticRoutingRepository(in_memory_db)
        decision = RouterDecision(
            model_name="Model1",
            table_name="Fact",
            category="FACT",
            confidence="HIGH",
            reason_code="TEST"
        )
        repo.save_routing_decision(decision)

        stmt = select(RouterDecision)
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.table_name == "Fact"

    def test_repository_get_routing_for_model(self, in_memory_db):
        """Verify repository can query routing decisions by model."""
        repo = SemanticRoutingRepository(in_memory_db)

        # Add multiple decisions for same model
        for i, category in enumerate(["FACT", "DIMENSION", "DIMENSION"]):
            decision = RouterDecision(
                model_name="Model1",
                table_name=f"Table{i}",
                category=category,
                confidence="HIGH",
                reason_code="TEST"
            )
            repo.save_routing_decision(decision)

        results = repo.get_routing_for_model("Model1")
        assert len(results) == 3
        assert all(r.model_name == "Model1" for r in results)

    def test_repository_save_measure_anchor(self, in_memory_db):
        """Verify repository can save measure anchor decisions."""
        repo = SemanticRoutingRepository(in_memory_db)
        anchor = MeasureAnchorDecision(
            model_name="Model1",
            measure_name="Revenue",
            anchor_table="Fact",
            confidence="HIGH",
            is_ambiguous=False,
            reason_code="TEST",
            candidate_facts="[]"
        )
        repo.save_measure_anchor(anchor)

        stmt = select(MeasureAnchorDecision)
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.measure_name == "Revenue"

    def test_repository_get_ambiguous_measures(self, in_memory_db):
        """Verify repository can query only ambiguous measures."""
        repo = SemanticRoutingRepository(in_memory_db)

        # Add mix of ambiguous and unambiguous
        for i, is_ambiguous in enumerate([False, True, True, False]):
            anchor = MeasureAnchorDecision(
                model_name="Model1",
                measure_name=f"Measure{i}",
                anchor_table="Fact" if not is_ambiguous else None,
                confidence="HIGH" if not is_ambiguous else "NONE",
                is_ambiguous=is_ambiguous,
                reason_code="TEST",
                candidate_facts="[]"
            )
            repo.save_measure_anchor(anchor)

        ambiguous = repo.get_ambiguous_measures_for_model("Model1")
        assert len(ambiguous) == 2
        assert all(m.is_ambiguous for m in ambiguous)

    def test_repository_save_deployment_outcome(self, in_memory_db):
        """Verify repository can save deployment outcomes."""
        repo = SemanticRoutingRepository(in_memory_db)
        outcome = FactDeploymentOutcome(
            model_name="Model1",
            fact_table="Fact",
            artifact_name="artifact",
            deployment_status="DEPLOYED",
            measure_count=5,
            dimension_count=3
        )
        repo.save_deployment_outcome(outcome)

        stmt = select(FactDeploymentOutcome)
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.deployment_status == "DEPLOYED"

    def test_repository_get_deployment_outcomes_for_model(self, in_memory_db):
        """Verify repository can query deployment outcomes by model."""
        repo = SemanticRoutingRepository(in_memory_db)

        # Add multiple outcomes
        for i in range(3):
            outcome = FactDeploymentOutcome(
                model_name="Model1",
                fact_table=f"Fact{i}",
                artifact_name=f"artifact{i}",
                deployment_status="DEPLOYED",
                measure_count=i,
                dimension_count=i
            )
            repo.save_deployment_outcome(outcome)

        outcomes = repo.get_deployment_outcomes_for_model("Model1")
        assert len(outcomes) == 3
        assert all(o.model_name == "Model1" for o in outcomes)

    def test_repository_timestamps_auto_set(self, in_memory_db):
        """Verify created_at and updated_at are automatically set."""
        repo = SemanticRoutingRepository(in_memory_db)

        decision = RouterDecision(
            model_name="Model1",
            table_name="Fact",
            category="FACT",
            confidence="HIGH",
            reason_code="TEST"
        )
        repo.save_routing_decision(decision)

        stmt = select(RouterDecision)
        result = in_memory_db.execute(stmt).scalar_one()
        assert result.created_at is not None
        assert result.updated_at is not None
        # Just verify both timestamps are set; SQLite doesn't handle timezones same as PostgreSQL
        assert isinstance(result.created_at, datetime)
        assert isinstance(result.updated_at, datetime)
