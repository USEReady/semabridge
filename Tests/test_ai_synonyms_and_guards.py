from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch
from semabridge.utils.synonyms import (
    is_filtered_metric,
    disambiguate_metric_synonyms,
    get_metric_qualifier_word,
)
from semabridge.ai.sql_guard_engine import (
    inspect_and_guard_generated_sql,
    SQLGuardDecision,
)
from semabridge.ontology.models import (
    DomainMetric,
    DomainConcept,
    DomainAttribute,
    DomainOntology,
    FederatedEnterpriseOntology,
)
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.bi_truth_service import (
    build_engine_instructions_for_view,
    set_truth_case_status,
)


def test_disambiguate_metric_synonyms():
    """Two metrics sharing a generic synonym -> collision recorded, base metric keeps it, filtered metric qualified."""
    m_base = {
        "unique_name": "EMPCOUNT",
        "name": "EMPCOUNT",
        "label": "EmpCount",
        "expression": "COUNT(EMPLOYEE.EMPLID)",
        "sql_expression": "COUNT(EMPLOYEE.EMPLID)",
        "synonyms": ["EmpCount", "Employees", "Headcount"],
    }
    m_filtered = {
        "unique_name": "ACTIVES",
        "name": "ACTIVES",
        "label": "Actives",
        "expression": "COUNT(CASE WHEN TERMDATE IS NULL THEN EMPLID END)",
        "sql_expression": "COUNT(CASE WHEN TERMDATE IS NULL THEN EMPLID END)",
        "synonyms": ["Actives", "Employees"],
    }

    assert not is_filtered_metric(m_base)
    assert is_filtered_metric(m_filtered)
    assert get_metric_qualifier_word(m_filtered) == "Actives"

    metrics, collisions = disambiguate_metric_synonyms([m_base, m_filtered], view_name="V_HR")

    assert len(collisions) == 1
    coll = collisions[0]
    assert coll["view"] == "V_HR"
    assert coll["term"].lower() == "employees"
    assert coll["resolved_to"] == "EMPCOUNT"

    # Base keeps "Employees", filtered gets "Actives Employees"
    assert "Employees" in m_base["synonyms"]
    assert "Employees" not in m_filtered["synonyms"]
    assert any("Actives Employees" in s for s in m_filtered["synonyms"])


def test_sql_guard_today_date_rewrite_to_sply():
    """SQL with CURRENT_DATE on historical model -> rewritten to governed period metrics (_SPLY)."""
    m_base = DomainMetric(name="ACTIVES", sql_expression="COUNT(ID)")
    m_sply = DomainMetric(name="ACTIVES_SPLY", sql_expression="COUNT(CASE WHEN YR = 2025 THEN ID END)")

    bad_sql = (
        "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.HR.V_HR METRICS actives "
        "WHERE date_year = DATE_PART('year', CURRENT_DATE))"
    )

    decision = inspect_and_guard_generated_sql(
        sql=bad_sql,
        prompt="How do our active employees compare to last year?",
        all_metrics=[m_base, m_sply],
        semantic_view_name="SEMABRIDGE.HR.V_HR",
    )

    assert decision.allowed is True
    assert decision.rewritten_sql is not None
    assert "ACTIVES_SPLY" in decision.rewritten_sql
    assert "SEMANTIC_VIEW(" in decision.rewritten_sql
    assert any("Rewritten to governed period metrics" in w for w in decision.warnings)
    assert decision.query_path == "semantic_view"


def test_sql_guard_today_date_blocked_when_no_sply():
    """SQL with CURRENT_DATE on historical model without period metrics -> refused with clear error."""
    m_base = DomainMetric(name="SALES_COUNT", sql_expression="COUNT(ID)")
    m_sply_other = DomainMetric(name="OTHER_SPLY", sql_expression="SUM(AMOUNT)")

    bad_sql = (
        "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.SALES.V_SALES METRICS sales_count "
        "WHERE year = DATE_PART('year', CURRENT_DATE))"
    )

    decision = inspect_and_guard_generated_sql(
        sql=bad_sql,
        prompt="Show sales count this year",
        all_metrics=[m_base, m_sply_other],
        semantic_view_name="SEMABRIDGE.SALES.V_SALES",
    )

    assert decision.allowed is False
    assert "CURRENT_DATE" in (decision.refusal_reason or "")
    assert not decision.checks_passed


def test_sql_guard_today_date_allowed_when_prompt_asks_today():
    """SQL with CURRENT_DATE is allowed if the user explicitly asked about today."""
    m_base = DomainMetric(name="ACTIVE_SESSIONS", sql_expression="COUNT(ID)")
    m_sply = DomainMetric(name="ACTIVE_SESSIONS_SPLY", sql_expression="COUNT(ID)")

    sql = "SELECT * FROM SEMANTIC_VIEW(V_SYS METRICS active_sessions WHERE dt = CURRENT_DATE)"

    decision = inspect_and_guard_generated_sql(
        sql=sql,
        prompt="How many active sessions are there right now today?",
        all_metrics=[m_base, m_sply],
    )

    assert decision.allowed is True


def test_sql_guard_free_form_table_flagged():
    """Free-form SQL on physical/enriched tables -> labelled free-form, suggests governed metrics, ineligible for certification."""
    m_emp = DomainMetric(name="EMPCOUNT", sql_expression="COUNT(EMPLOYEE.EMPLID)")

    free_form_sql = (
        "WITH __employee AS (SELECT emplid, termdate FROM SEMABRIDGE.HR.EMPLOYEE_ENRICHED) "
        "SELECT COUNT(emplid) FROM __employee WHERE termdate IS NULL"
    )

    decision = inspect_and_guard_generated_sql(
        sql=free_form_sql,
        prompt="How many employees do we have?",
        all_metrics=[m_emp],
    )

    assert decision.query_path == "free-form (not governed)"
    assert decision.is_certified_eligible is False
    assert any("Free-form SQL queries physical/base tables directly" in w for w in decision.warnings)


def test_sql_guard_snapshot_safety_and_business_rules():
    """Detects aggregation over snapshot tables and invented business rule conditions."""
    m_snap = DomainMetric(
        name="HEADCOUNT",
        sql_expression="COUNT(ID)",
        non_additive_dimension="DATE_PERIOD",
        concept="EMPLOYEE",
    )

    sql = (
        "SELECT bu, "
        "CASE WHEN e.termdate IS NULL THEN 'Active' ELSE 'Separated' END AS status, "
        "AVG(e.tenuredays) AS avg_tenure "
        "FROM EMPLOYEE_ENRICHED e GROUP BY bu, status"
    )

    decision = inspect_and_guard_generated_sql(
        sql=sql,
        prompt="Compare tenure for active vs separated",
        all_metrics=[m_snap],
    )

    # Rule 3: Snapshot safety
    assert any("Rows may repeat per period" in a for a in decision.assumptions)
    # Rule 4: Business-rule guess
    assert any("Assumes 'Active' vs 'Separated' is defined by" in a for a in decision.assumptions)


def test_sql_guard_helper_metrics():
    """Detects auto-generated helper metrics (SUM_OF_*) and suggests business measures."""
    m_biz = DomainMetric(name="BADHIRES", label="Bad Hires", sql_expression="SUM(IS_BAD_HIRE)")

    sql = "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.HR.V_HR METRICS sum_of_badhires)"

    decision = inspect_and_guard_generated_sql(
        sql=sql,
        prompt="How many bad hires did we have?",
        all_metrics=[m_biz],
    )

    assert any("Auto-generated helper 'sum_of_badhires' used" in w for w in decision.warnings)


def test_build_engine_instructions():
    """build_engine_instructions_for_view includes core governance rules and period metrics."""
    m1 = DomainMetric(name="ACTIVES", sql_expression="COUNT(ID)")
    m2 = DomainMetric(name="ACTIVES_SPLY", sql_expression="COUNT(ID)")
    m3 = DomainMetric(name="HEADCOUNT", sql_expression="COUNT(ID)", non_additive_dimension="DATE.PERIOD")

    instructions = build_engine_instructions_for_view(
        view_name="V_HR",
        metrics=[m1, m2, m3],
        data_max_date="2025-12-31",
    )

    assert "Always query using SEMANTIC_VIEW" in instructions
    assert "Never filter or anchor on the current date" in instructions
    assert "Model data ends at 2025-12-31" in instructions
    assert "ACTIVES_SPLY" in instructions
    assert "HEADCOUNT by DATE.PERIOD" in instructions


def test_bi_truth_auto_promotion_to_verified_query():
    """Setting truth case to 'certified' automatically promotes it to a verified query."""
    case = {
        "id": "truth-auto-test",
        "project_id": "proj-auto",
        "domain": "hr",
        "metric_name": "EMPCOUNT",
        "question": "What is total headcount?",
        "expected_sql": "SELECT * FROM SEMANTIC_VIEW(V_HR METRICS empcount)",
        "expected_value": 32564,
        "expected_row_count": 1,
        "status": "approved",
        "certified_parity": True,
        "view": "V_HR",
    }

    with patch("semabridge.ai.bi_truth_service._load_suite", return_value={"cases": [case], "runs": []}), \
         patch("semabridge.ai.bi_truth_service._save_suite") as mock_save, \
         patch("semabridge.ai.verified_queries_service.record_query_execution") as mock_rec, \
         patch("semabridge.ai.verified_queries_service.save_verified_query") as mock_save_vq:

        updated = set_truth_case_status("truth-auto-test", "certified")

        assert updated is not None
        assert updated["status"] == "certified"
        mock_rec.assert_called_once()
        mock_save_vq.assert_called_once()


def test_golden_natural_language_queries_resolution():
    """Golden test verifying natural language query disambiguation on real enterprise ontology:
    1. 'How many employees do we have?' -> Unfiltered base count (EmpCount), not filtered metric (Actives).
    2. 'How many active employees are there?' -> Filtered metric (Actives).
    3. 'How many employees separated in total?' -> Separation metric (Seps).
    4. 'What is the average tenure in months by age group and pay type?' -> AVG Tenure Months with AgeGroup and PayType dimensions.
    """
    hr_path = Path("Config/ontologies/hr__human_resources_sample_pbix.ontology.yaml")
    if not hr_path.exists():
        pytest.skip("HR ontology file not found")

    dom = FederatedOntologyLoader.load_from_file(hr_path)
    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    eng = NLQueryEngine(idx, fed)

    # 1. Base unfiltered count
    q1 = eng.resolve_query("How many employees do we have?")
    assert q1.answerable, q1.warnings
    assert "EmpCount" in q1.detected_metrics
    assert "Actives" not in q1.detected_metrics

    # 2. Filtered count (Active)
    q2 = eng.resolve_query("How many active employees are there?")
    assert q2.answerable, q2.warnings
    assert "Actives" in q2.detected_metrics

    # 3. Separations count
    q3 = eng.resolve_query("How many employees separated in total?")
    assert q3.answerable, q3.warnings
    assert "Seps" in q3.detected_metrics

    # 4. Metric with dimensions
    q4 = eng.resolve_query("What is the average tenure in months by age group and pay type?")
    assert q4.answerable, q4.warnings
    assert "AVG Tenure Months" in q4.detected_metrics
    dims = [d.upper() for d in q4.detected_dimensions]
    assert "AGEGROUP" in dims
    assert "PAYTYPE" in dims

