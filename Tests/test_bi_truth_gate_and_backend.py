"""Unit tests for BI Truth Deploy Gate and Backend (Reporting vs Blocking modes).

Verifies:
1. Default is 'report': failing critical measures report findings and heavy warnings without blocking.
2. 'block' mode can be explicitly configured per project to halt deployments.
3. In block mode, an evaluation error refuses the deploy (does not fail open).
4. In report mode, an evaluation error logs a warning and the deploy continues.
5. Drift pre-check respects project gate mode (default report, continues deploy).
6. Critical measure toggle and tolerance override.
7. Scheduling lifecycle (create, list, delete).
"""

from __future__ import annotations

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from semabridge.ai.bi_truth_service import (
    create_truth_schedule,
    delete_truth_schedule,
    evaluate_project_deploy_gate,
    list_truth_schedules,
    set_truth_case_critical_status,
)
from semabridge.api.services.run_service import _perform_project_run_impl


def test_gate_not_gated_when_no_cases_or_no_critical(monkeypatch):
    """A project with no truth cases or no critical measures is not gated."""
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_cases", lambda pid: [])
    v1 = evaluate_project_deploy_gate("proj-empty")
    assert v1.passed is True
    assert v1.blocks_deploy is False
    assert v1.status in ("not_gated", "not_configured")

    normal_case = {
        "id": "c1",
        "project_id": "proj-normal",
        "metric_name": "REVENUE",
        "is_critical": False,
        "status": "approved",
    }
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_cases", lambda pid: [normal_case])
    v2 = evaluate_project_deploy_gate("proj-normal")
    assert v2.passed is True
    assert v2.blocks_deploy is False
    assert v2.status in ("not_gated", "not_configured")


def test_gate_reports_by_default_without_blocking(monkeypatch):
    """In default report mode, failing critical measure reports findings but should_block is False."""
    critical_case = {
        "id": "c-crit",
        "project_id": "proj-crit",
        "metric_name": "TOTAL_ARR",
        "is_critical": True,
        "status": "approved",
        "tolerance": 0.001,
    }
    recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    failing_run = {
        "id": "trun-recent",
        "project_id": "proj-crit",
        "ran_at": recent_time,
        "results": [
            {
                "case_id": "c-crit",
                "metric_name": "TOTAL_ARR",
                "window_name": "FY2024",
                "passed": False,
                "expected_value": 1000000.0,
                "actual_value": 980000.0,
                "error": "Value mismatch: variance 2.0000% > tolerance 0.1000%",
                "comparison_details": {
                    "variance_pct": 2.0,
                    "tolerance": 0.001,
                },
            }
        ],
    }
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_cases", lambda pid: [critical_case])
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_runs", lambda pid: [failing_run])

    # Default call without explicit mode -> report mode
    verdict = evaluate_project_deploy_gate("proj-crit")
    assert verdict.passed is False
    assert verdict.blocks_deploy is False
    assert str(getattr(verdict.mode, "value", verdict.mode)) == "report"
    assert verdict.status in ("warning", "failed")
    assert "TOTAL_ARR" in str(verdict.reason) or len(verdict.findings) == 1
    assert len(verdict.findings) == 1


def test_gate_blocks_only_when_explicitly_configured_block(monkeypatch):
    """When gate_mode is 'block', failing critical measure sets blocks_deploy=True."""
    critical_case = {
        "id": "c-crit",
        "project_id": "proj-crit",
        "metric_name": "TOTAL_ARR",
        "is_critical": True,
        "status": "approved",
    }
    recent_time = datetime.datetime.now(datetime.timezone.utc).isoformat()
    failing_run = {
        "id": "trun-recent",
        "project_id": "proj-crit",
        "ran_at": recent_time,
        "results": [
            {
                "case_id": "c-crit",
                "metric_name": "TOTAL_ARR",
                "passed": False,
                "error": "Outside tolerance",
            }
        ],
    }
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_cases", lambda pid: [critical_case])
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_runs", lambda pid: [failing_run])

    verdict = evaluate_project_deploy_gate("proj-crit", gate_mode="block")
    assert verdict.passed is False
    assert verdict.blocks_deploy is True
    assert str(getattr(verdict.mode, "value", verdict.mode)) == "block"
    assert verdict.status in ("blocked", "failed")


@pytest.mark.asyncio
async def test_deploy_run_proceeds_with_failing_gate_in_report_mode(monkeypatch):
    """In report mode, deploy continues and logs heavy warning; run status is NOT blocked."""
    run = {
        "id": "run-test-01",
        "project_id": "proj-report",
        "schedule": "Manual",
        "logs": [],
        "summary": {},
    }
    project_cfg = "name: proj-report\n"  # No bi_truth_gate_mode -> defaults to report

    critical_case = {
        "id": "c1",
        "project_id": "proj-report",
        "metric_name": "KPI_VAL",
        "is_critical": True,
        "status": "approved",
    }
    failing_run = {
        "id": "trun-1",
        "project_id": "proj-report",
        "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "results": [{"case_id": "c1", "metric_name": "KPI_VAL", "passed": False, "error": "Mismatch 5%"}],
    }

    async def _mock_sync_success(payload):
        return {"status": "success", "summary": {}, "results": []}

    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_cases", lambda pid: [critical_case])
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_runs", lambda pid: [failing_run])
    monkeypatch.setattr("semabridge.api.services.core_domain_service.sync_models", _mock_sync_success)
    monkeypatch.setattr("semabridge.api.services.run_service._persist_run", lambda r: None)
    monkeypatch.setattr("semabridge.api.services.run_service._RUN_SLOTS.try_acquire", lambda: MagicMock())
    monkeypatch.setattr("semabridge.api.services.run_service._build_run_logs", lambda res: [])
    monkeypatch.setattr("semabridge.api.services.run_service._build_stage_states", lambda res: [])

    result = await _perform_project_run_impl(run, project_cfg, started=100.0)

    # Deploy was NOT blocked; it carried on
    assert result.get("status") != "blocked"
    assert "bi_truth_gate" in result["summary"]
    assert result["summary"]["bi_truth_gate"]["passed"] is False
    assert result["summary"]["bi_truth_gate"]["mode"] == "report"
    # Heavy warning is present in logs
    assert any("HEAVY WARNING [BI TRUTH GATE - REPORT MODE]" in log for log in result["logs"])


@pytest.mark.asyncio
async def test_deploy_run_blocks_when_project_config_is_block(monkeypatch):
    """When project configuration sets bi_truth_gate_mode: block, deploy is halted."""
    run = {
        "id": "run-test-02",
        "project_id": "proj-block",
        "schedule": "Manual",
        "logs": [],
        "summary": {},
    }
    project_cfg = "name: proj-block\nbi_truth_gate_mode: block\n"

    critical_case = {
        "id": "c1",
        "project_id": "proj-block",
        "metric_name": "KPI_VAL",
        "is_critical": True,
        "status": "approved",
    }
    failing_run = {
        "id": "trun-1",
        "project_id": "proj-block",
        "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "results": [{"case_id": "c1", "metric_name": "KPI_VAL", "passed": False, "error": "Mismatch 5%"}],
    }

    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_cases", lambda pid: [critical_case])
    monkeypatch.setattr("semabridge.ai.bi_truth_service.list_truth_runs", lambda pid: [failing_run])
    monkeypatch.setattr("semabridge.api.services.run_service._persist_run", lambda r: None)

    result = await _perform_project_run_impl(run, project_cfg, started=100.0)

    # In block mode, run status IS blocked
    assert result.get("status") == "blocked"
    assert "DEPLOYMENT BLOCKED by BI Truth Gate" in result["logs"][-2]


@pytest.mark.asyncio
async def test_deploy_run_proceeds_when_gate_raises_in_report_mode(monkeypatch):
    """In report mode, if gate evaluation encounters an unexpected error, deploy carries on."""
    run = {
        "id": "run-test-03",
        "project_id": "proj-err",
        "schedule": "Manual",
        "logs": [],
        "summary": {},
    }
    project_cfg = "name: proj-err\n"

    def _broken_gate(*args, **kwargs):
        raise RuntimeError("Transient store failure")

    async def _mock_sync_success(payload):
        return {"status": "success", "summary": {}, "results": []}

    monkeypatch.setattr("semabridge.ai.bi_truth_service.evaluate_project_deploy_gate", _broken_gate)
    monkeypatch.setattr("semabridge.api.services.core_domain_service.sync_models", _mock_sync_success)
    monkeypatch.setattr("semabridge.api.services.run_service._persist_run", lambda r: None)
    monkeypatch.setattr("semabridge.api.services.run_service._RUN_SLOTS.try_acquire", lambda: MagicMock())
    monkeypatch.setattr("semabridge.api.services.run_service._build_run_logs", lambda res: [])
    monkeypatch.setattr("semabridge.api.services.run_service._build_stage_states", lambda res: [])

    result = await _perform_project_run_impl(run, project_cfg, started=100.0)
    assert result.get("status") != "failed"
    assert result.get("status") != "blocked"
    assert any("BI Truth Gate evaluation encountered non-fatal error in report mode" in log for log in result["logs"])


@pytest.mark.asyncio
async def test_deploy_run_refuses_when_gate_raises_in_block_mode(monkeypatch):
    """In block mode, if gate evaluation encounters an error, it refuses deploy (does not fail open)."""
    run = {
        "id": "run-test-04",
        "project_id": "proj-err-block",
        "schedule": "Manual",
        "logs": [],
        "summary": {},
    }
    project_cfg = "name: proj-err-block\nbi_truth_gate_mode: block\n"

    def _broken_gate(*args, **kwargs):
        raise RuntimeError("DB connection lost")

    monkeypatch.setattr("semabridge.ai.bi_truth_service.evaluate_project_deploy_gate", _broken_gate)
    monkeypatch.setattr("semabridge.api.services.run_service._persist_run", lambda r: None)

    result = await _perform_project_run_impl(run, project_cfg, started=100.0)
    assert result.get("status") == "failed"
    assert "BI Truth Gate evaluation failed in block mode" in result.get("message", "")


@pytest.mark.asyncio
async def test_drift_precheck_proceeds_in_report_mode(monkeypatch):
    """Scheduled run with drift in default report mode logs heavy warning and does NOT halt deploy."""
    run = {
        "id": "run-test-05",
        "project_id": "proj-drift-report",
        "schedule": "Scheduled Run",
        "logs": [],
        "summary": {},
    }
    project_cfg = "name: proj-drift-report\n"  # drift_gate_mode defaults to report

    async def _mock_sync_success(payload):
        return {"status": "success", "summary": {}, "results": []}

    monkeypatch.setattr(
        "semabridge.governance.drift_service.run_drift_check",
        lambda project_id, owner_id=None: {"drift_detected": 2},
    )
    monkeypatch.setattr(
        "semabridge.governance.drift_service.list_drift_events",
        lambda project_id, status: [{"id": "ev1", "metric": "M1"}, {"id": "ev2", "metric": "M2"}],
    )
    monkeypatch.setattr("semabridge.ai.bi_truth_service.evaluate_project_deploy_gate", lambda *args, **kwargs: MagicMock(passed=True, blocks_deploy=False, mode="report", status="ready", reason="OK", findings=[]))
    monkeypatch.setattr("semabridge.api.services.core_domain_service.sync_models", _mock_sync_success)
    monkeypatch.setattr("semabridge.api.services.run_service._persist_run", lambda r: None)
    monkeypatch.setattr("semabridge.api.services.run_service._RUN_SLOTS.try_acquire", lambda: MagicMock())
    monkeypatch.setattr("semabridge.api.services.run_service._build_run_logs", lambda res: [])
    monkeypatch.setattr("semabridge.api.services.run_service._build_stage_states", lambda res: [])

    result = await _perform_project_run_impl(run, project_cfg, started=100.0)

    # Direct upsert was NOT paused
    assert result.get("status") != "warning"
    assert result["summary"]["drift_detected"] is True
    assert result["summary"]["upsert_executed"] is True
    assert any("HEAVY WARNING Drift detected on scheduled run: 2 drifted metric(s). Mode: REPORT" in log for log in result["logs"])


def test_critical_measure_toggle_and_tolerance(monkeypatch):
    """set_truth_case_critical_status updates is_critical flag and custom tolerance."""
    case = {
        "id": "c-crit-toggle",
        "project_id": "proj-t",
        "is_critical": False,
        "tolerance": None,
    }
    saved_state = {}
    monkeypatch.setattr("semabridge.ai.bi_truth_service._load_suite", lambda: {"cases": [case], "runs": []})
    monkeypatch.setattr("semabridge.ai.bi_truth_service._save_suite", lambda data: saved_state.update(data))

    updated = set_truth_case_critical_status("c-crit-toggle", is_critical=True, tolerance=0.005)
    assert updated["is_critical"] is True
    assert updated["tolerance"] == 0.005


def test_truth_schedule_lifecycle(monkeypatch):
    """create_truth_schedule, list_truth_schedules, and delete_truth_schedule operate cleanly."""
    state = {"cases": [], "runs": [], "schedules": []}
    monkeypatch.setattr("semabridge.ai.bi_truth_service._load_suite", lambda: state)
    monkeypatch.setattr("semabridge.ai.bi_truth_service._save_suite", lambda d: state.update(d))
    monkeypatch.setattr("semabridge.governance.drift_service._get_scheduler", lambda: None)

    sched = create_truth_schedule(project_id="proj-sched", cron_expression="0 2 * * *")
    assert sched["project_id"] == "proj-sched"
    assert sched["cron_expression"] == "0 2 * * *"

    listed = list_truth_schedules(project_id="proj-sched")
    assert len(listed) == 1
    assert listed[0]["id"] == sched["id"]

    del_ok = delete_truth_schedule(sched["id"])
    assert del_ok is True
    assert len(list_truth_schedules(project_id="proj-sched")) == 0
