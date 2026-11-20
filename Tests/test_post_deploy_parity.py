from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from semabridge.ai.bi_truth_service import run_truth_suite, add_manual_truth_case, clear_truth_cases
from semabridge.reconciliation.row_count_parity import verify_row_count_parity


def test_row_count_parity_verification():
    cursor = MagicMock()

    # Table 1 matches (100 rows), Table 2 mismatches (expected 200, got 150)
    def fetch_mock():
        query = cursor.execute.call_args[0][0]
        if "TABLE_A" in query:
            return (100,)
        if "TABLE_B" in query:
            return (150,)
        return (0,)

    cursor.fetchone.side_effect = fetch_mock

    # Test 1: Mismatch detected
    source_counts = {"TABLE_A": 100, "TABLE_B": 200}
    res = verify_row_count_parity(source_counts, cursor, "TEST_DB", "TEST_SCHEMA")
    assert res["status"] == "FAILED"
    assert res["total_tables"] == 2
    assert res["matched_tables"] == 1
    assert res["mismatched_tables"] == 1
    assert res["mismatches"][0]["source_table"] == "TABLE_B"
    assert res["mismatches"][0]["delta"] == -50

    # Test 2: All match
    source_counts_matching = {"TABLE_A": 100}
    res2 = verify_row_count_parity(source_counts_matching, cursor, "TEST_DB", "TEST_SCHEMA")
    assert res2["status"] == "PASSED"
    assert res2["matched_tables"] == 1
    assert res2["mismatched_tables"] == 0


def test_bi_truth_live_dax_vs_sql_parity():
    test_proj = "test_parity_truth_proj"
    clear_truth_cases(test_proj)

    try:
        # Add a case with both expected_sql and expected_dax
        add_manual_truth_case(
            project_id=test_proj,
            question="What is total revenue?",
            metric_name="Total Revenue",
            expected_sql="SELECT SUM(revenue) AS TOTAL_REVENUE FROM sales",
            expected_dax="EVALUATE ROW(\"Total Revenue\", [Total Revenue])",
            status="approved",
        )

        # 1. Matching values -> passes
        mock_dax = lambda q: [{"Total Revenue": 50000.0}]
        mock_sql = lambda q: [{"TOTAL_REVENUE": 50000.0}]

        run1 = run_truth_suite(
            project_id=test_proj,
            live_parity=True,
            dax_executor=mock_dax,
            sql_executor=mock_sql,
        )
        assert run1["passed"] == 1
        assert run1["results"][0]["passed"] is True

        # 2. SQL generated but values MISMATCH -> fails (never passes just because SQL was generated!)
        mock_sql_diff = lambda q: [{"TOTAL_REVENUE": 42000.0}]
        run2 = run_truth_suite(
            project_id=test_proj,
            live_parity=True,
            dax_executor=mock_dax,
            sql_executor=mock_sql_diff,
        )
        assert run2["passed"] == 0
        assert run2["results"][0]["passed"] is False
        assert "Parity mismatch" in run2["results"][0]["error"]
    finally:
        clear_truth_cases(test_proj)

