"""Generic unit tests for AI impact comparison outcomes.

Verifies the three distinct outcomes:
1. CHANGED: routing, SQL, grouping, or refusal changes between baseline and live.
2. UNCHANGED: both sides resolve successfully to identical SQL and objects.
3. NOT_COMPARABLE: both sides refuse or fail to resolve, which cannot be counted as evidence.
Also verifies weak sample reporting when non-comparable queries dominate.
"""

from unittest.mock import MagicMock
import pytest

from semabridge.governance.ai_impact_service import (
    run_ai_impact_check,
    QuestionImpactResult,
    AIImpactReport,
)
from semabridge.ai.nl_query_engine import QueryResolutionPlan


def test_three_outcomes_and_weak_sample(monkeypatch):
    """Verify that double refusal becomes NOT_COMPARABLE, successful matches become UNCHANGED,

    divergent queries become CHANGED, and weak sample is flagged when not_comparable > compared.
    """
    # Build mock engine behavior
    mock_base_plan_1 = QueryResolutionPlan(
        original_prompt="Q_AMBIGUOUS",
        refused=True,
        reason_text="Ambiguity detected between identical candidates",
        detected_metrics=[],
        detected_dimensions=[],
        generated_sql="",
    )
    mock_live_plan_1 = QueryResolutionPlan(
        original_prompt="Q_AMBIGUOUS",
        refused=True,
        reason_text="Ambiguity detected between identical candidates",
        detected_metrics=[],
        detected_dimensions=[],
        generated_sql="",
    )

    # Question 2: double unresolvable (neither generates SQL nor resolves object)
    mock_base_plan_2 = QueryResolutionPlan(
        original_prompt="Q_UNRESOLVABLE",
        refused=False,
        detected_metrics=[],
        detected_dimensions=[],
        generated_sql="",
    )
    mock_live_plan_2 = QueryResolutionPlan(
        original_prompt="Q_UNRESOLVABLE",
        refused=False,
        detected_metrics=[],
        detected_dimensions=[],
        generated_sql="",
    )

    # Question 3: successful identical resolution (UNCHANGED)
    mock_base_plan_3 = QueryResolutionPlan(
        original_prompt="Q_MATCHING",
        refused=False,
        detected_metrics=["OBJ_ALPHA"],
        detected_dimensions=[],
        generated_sql="SELECT SUM(COL_A) FROM TBL_ALPHA",
    )
    mock_live_plan_3 = QueryResolutionPlan(
        original_prompt="Q_MATCHING",
        refused=False,
        detected_metrics=["OBJ_ALPHA"],
        detected_dimensions=[],
        generated_sql="SELECT SUM(COL_A) FROM TBL_ALPHA",
    )

    # Question 4: divergent SQL (CHANGED)
    mock_base_plan_4 = QueryResolutionPlan(
        original_prompt="Q_DIVERGENT",
        refused=False,
        detected_metrics=["OBJ_BETA"],
        detected_dimensions=[],
        generated_sql="SELECT SUM(COL_B) FROM TBL_BETA",
    )
    mock_live_plan_4 = QueryResolutionPlan(
        original_prompt="Q_DIVERGENT",
        refused=False,
        detected_metrics=["OBJ_BETA"],
        detected_dimensions=[],
        generated_sql="SELECT SUM(COL_B) FROM TBL_BETA WHERE COL_C = 1",
    )

    plans_base = {
        "Q_AMBIGUOUS": mock_base_plan_1,
        "Q_UNRESOLVABLE": mock_base_plan_2,
        "Q_MATCHING": mock_base_plan_3,
        "Q_DIVERGENT": mock_base_plan_4,
    }
    plans_live = {
        "Q_AMBIGUOUS": mock_live_plan_1,
        "Q_UNRESOLVABLE": mock_live_plan_2,
        "Q_MATCHING": mock_live_plan_3,
        "Q_DIVERGENT": mock_live_plan_4,
    }

    # Mock engine resolve_query
    def mock_resolve(self, query: str, **kwargs):
        if "base" in getattr(self, "_mock_tag", "base"):
            return plans_base.get(query, mock_base_plan_3)
        return plans_live.get(query, mock_live_plan_3)

    # Mock search index
    mock_index = MagicMock()
    mock_index.docs = ["doc1"]
    mock_index.clone.return_value = mock_index

    # Mock ontology
    mock_fed = MagicMock()
    mock_fed.domains = {}
    mock_fed.metrics = {}
    mock_fed.concepts = {}

    # Mock ontology store init so it does not load disk files
    def mock_store_init(self, storage_dir=None, seed_demo=False):
        self.domains = {}
        self.load_errors = []
        self.search_index = mock_index
        self.query_engine = None

    monkeypatch.setattr(
        "semabridge.ontology.store.DomainOntologyStore.__init__",
        mock_store_init,
    )
    monkeypatch.setattr(
        "semabridge.ontology.store.DomainOntologyStore.get_federated_ontology",
        lambda self: mock_fed,
    )
    monkeypatch.setattr(
        "semabridge.ai.nl_query_engine.NLQueryEngine.resolve_query",
        mock_resolve,
    )

    def mock_init(self, search_index=None, ontology=None):
        pass

    monkeypatch.setattr("semabridge.ai.nl_query_engine.NLQueryEngine.__init__", mock_init)

    # Run check with custom questions
    custom_qs = ["Q_AMBIGUOUS", "Q_UNRESOLVABLE", "Q_MATCHING"]
    report = run_ai_impact_check(
        object_name="OBJ_ALPHA",
        kind="METRIC",
        baseline_description="Standard calculation rule.",
        live_description="Standard calculation rule.",
        custom_questions=custom_qs,
        bypass_cache=True,
    )

    assert report.questions_tested == 3
    assert report.questions_not_comparable == 2
    assert report.questions_compared == 1
    assert report.questions_unchanged == 1
    assert report.questions_changed == 0
    # 2 not comparable > 1 compared -> sample_is_weak must be True!
    assert report.sample_is_weak is True
    assert "Sample is weak" in report.risk_summary
    assert report.questions[0].status == "NOT_COMPARABLE"
    assert report.questions[1].status == "NOT_COMPARABLE"
    assert report.questions[2].status == "UNCHANGED"
