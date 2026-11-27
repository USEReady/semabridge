"""Pure unit tests for the BI Truth comparison engine and benchmark acceptance.

Zero database dependencies, zero ORM imports, zero database setup.
"""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest
from semabridge.ai.bi_truth_comparison import compare_truth_results
from semabridge.ai.bi_truth_service import accept_truth_case_benchmark


def test_null_equals_null_passes():
    """Null against null must pass without error."""
    passed, err, details = compare_truth_results(None, None)
    assert passed is True
    assert err is None
    assert details["rule_applied"] in ("null_equals_null", "null_match")


def test_empty_rows_equal_empty_rows_passes():
    """Empty list against empty list must pass."""
    passed, err, details = compare_truth_results([], [])
    assert passed is True
    assert err is None
    assert details["rule_applied"] in ("empty_rows_match", "null_match")


def test_null_against_zero_fails_naming_null_side():
    """Null against zero must fail and explicitly name which side was null."""
    # Actual is 0, expected is None
    p1, err1, d1 = compare_truth_results([{"VAL": 0}], None)
    assert p1 is False
    assert "expected" in err1.lower() and "null" in err1.lower()

    # Actual is None, expected is 0
    p2, err2, d2 = compare_truth_results(None, 0)
    assert p2 is False
    assert "actual" in err2.lower() and "null" in err2.lower()


def test_relative_tolerance_pass_and_fail():
    """Values within relative tolerance pass; values outside fail."""
    # Expected 1000.0, actual 1000.5 (delta 0.5, tol 0.001 -> max_delta 1.0) -> PASS
    p1, err1, d1 = compare_truth_results([{"TOTAL": 1000.5}], 1000.0, tolerance=1e-3)
    assert p1 is True
    assert err1 is None
    assert d1["rule_applied"] in ("relative_variance", "tolerance")

    # Expected 1000.0, actual 1002.5 (delta 2.5 > max_delta 1.0) -> FAIL
    p2, err2, d2 = compare_truth_results([{"TOTAL": 1002.5}], 1000.0, tolerance=1e-3)
    assert p2 is False
    assert "variance" in err2.lower() or "tolerance" in err2.lower()
    assert d2["rule_applied"] in ("relative_variance", "tolerance")


def test_near_zero_absolute_floor():
    """Small values near zero use absolute floor in measure's own units."""
    # Under per-measure floor 0.0001:
    # delta 0.00003 <= 0.0001 -> PASS
    p1, err1, d1 = compare_truth_results(0.00008, 0.00005, absolute_floor=1e-4)
    assert p1 is True
    assert d1["rule_applied"] in ("absolute_floor_near_zero", "absolute_floor")

    # delta 0.0005 > floor 0.0001 -> FAIL
    p2, err2, d2 = compare_truth_results(0.00055, 0.00005, absolute_floor=1e-4)
    assert p2 is False
    assert "floor" in err2.lower() or "delta" in err2.lower()
    assert d2["rule_applied"] in ("absolute_floor_near_zero", "absolute_floor")


def test_row_count_mismatch_fails_even_when_scalar_agrees():
    """Matching scalar value with differing row count must fail."""
    actual = [{"VAL": 42}]
    passed, err, details = compare_truth_results(
        actual=actual,
        expected=42,
        expected_row_count=5,
    )
    assert passed is False
    assert "row count" in err.lower() or "row" in err.lower()
    assert "5" in err and "1" in err
    assert details["verdict"] == "ROWS_DIFFER"


def test_benchmark_acceptance_action(monkeypatch):
    """accept_truth_case_benchmark updates expected value, evaluates timestamp, and records audit note."""
    stored_case = {
        "id": "case-bench-101",
        "project_id": "proj-benchmarks",
        "metric_name": "HEADCOUNT",
        "question": "What is total headcount?",
        "expected_value": 100,
        "expected_row_count": 1,
        "expected_evaluated_at": "2026-01-01T00:00:00Z",
        "status": "approved",
    }
    mock_suite = {
        "cases": [stored_case],
        "runs": [],
        "schedules": [],
    }

    from semabridge.ai import bi_truth_service
    monkeypatch.setattr(bi_truth_service, "_load_suite", lambda: mock_suite)
    saved = {}
    monkeypatch.setattr(bi_truth_service, "_save_suite", lambda s: saved.update(s))

    updated = accept_truth_case_benchmark(
        project_id="proj-benchmarks",
        case_id="case-bench-101",
        new_expected_value=120,
        user_id="lead-architect",
        note="Q4 baseline re-anchor",
        new_row_count=1,
    )

    assert updated["id"] == "case-bench-101"
    assert updated["expected_value"] == 120
    assert updated["expected_row_count"] == 1
    assert "lead-architect" in updated["evaluation_note"]
    assert "Q4 baseline re-anchor" in updated["evaluation_note"]
    assert "expected_evaluated_at" in updated
    assert saved["cases"][0]["expected_value"] == 120


def test_non_read_only_sql_is_refused_by_truth_suite(monkeypatch):
    """Refuse unsafe SQL and distinguish it from a failure to evaluate the guard."""
    from semabridge.ai import bi_truth_service
    from semabridge.ai.bi_truth_service import run_truth_suite

    mock_case = {
        "id": "case-malicious-drop",
        "project_id": "proj-safety-test",
        "question": "What is total headcount?",
        "status": "approved",
        "is_critical": False,
        "expected_value": 100,
        "expected_sql": "DROP TABLE employees;",
        "expected_row_count": 1,
        "windows": [],
    }

    mock_suite = {
        "cases": [mock_case],
        "runs": [],
        "schedules": [],
    }

    monkeypatch.setattr(bi_truth_service, "_load_suite", lambda: mock_suite)
    monkeypatch.setattr(bi_truth_service, "_save_suite", lambda s: None)
    from semabridge.ai import project_connection
    monkeypatch.setattr(project_connection, "resolve_project_warehouse_config", lambda p, w, owner_id=None: MagicMock())

    from semabridge.connectors import snowflake_connection
    monkeypatch.setattr(snowflake_connection, "get_snowflake_connect_kwargs", lambda cfg: {"account": "acc", "user": "usr"})

    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    import snowflake.connector
    monkeypatch.setattr(snowflake.connector, "connect", lambda **kwargs: mock_conn)

    mock_plan = MagicMock()
    mock_plan.answerable = True
    mock_plan.generated_sql = "DROP TABLE employees;"

    mock_engine = MagicMock()
    mock_engine.resolve_query.return_value = mock_plan

    from semabridge.ai import nl_query_engine
    monkeypatch.setattr(nl_query_engine, "NLQueryEngine", lambda *args, **kwargs: mock_engine)

    run_record = run_truth_suite(project_id="proj-safety-test")

    assert run_record["total"] == 1
    assert run_record["passed"] == 0
    assert len(run_record["results"]) == 1
    res = run_record["results"][0]

    assert res["verdict"] == "NOT_COMPARABLE"
    assert res["comparable"] is False
    assert "Unsafe SQL rejected: Read-only SQL guard blocked forbidden operation 'DROP'" in res["error"]
    assert res["generated_sql"] == ""
    # Verify execute was never called with DROP TABLE
    for call_args in mock_cursor.execute.call_args_list:
        assert "DROP" not in str(call_args).upper()

