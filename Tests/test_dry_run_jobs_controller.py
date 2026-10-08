"""Part B: dry_run_jobs_controller.py wiring tests — the thin HTTP layer over
dry_run_job_service.py (already thoroughly tested at the service level in
test_dry_run_job_service.py). These confirm the controller endpoints wire
BackgroundTasks correctly and expose the right response shapes, not the
underlying job logic again.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest
from fastapi import BackgroundTasks

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_dry_run_jobs_controller.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

FILE_A = "C:/Reports/Sales Report.pbix"
FILE_B = "C:/Reports/Marketing Analysis.pbix"


def _success_result(filename: str) -> dict:
    return {
        "success": True,
        "entity_mappings": [{"source_name": "AMOUNT", "source_file": filename}],
        "extraction_failed": False,
        "dropped_entities": [],
        "summary": {"total_fields": 1, "auto_mapped": 1, "unmapped": 0, "collisions": 0, "predicted_failures": 0, "extraction_failed": False},
    }


def _patch_pipeline(monkeypatch, results_by_path):
    import semabridge.api.controllers.mappings_controller as mc

    async def _fake(*, project_id, request_user_id, source_config, target_config, selected_sources):
        outcome = results_by_path[selected_sources[0]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(mc, "_run_dry_run_pipeline", _fake)


def test_create_endpoint_returns_running_status_and_dispatches_background_work(monkeypatch):
    import semabridge.api.controllers.dry_run_jobs_controller as ctrl

    monkeypatch.setattr(ctrl, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(ctrl, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)
    _patch_pipeline(monkeypatch, {
        FILE_A: _success_result("Sales Report"),
        FILE_B: _success_result("Marketing Analysis"),
    })

    request = ctrl.DryRunJobRequest(
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[FILE_A, FILE_B],
    )
    background_tasks = BackgroundTasks()

    response = asyncio.run(
        ctrl.create_dry_run_job_endpoint("preview", object(), request, background_tasks)
    )
    assert response["status"] == "running"
    assert len(response["files"]) == 2
    job_id = response["job_id"]

    # Simulate Starlette running the scheduled background task after the
    # response is sent.
    asyncio.run(background_tasks())

    status = asyncio.run(ctrl.get_dry_run_job_status_endpoint("preview", job_id, object()))
    assert status["status"] == "success"
    assert all(f["status"] == "success" for f in status["files"])


def test_get_file_endpoint_returns_full_result_for_drill_in(monkeypatch):
    import semabridge.api.controllers.dry_run_jobs_controller as ctrl

    monkeypatch.setattr(ctrl, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(ctrl, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)
    _patch_pipeline(monkeypatch, {FILE_A: _success_result("Sales Report")})

    request = ctrl.DryRunJobRequest(
        source_config={"type": "pbix"}, target_config={"type": "snowflake"}, selected_sources=[FILE_A],
    )
    background_tasks = BackgroundTasks()
    response = asyncio.run(ctrl.create_dry_run_job_endpoint("preview", object(), request, background_tasks))
    asyncio.run(background_tasks())

    file_id = response["files"][0]["file_id"]
    detail = asyncio.run(ctrl.get_dry_run_job_file_endpoint("preview", response["job_id"], file_id, object()))
    assert detail["status"] == "success"
    assert detail["result"]["entity_mappings"][0]["source_file"] == "Sales Report"


def test_rerun_endpoint_resets_and_reschedules_only_the_named_file(monkeypatch):
    import semabridge.api.controllers.dry_run_jobs_controller as ctrl

    monkeypatch.setattr(ctrl, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(ctrl, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)
    _patch_pipeline(monkeypatch, {
        FILE_A: _success_result("Sales Report"),
        FILE_B: RuntimeError("first attempt failed"),
    })

    request = ctrl.DryRunJobRequest(
        source_config={"type": "pbix"}, target_config={"type": "snowflake"}, selected_sources=[FILE_A, FILE_B],
    )
    background_tasks = BackgroundTasks()
    response = asyncio.run(ctrl.create_dry_run_job_endpoint("preview", object(), request, background_tasks))
    asyncio.run(background_tasks())

    status = asyncio.run(ctrl.get_dry_run_job_status_endpoint("preview", response["job_id"], object()))
    assert status["status"] == "partial"
    failed_file_id = next(f["file_id"] for f in status["files"] if f["status"] == "failed")

    _patch_pipeline(monkeypatch, {FILE_B: _success_result("Marketing Analysis")})
    rerun_background_tasks = BackgroundTasks()
    rerun_response = asyncio.run(
        ctrl.rerun_dry_run_job_file_endpoint("preview", response["job_id"], failed_file_id, object(), rerun_background_tasks)
    )
    assert rerun_response["status"] == "pending"
    asyncio.run(rerun_background_tasks())

    final_status = asyncio.run(ctrl.get_dry_run_job_status_endpoint("preview", response["job_id"], object()))
    assert final_status["status"] == "success"
    assert all(f["status"] == "success" for f in final_status["files"])
