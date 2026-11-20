import pytest
from fastapi.testclient import TestClient

from semabridge.ai.bi_truth_service import (
    add_manual_truth_case,
    import_truth_cases_from_content,
    list_truth_cases,
    clear_truth_cases,
    set_batch_truth_case_status,
)
from semabridge.api.main import app


@pytest.fixture(autouse=True)
def clean_test_cases():
    test_proj = "test_manual_import_proj"
    clear_truth_cases(project_id=test_proj)
    yield
    clear_truth_cases(project_id=test_proj)


def test_add_manual_truth_case():
    test_proj = "test_manual_import_proj"
    case = add_manual_truth_case(
        project_id=test_proj,
        question="What is the gross revenue for 2024?",
        metric_name="Gross Revenue",
        expected_sql="SELECT SUM(revenue) FROM sales WHERE year = 2024;",
        expected_dax="CALCULATE([Gross Revenue], Year = 2024)",
        status="approved",
    )
    assert case["id"].startswith("truth-")
    assert case["question"] == "What is the gross revenue for 2024?"
    assert case["metric_name"] == "Gross Revenue"
    assert case["status"] == "approved"

    cases = list_truth_cases(project_id=test_proj)
    assert len(cases) == 1
    assert cases[0]["id"] == case["id"]


def test_import_truth_cases_csv():
    test_proj = "test_manual_import_proj"
    csv_content = """question,expected_sql,metric_name
"What is total sales amount?","SELECT SUM(sales) FROM sales_fact;","Total Sales"
"What is the total order count?","SELECT COUNT(order_id) FROM orders_fact;","Order Count"
"""
    imported = import_truth_cases_from_content(
        content=csv_content,
        filename="test.csv",
        project_id=test_proj,
        status="approved",
    )
    assert len(imported) == 2
    assert imported[0]["question"] == "What is total sales amount?"
    assert imported[0]["metric_name"] == "Total Sales"
    assert imported[1]["question"] == "What is the total order count?"


def test_import_truth_cases_json():
    test_proj = "test_manual_import_proj"
    json_content = """[
        {"question": "How many customers are active?", "expected_sql": "SELECT COUNT(*) FROM customers WHERE active = 1;", "metric_name": "Active Customers"},
        {"question": "What is the average discount?", "expected_sql": "SELECT AVG(discount) FROM orders;"}
    ]"""
    imported = import_truth_cases_from_content(
        content=json_content,
        filename="suite.json",
        project_id=test_proj,
        status="approved",
    )
    assert len(imported) == 2
    assert imported[0]["question"] == "How many customers are active?"
    assert imported[0]["metric_name"] == "Active Customers"
    assert imported[1]["question"] == "What is the average discount?"


def test_import_truth_cases_qa_text():
    test_proj = "test_manual_import_proj"
    text_content = """
    Q: What was the total product margin?
    A: SELECT SUM(margin) FROM sales_fact;

    Q: What was the total refund amount?
    A: SELECT SUM(refund) FROM returns_fact;
    """
    imported = import_truth_cases_from_content(
        content=text_content,
        filename="notes.txt",
        project_id=test_proj,
        status="approved",
    )
    assert len(imported) == 2
    assert "total product margin" in imported[0]["question"].lower()
    assert "SELECT SUM(margin)" in imported[0]["expected_sql"]


def test_batch_status_update():
    test_proj = "test_manual_import_proj"
    c1 = add_manual_truth_case(project_id=test_proj, question="Q1", status="draft")
    c2 = add_manual_truth_case(project_id=test_proj, question="Q2", status="draft")

    count = set_batch_truth_case_status(status="approved", project_id=test_proj)
    assert count == 2

    cases = list_truth_cases(project_id=test_proj)
    assert all(c["status"] == "approved" for c in cases)


def test_api_manual_and_import_endpoints():
    client = TestClient(app)
    test_proj = "test_manual_import_proj"

    # 1. POST manual case
    res1 = client.post(
        "/api/bi-truth/cases",
        json={
            "project_id": test_proj,
            "question": "What is total margin?",
            "metric_name": "Total Margin",
            "expected_sql": "SELECT SUM(margin) FROM sales;",
            "status": "approved",
        },
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["success"] is True
    assert data1["case"]["question"] == "What is total margin?"

    # 2. POST import text
    csv_body = {
        "project_id": test_proj,
        "content": "question,expected_sql\nWhat is total tax?,SELECT SUM(tax) FROM sales;",
        "filename": "tax.csv",
        "status": "approved",
    }
    res2 = client.post("/api/bi-truth/cases/import-text", json=csv_body)
    assert res2.status_code == 200
    assert res2.json()["added_count"] == 1

    # 3. GET cases
    res3 = client.get(f"/api/bi-truth/cases?project_id={test_proj}")
    assert res3.status_code == 200
    cases = res3.json()["cases"]
    assert len(cases) == 2


def test_generate_truth_cases_fallback_and_prerequisite_error(monkeypatch, tmp_path):
    import pytest
    from semabridge.ai.bi_truth_service import generate_truth_cases

    # Isolate suite storage for test
    monkeypatch.setattr("semabridge.ai.bi_truth_service._suite_path", lambda: tmp_path / "bi_truth_suite.json")

    # 1. When project has no certs, vqs, or domains, but has reconciliation metrics:
    monkeypatch.setattr(
        "semabridge.api.routers.reconciliation_router.get_project_reconciliation_metrics",
        lambda pid: {
            "project_id": pid,
            "count": 1,
            "metrics": [
                {
                    "name": "Headcount",
                    "logical_name": "HEADCOUNT",
                    "sql_expression": "COUNT(DISTINCT EMP_ID)",
                    "dax_expression": "DISTINCTCOUNT(Emp[EmpID])",
                    "domain": "HR_Domain",
                    "dimensions": ["Department.DeptName"],
                }
            ],
        },
    )
    cases = generate_truth_cases(project_id="proj-hr-sample", limit=10)
    assert len(cases) >= 1
    qs = [c["question"] for c in cases]
    assert any("Headcount" in q for q in qs)

    # 2. When project has no metrics at all -> raises ValueError with clear prerequisite message
    monkeypatch.setattr(
        "semabridge.api.routers.reconciliation_router.get_project_reconciliation_metrics",
        lambda pid: {"project_id": pid, "count": 0, "metrics": []},
    )
    with pytest.raises(ValueError, match="Prerequisite missing: project 'proj-empty' has no"):
        generate_truth_cases(project_id="proj-empty")


def test_ddl_synonyms_and_comment_stripped_from_metric():
    """Requirement 2: Ensure DDL WITH SYNONYMS and COMMENT clauses are stripped from sql_expression."""
    from semabridge.ontology.models import DomainMetric

    raw_expr = "SUM(t.col) WITH SYNONYMS = ('total amount', 'sum amt') COMMENT = 'Calculates total amount in usd'"
    m = DomainMetric(
        name="test_clean_metric",
        domain="TestDomain",
        sql_expression=raw_expr,
    )
    assert "WITH SYNONYMS" not in m.sql_expression
    assert "COMMENT" not in m.sql_expression
    assert m.sql_expression == "SUM(t.col)"


def test_certification_requires_matching_power_bi_reference(monkeypatch, tmp_path):
    """Requirement 3: A case becomes certified only when it matches a Power BI reference value AND is approved."""
    import pytest
    from semabridge.ai.bi_truth_service import (
        add_manual_truth_case,
        set_truth_case_status,
        _suite_path,
    )

    monkeypatch.setattr("semabridge.ai.bi_truth_service._suite_path", lambda: tmp_path / "bi_truth_suite.json")

    case_no_ref = add_manual_truth_case(
        question="What is test total?",
        expected_sql="SELECT * FROM SEMANTIC_VIEW(V METRICS M)",
        metric_name="M",
    )
    # Transitioning to approved is allowed
    appr = set_truth_case_status(case_no_ref["id"], "approved")
    assert appr["status"] == "approved"

    # Transitioning to certified without reference value is rejected
    with pytest.raises(ValueError, match="certification requires matching a Power BI reference value"):
        set_truth_case_status(case_no_ref["id"], "certified")

    # With reference value matching expected value, certification succeeds
    from semabridge.ai.bi_truth_service import _load_suite, _save_suite
    data = _load_suite()
    for c in data.get("cases", []):
        if c["id"] == case_no_ref["id"]:
            c["expected_value"] = "100.0"
            c["power_bi_reference_value"] = "100.0"
    _save_suite(data)
    cert = set_truth_case_status(case_no_ref["id"], "certified")
    assert cert["status"] == "certified"


def test_generate_truth_cases_runnable_semantic_view_sql_and_skips(monkeypatch, tmp_path):
    """Requirements 1, 5, 6, 7: Expected SQL uses SEMANTIC_VIEW, ratios use overall, unanswerables skipped."""
    from semabridge.ai.bi_truth_service import generate_truth_cases
    from semabridge.ontology.models import DomainOntology, DomainConcept, DomainMetric, DomainAttribute

    monkeypatch.setattr("semabridge.ai.bi_truth_service._suite_path", lambda: tmp_path / "bi_truth_suite.json")

    dom = DomainOntology(
        domain="TestModelDomain",
        id="dom_test",
        owner="dev",
        source_project_id="proj-unit-test",
        semantic_view_name="DB.SCHEMA.TEST_SV",
        concepts=[
            DomainConcept(
                name="FactTable",
                logical_name="FACT_TABLE",
                domain="TestModelDomain",
                attributes=[
                    DomainAttribute(name="AttrCode", is_primary_key=False, data_type="VARCHAR"),
                ],
            )
        ],
        metrics=[
            DomainMetric(
                name="REGULAR_METRIC",
                label="Regular Metric",
                domain="TestModelDomain",
                concept="FactTable",
                sql_expression="SUM(FactTable.col)",
            ),
            DomainMetric(
                name="RATIO_METRIC_PCT",
                label="Ratio Pct",
                domain="TestModelDomain",
                concept="FactTable",
                sql_expression="SUM(a) / NULLIF(SUM(b), 0)",
            ),
            DomainMetric(
                name="WINDOW_METRIC",
                label="Window Metric",
                domain="TestModelDomain",
                concept="FactTable",
                sql_expression="SUM(col) OVER (PARTITION BY EXCLUDING Period)",
            ),
            DomainMetric(
                name="PRIVATE_HELPER",
                label="Private Helper",
                domain="TestModelDomain",
                concept="FactTable",
                sql_expression="SUM(c)",
                is_hidden=True,
                deploy_reason="Marked PRIVATE in deployed semantic view",
            ),
            DomainMetric(
                name="NULL_PLACEHOLDER",
                label="Null Metric",
                domain="TestModelDomain",
                concept="FactTable",
                sql_expression="CAST(NULL AS DOUBLE)",
            ),
            DomainMetric(
                name="SUM_OF_COLUMN",
                label="Sum Of Column",
                domain="TestModelDomain",
                concept="FactTable",
                sql_expression="SUM(col)",
            ),
        ],
    )

    from semabridge.ontology.store import ontology_store
    monkeypatch.setattr(ontology_store, "domains_for", lambda pid: [dom])

    res = generate_truth_cases(project_id="proj-unit-test", limit=20)
    assert len(res) >= 2

    # Verify unanswerable skipped metrics
    skipped_names = [s["metric_name"] for s in res.skipped]
    assert "PRIVATE_HELPER" in skipped_names
    assert "NULL_PLACEHOLDER" in skipped_names
    assert "SUM_OF_COLUMN" in skipped_names

    # Check SQL format
    for c in res:
        assert c["expected_sql"].startswith("SELECT * FROM SEMANTIC_VIEW(DB.SCHEMA.TEST_SV METRICS")

    # Check ratio wording
    ratio_cases = [c for c in res if c["metric_name"] == "RATIO_METRIC_PCT"]
    for rc in ratio_cases:
        assert "total" not in rc["question"].lower() or "overall" in rc["question"].lower()

    # Window metric must not have a "total" question
    window_cases = [c for c in res if c["metric_name"] == "WINDOW_METRIC"]
    for wc in window_cases:
        assert not wc["question"].startswith("What is the total")

