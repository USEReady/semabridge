"""Unit tests for BI Truth Verified Pair Injection (Criterion 2).

Verifies:
1. Only qualifying cases (approved/certified, benchmark captured, passed in latest run) are selected.
2. Cases failing each specific condition are skipped with explicit descriptive reasons.
3. Dry run mode reports preview of what would land without executing warehouse writes.
4. Unsupported platform refuses with the spike reason.
5. Partial failures report what landed and what failed.
6. Audit record contains what, where, when, who, and counts.
7. Credentials come strictly from the project's stored warehouse config.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from semabridge.ai.bi_truth_injection import (
    build_injection_pairs,
    filter_qualifying_cases,
    perform_injection,
)
from semabridge.governance.drift.adapters.base import PlatformAdapter


class FakeRecordingAdapter(PlatformAdapter):
    platform_name: str = "TEST_PLATFORM"

    def __init__(self, supported: bool = True, partial_after: int = -1):
        self.supported = supported
        self.partial_after = partial_after
        self.calls: list[dict] = []

    def list_views(self, project_id: str, scope=None):
        return ["V_TEST"]

    def fetch_definition_text(self, view_name: str, connection_config=None):
        return "CREATE VIEW V_TEST AS SELECT 1"

    def parse_definition(self, raw_text: str, view_name: str, source="live", descriptions=None):
        raise NotImplementedError

    def inject_verified_queries(self, pairs, view_name, connection_config=None, dry_run=False):
        self.calls.append({
            "pairs": pairs,
            "view_name": view_name,
            "connection_config": connection_config,
            "dry_run": dry_run,
        })
        if not self.supported:
            return {
                "supported": False,
                "injected_count": 0,
                "target": None,
                "details": [],
                "reason": "Platform 'TEST_PLATFORM' does not support automated verified query injection.",
                "dry_run": dry_run,
            }

        count = len(pairs)
        if self.partial_after >= 0:
            count = min(count, self.partial_after)

        target = f"@TEST_STAGE/model.yaml"
        return {
            "supported": True,
            "injected_count": count,
            "target": target,
            "details": pairs[:count],
            "reason": f"Injected {count} pairs into {target}",
            "dry_run": dry_run,
        }


@pytest.fixture
def sample_cases():
    return [
        {
            "id": "case-qual-1",
            "metric_name": "HEADCOUNT",
            "question": "What is current headcount?",
            "expected_sql": "SELECT COUNT(*) FROM EMP",
            "expected_value": 1500,
            "status": "approved",
        },
        {
            "id": "case-qual-2",
            "metric_name": "REVENUE",
            "question": "Total quarterly revenue?",
            "expected_sql": "SELECT SUM(AMT) FROM SALES",
            "expected_value": 450000.0,
            "status": "certified",
        },
        {
            "id": "case-draft",
            "metric_name": "AVG_SALARY",
            "question": "Average employee salary?",
            "expected_sql": "SELECT AVG(SAL) FROM EMP",
            "expected_value": 75000.0,
            "status": "draft",
        },
        {
            "id": "case-no-benchmark",
            "metric_name": "ATTRITION",
            "question": "Annual attrition rate?",
            "expected_sql": "SELECT RATE FROM ATTR",
            "expected_value": None,
            "status": "approved",
        },
        {
            "id": "case-failed-run",
            "metric_name": "BAD_HIRES",
            "question": "Bad hires count?",
            "expected_sql": "SELECT COUNT(*) FROM BAD",
            "expected_value": 12,
            "status": "approved",
        },
        {
            "id": "case-not-in-run",
            "metric_name": "NEW_MEASURE",
            "question": "New measure total?",
            "expected_sql": "SELECT SUM(X) FROM T",
            "expected_value": 100,
            "status": "approved",
        },
    ]


@pytest.fixture
def sample_latest_run():
    return {
        "id": "trun-sample",
        "project_id": "proj-sample",
        "results": [
            {"case_id": "case-qual-1", "passed": True, "verdict": "PASS"},
            {"case_id": "case-qual-2", "passed": True, "verdict": "PASS"},
            {"case_id": "case-draft", "passed": True, "verdict": "PASS"},
            {"case_id": "case-failed-run", "passed": False, "verdict": "FAIL"},
        ],
    }


def test_filter_qualifying_cases_isolates_only_valid_pairs(sample_cases, sample_latest_run):
    qualifying, skipped = filter_qualifying_cases(sample_cases, sample_latest_run)

    qual_ids = [c["id"] for c in qualifying]
    assert qual_ids == ["case-qual-1", "case-qual-2"]

    skipped_reasons = {s["case_id"]: s["reason"] for s in skipped}
    assert "draft" in skipped_reasons["case-draft"]
    assert "null" in skipped_reasons["case-no-benchmark"]
    assert "did not pass" in skipped_reasons["case-failed-run"]
    assert "not evaluated" in skipped_reasons["case-not-in-run"]


def test_build_injection_pairs_formats_proper_records(sample_cases, sample_latest_run):
    qualifying, _ = filter_qualifying_cases(sample_cases, sample_latest_run)
    pairs = build_injection_pairs(qualifying, user="data_steward")

    assert len(pairs) == 2
    assert pairs[0]["case_id"] == "case-qual-1"
    assert pairs[0]["name"] == "HEADCOUNT"
    assert pairs[0]["question"] == "What is current headcount?"
    assert pairs[0]["sql"] == "SELECT COUNT(*) FROM EMP"
    assert pairs[0]["verified_by"] == "data_steward"
    assert isinstance(pairs[0]["verified_at"], int)


def test_dry_run_reports_without_writing_to_warehouse(sample_cases, sample_latest_run):
    adapter = FakeRecordingAdapter()
    res = perform_injection(
        adapter=adapter,
        project_id="proj-test",
        view_name="V_TEST",
        connection_config={"account": "acc1", "warehouse": "wh1"},
        cases=sample_cases,
        latest_run=sample_latest_run,
        dry_run=True,
        user="test_steward",
    )

    assert res["status"] == "dry_run"
    assert res["dry_run"] is True
    assert res["injected_count"] == 2
    assert len(res["injected"]) == 2
    assert len(res["skipped"]) == 4
    assert res["audit_record"]["dry_run"] is True

    # Check adapter received dry_run flag
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["dry_run"] is True


def test_unsupported_platform_refuses_with_adapter_reason(sample_cases, sample_latest_run):
    adapter = FakeRecordingAdapter(supported=False)
    res = perform_injection(
        adapter=adapter,
        project_id="proj-test",
        view_name="V_TEST",
        connection_config={"account": "acc1"},
        cases=sample_cases,
        latest_run=sample_latest_run,
        dry_run=False,
    )

    assert res["status"] == "refused"
    assert res["injected_count"] == 0
    assert len(res["failed"]) == 2
    assert "does not support" in res["message"]


def test_partial_failure_reports_what_landed_and_what_failed(sample_cases, sample_latest_run):
    adapter = FakeRecordingAdapter(supported=True, partial_after=1)
    res = perform_injection(
        adapter=adapter,
        project_id="proj-test",
        view_name="V_TEST",
        connection_config={"account": "acc1"},
        cases=sample_cases,
        latest_run=sample_latest_run,
        dry_run=False,
    )

    assert res["status"] == "partial"
    assert res["injected_count"] == 1
    assert len(res["injected"]) == 1
    assert len(res["failed"]) == 1
    assert res["injected"][0]["case_id"] == "case-qual-1"
    assert res["failed"][0]["case_id"] == "case-qual-2"


def test_audit_record_names_what_where_when_and_who(sample_cases, sample_latest_run):
    adapter = FakeRecordingAdapter()
    res = perform_injection(
        adapter=adapter,
        project_id="proj-sample-audit",
        view_name="V_AUDIT",
        connection_config={"account": "acc1"},
        cases=sample_cases,
        latest_run=sample_latest_run,
        dry_run=False,
        user="auditor_jane",
    )

    record = res["audit_record"]
    assert record["project_id"] == "proj-sample-audit"
    assert record["view_name"] == "V_AUDIT"
    assert record["target"] == "@TEST_STAGE/model.yaml"
    assert record["injected_by"] == "auditor_jane"
    assert record["injected_count"] == 2
    assert record["skipped_count"] == 4
    assert record["failed_count"] == 0
    assert record["injected_at"]


def test_credentials_come_strictly_from_project_config():
    """Confirms stored connection config is passed directly to the adapter without env fallback."""
    adapter = FakeRecordingAdapter()
    custom_cfg = {
        "account": "secure-corp-acc",
        "user": "service_bot",
        "warehouse": "PROD_ANALYTICS_WH",
        "database": "FINANCE_DB",
        "schema_name": "REPORTING",
    }

    qual_cases = [
        {
            "id": "c1",
            "metric_name": "M1",
            "question": "Q1?",
            "expected_sql": "SELECT 1",
            "expected_value": 1,
            "status": "approved",
        }
    ]
    latest_run = {"results": [{"case_id": "c1", "passed": True, "verdict": "PASS"}]}

    perform_injection(
        adapter=adapter,
        project_id="proj-strict",
        view_name="V_FIN",
        connection_config=custom_cfg,
        cases=qual_cases,
        latest_run=latest_run,
        dry_run=False,
    )

    assert len(adapter.calls) == 1
    passed_cfg = adapter.calls[0]["connection_config"]
    assert passed_cfg["account"] == "secure-corp-acc"
    assert passed_cfg["warehouse"] == "PROD_ANALYTICS_WH"
    assert passed_cfg["database"] == "FINANCE_DB"


def test_windowed_case_with_window_benchmark_qualifies_for_injection():
    """A case whose case-level expected_value is None still qualifies if a window has its own benchmark and passed."""
    cases = [
        {
            "id": "c_win",
            "metric_name": "AVG_TENURE_DAYS",
            "question": "What is AVG_TENURE_DAYS?",
            "expected_sql": "SELECT 1",
            "expected_value": None,
            "status": "approved",
            "windows": [
                {"window_key": "y2014", "window_label": "2014", "filter_clause": "COL_DATE.YEAR = 2014", "expected_value": 1195.0}
            ],
        }
    ]
    latest_run = {
        "results": [
            {"case_id": "c_win", "window_name": "2014", "passed": True, "verdict": "PASS"},
        ]
    }
    qualifying, skipped = filter_qualifying_cases(cases, latest_run)
    assert [c["id"] for c in qualifying] == ["c_win"]
    assert skipped == []
