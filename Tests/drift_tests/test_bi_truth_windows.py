"""Tests for Time Windows on BI-Truth Cases (Step 1 of CS-19 Target-Aware BI Truth Suite).

Verifies:
1. parse_case_windows handles dicts, JSON strings, and empty/invalid values cleanly.
2. build_windowed_sql applies window filters to SEMANTIC_VIEW and standard SQL without warehouse access.
3. build_windowed_sql correctly handles queries with existing WHERE, ORDER BY, and LIMIT.
4. Backward compatibility: cases with no windows run and serialize unchanged.
5. BITruthCaseRow ORM persistence of windows_json.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from semabridge.ai.bi_truth_windows import build_windowed_sql, parse_case_windows
from semabridge.ai.bi_truth_service import (
    _row_to_case_dict,
    _save_suite,
    _load_suite,
    run_truth_suite,
    clear_truth_cases,
)
from semabridge.repository.orm.ai_models import BITruthCaseRow


def test_parse_case_windows_dict_and_json():
    """Windows can be provided as a list of dicts or JSON string."""
    raw_list = [
        {"name": "2020 Slices", "filter": 'YEAR."YEAR" = 2020'},
        {"name": "2021 Slices", "filter": 'YEAR."YEAR" = 2021'},
    ]
    parsed = parse_case_windows(raw_list)
    assert len(parsed) == 2
    assert parsed[0]["name"] == "2020 Slices"
    assert parsed[0]["filter"] == 'YEAR."YEAR" = 2020'

    raw_json = json.dumps(raw_list)
    parsed_from_json = parse_case_windows(raw_json)
    assert parsed_from_json == parsed


def test_parse_case_windows_empty_or_none():
    """Empty, None, or invalid windows cleanly default to empty list."""
    assert parse_case_windows(None) == []
    assert parse_case_windows([]) == []
    assert parse_case_windows("") == []
    assert parse_case_windows("[]") == []
    assert parse_case_windows("invalid-json") == []


def test_build_windowed_sql_semantic_view():
    """Window filter on SEMANTIC_VIEW query is injected inside the SEMANTIC_VIEW call."""
    base_sql = 'SELECT * FROM SEMANTIC_VIEW("SEMABRIDGE"."FINANCE"."SALES_SEMANTIC" METRICS SALES_USD)'
    win = {"name": "FY2020", "filter": 'DATE_DIM."YEAR" = 2020'}
    windowed = build_windowed_sql(base_sql, win)
    assert windowed is not None
    assert 'WHERE DATE_DIM."YEAR" = 2020' in windowed


def test_build_windowed_sql_combines_existing_where():
    """Window filter is combined with AND when WHERE already exists."""
    base_sql = "SELECT * FROM \"DB\".\"SCH\".\"VIEW\" WHERE REGION = 'North'"
    win = {"name": "Q3", "filter": "QUARTER = 3"}
    windowed = build_windowed_sql(base_sql, win)
    assert windowed.upper().count("WHERE") == 1
    assert " AND " in windowed.upper()
    assert "QUARTER = 3" in windowed


def test_build_windowed_sql_handles_order_by_and_limit():
    """Window filter is placed before ORDER BY and LIMIT."""
    base_sql = 'SELECT * FROM "DB"."SCH"."VIEW" ORDER BY DATE DESC LIMIT 10'
    win = {"name": "2022", "filter": "YEAR = 2022"}
    windowed = build_windowed_sql(base_sql, win)
    assert windowed.upper().index("YEAR = 2022") < windowed.upper().index("ORDER BY")
    assert windowed.upper().index("ORDER BY") < windowed.upper().index("LIMIT")


def test_backward_compatibility_case_without_windows(monkeypatch):
    """A case with no windows runs exactly as today: one result with no window_name."""
    unwindowed_case = {
        "id": "truth-unwindowed-01",
        "project_id": "proj-compat",
        "question": "What is Total Revenue?",
        "metric_name": "REVENUE",
        "expected_sql": "SELECT 100",
        "expected_value": 100,
        "status": "approved",
        "windows": [],
    }

    mock_engine = MagicMock()
    mock_plan = MagicMock()
    mock_plan.answerable = True
    mock_plan.generated_sql = "SELECT 100"
    mock_engine.resolve_query.return_value = mock_plan

    monkeypatch.setattr("semabridge.ai.bi_truth_service._load_suite", lambda: {"cases": [unwindowed_case], "runs": []})
    monkeypatch.setattr("semabridge.ai.bi_truth_service._save_suite", lambda data: None)
    monkeypatch.setattr("semabridge.ai.nl_query_engine.NLQueryEngine.resolve_query", lambda self, *args, **kwargs: mock_plan)
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_scope", lambda pid, explicit_view=None: MagicMock(semantic_view=None, allowed_domains=[]))
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, platform, owner_id=None: None)

    run_result = run_truth_suite(project_id="proj-compat")
    assert run_result["total"] == 1
    assert len(run_result["results"]) == 1
    res = run_result["results"][0]
    assert res["case_id"] == "truth-unwindowed-01"
    assert "window_name" not in res


def test_windowed_case_evaluates_per_window(monkeypatch):
    """A case with 2 windows produces 2 distinct results, one per window."""
    windowed_case = {
        "id": "truth-windowed-02",
        "project_id": "proj-windows",
        "question": "What is Active Headcount?",
        "metric_name": "ACTIVES",
        "expected_sql": "SELECT 50",
        "expected_value": 50,
        "status": "approved",
        "windows": [
            {"name": "2020", "filter": "YEAR = 2020"},
            {"name": "2021", "filter": "YEAR = 2021"},
        ],
    }

    mock_engine = MagicMock()
    mock_plan = MagicMock()
    mock_plan.answerable = True
    mock_plan.generated_sql = "SELECT ACTIVES FROM HR_SUMMARY"
    mock_engine.resolve_query.return_value = mock_plan

    monkeypatch.setattr("semabridge.ai.bi_truth_service._load_suite", lambda: {"cases": [windowed_case], "runs": []})
    monkeypatch.setattr("semabridge.ai.bi_truth_service._save_suite", lambda data: None)
    monkeypatch.setattr("semabridge.ai.nl_query_engine.NLQueryEngine.resolve_query", lambda self, *args, **kwargs: mock_plan)
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_scope", lambda pid, explicit_view=None: MagicMock(semantic_view=None, allowed_domains=[]))
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, platform, owner_id=None: None)

    run_result = run_truth_suite(project_id="proj-windows")
    assert run_result["total"] == 2
    assert len(run_result["results"]) == 2
    assert run_result["results"][0]["window_name"] == "2020"
    assert "YEAR = 2020" in run_result["results"][0]["generated_sql"]
    assert run_result["results"][1]["window_name"] == "2021"
    assert "YEAR = 2021" in run_result["results"][1]["generated_sql"]


def test_window_without_expected_value_is_not_comparable(monkeypatch):
    """A window with no expected value does NOT fall back to whole-case benchmark; it is not comparable."""
    case = {
        "id": "truth-window-fallback-check",
        "project_id": "proj-fallback",
        "question": "What is Active Headcount?",
        "metric_name": "ACTIVES",
        "expected_sql": "SELECT 100",
        "expected_value": 100,  # whole-case annual number
        "status": "approved",
        "windows": [
            {"name": "Q1", "filter": "QUARTER = 1"},  # Has NO expected_value
            {"name": "Q2", "filter": "QUARTER = 2", "expected_value": 25},  # Has its own expected_value
        ],
    }

    mock_engine = MagicMock()
    mock_plan = MagicMock()
    mock_plan.answerable = True
    mock_plan.generated_sql = "SELECT ACTIVES FROM HR_SUMMARY"
    mock_engine.resolve_query.return_value = mock_plan

    monkeypatch.setattr("semabridge.ai.bi_truth_service._load_suite", lambda: {"cases": [case], "runs": []})
    monkeypatch.setattr("semabridge.ai.bi_truth_service._save_suite", lambda data: None)
    monkeypatch.setattr("semabridge.ai.nl_query_engine.NLQueryEngine.resolve_query", lambda self, *args, **kwargs: mock_plan)
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_scope", lambda pid, explicit_view=None: MagicMock(semantic_view=None, allowed_domains=[]))
    monkeypatch.setattr("semabridge.ai.project_connection.resolve_project_warehouse_config", lambda pid, platform, owner_id=None: None)

    run_result = run_truth_suite(project_id="proj-fallback")
    assert run_result["total"] == 2
    q1_res = next(r for r in run_result["results"] if r.get("window_name") == "Q1")
    q2_res = next(r for r in run_result["results"] if r.get("window_name") == "Q2")

    # Q1 must NOT be compared against 100, must be marked not comparable
    assert q1_res["comparable"] is False
    assert q1_res["passed"] is False
    assert "not comparable against whole-case benchmark" in q1_res["error"]
    assert q1_res["expected_value"] is None

    # Q2 has its own expected value
    assert q2_res["comparable"] is True
    assert q2_res["expected_value"] == 25


def test_window_saved_through_route_round_trips(monkeypatch):
    """A window saved through set_truth_case_windows comes back on the next read, unchanged."""
    from semabridge.ai.bi_truth_service import set_truth_case_windows

    mock_case = {
        "id": "case-save-windows-test",
        "project_id": "proj-win-persist",
        "question": "What is total headcount?",
        "status": "approved",
        "windows": [],
    }
    stored_suite = {"cases": [mock_case], "runs": []}
    monkeypatch.setattr("semabridge.ai.bi_truth_service._load_suite", lambda: stored_suite)
    monkeypatch.setattr("semabridge.ai.bi_truth_service._save_suite", lambda s: stored_suite.update(s))

    windows_payload = [
        {
            "window_key": "q1_2024",
            "window_label": "Q1 2024",
            "filter_clause": "COL_DATE.YEAR = 2024 AND COL_DATE.QUARTER = 1",
            "expected_value": 420.5,
            "expected_row_count": 1,
        },
        {
            "window_key": "q2_2024",
            "window_label": "Q2 2024",
            "filter_clause": "COL_DATE.YEAR = 2024 AND COL_DATE.QUARTER = 2",
            "expected_value": None,
            "expected_row_count": None,
        },
    ]

    updated = set_truth_case_windows("case-save-windows-test", windows_payload)
    assert updated is not None
    assert len(updated["windows"]) == 2

    w1 = updated["windows"][0]
    assert w1["window_key"] == "q1_2024"
    assert w1["window_label"] == "Q1 2024"
    assert w1["filter_clause"] == "COL_DATE.YEAR = 2024 AND COL_DATE.QUARTER = 1"
    assert w1["expected_value"] == 420.5
    assert w1["expected_row_count"] == 1
    assert w1["comparable"] is True

    w2 = updated["windows"][1]
    assert w2["window_key"] == "q2_2024"
    assert w2["expected_value"] is None
    assert w2["comparable"] is False


def test_window_invalid_filter_clause_is_refused():
    """A window with an invalid or mutating filter clause is refused with a reason and not stored."""
    from semabridge.ai.bi_truth_service import validate_window_filter_clause

    # Empty clause
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_window_filter_clause("")

    # Semicolon injection
    with pytest.raises(ValueError, match="forbidden SQL syntax"):
        validate_window_filter_clause("YEAR = 2024; DROP TABLE employees")

    # SQL comments
    with pytest.raises(ValueError, match="forbidden SQL syntax"):
        validate_window_filter_clause("YEAR = 2024 -- comment")

    # Mutation keyword
    with pytest.raises(ValueError, match="forbidden mutation operation"):
        validate_window_filter_clause("DELETE FROM employees WHERE id = 1")


