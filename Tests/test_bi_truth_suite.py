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
