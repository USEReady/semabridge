"""Ask: a matching saved question answers first; every answer is labelled
with what is known about its measures vs Power BI."""
from types import SimpleNamespace

import pytest

from semabridge.ai.nl_query_engine import QueryResolutionPlan
from semabridge.api.routers import ai_router as ar
from semabridge.reconciliation.certification_service import extract_semantic_view_metrics

SAVED = [{"question": "What is total revenue by region?", "sql": "SELECT region, MEASURE(`total_revenue`) FROM mv GROUP BY 1",
          "target": "DATABRICKS", "view_name": "main.sales.mv", "verified_by": "ana"}]


@pytest.fixture
def saved(monkeypatch):
    import semabridge.ai.verified_queries_service as vqs

    monkeypatch.setattr(vqs, "list_verified_queries_for_project", lambda pid: SAVED)


def _req(prompt):
    return SimpleNamespace(prompt=prompt, project_id="p1")


def test_matching_saved_question_answers_first(saved):
    plan = ar._verified_answer(_req("total revenue by region"), SimpleNamespace(), "DATABRICKS")
    assert plan.trust == "verified" and "MEASURE(`total_revenue`)" in plan.generated_sql


def test_different_question_or_target_is_not_matched(saved):
    assert ar._verified_answer(_req("units by month"), SimpleNamespace(), "DATABRICKS") is None
    assert ar._verified_answer(_req("total revenue by region"), SimpleNamespace(), "SNOWFLAKE") is None


def _plan(sql):
    return QueryResolutionPlan(original_prompt="q", detected_metrics=[], detected_dimensions=[], resolved_tables=[],
                               generated_sql=sql, confidence_score=0.5, explanation="")


@pytest.mark.parametrize("status, trust", [
    ({"certified": ["total_revenue"], "failed": [], "uncertified": []}, "matches_power_bi"),
    ({"certified": [], "failed": ["total_revenue (FAILED)"], "uncertified": []}, "differs"),
    ({"certified": [], "failed": [], "uncertified": ["units"]}, "unverified"),
])
def test_trust_label(monkeypatch, status, trust):
    import semabridge.reconciliation.certification_service as cs

    monkeypatch.setattr(cs, "check_sql_certification_status", lambda sql, project_id=None: status)
    assert ar._with_trust(_plan("SELECT MEASURE(`total_revenue`) FROM mv"), "p1").trust == trust


def test_databricks_measures_are_recognised():
    assert extract_semantic_view_metrics("SELECT region, MEASURE(`total_revenue`), MEASURE(units) FROM mv") == [
        "total_revenue", "units"]
