"""Tests for demo-mode masking of multi-model runs and endpoints.
Neutral naming used throughout.
"""
from __future__ import annotations

import os
import sys
import types
import pytest

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

from semabridge.core.run_helpers import _DEMO_SUCCESS_LOG_LINE, mask_run_for_display
from semabridge.api.services.run_service import (
    get_run_models_compat,
    get_run_model_logs_compat,
    get_run_model_report_data_compat,
    _compat_project_runs,
)


def test_mask_run_for_display_leaves_successful_run_unchanged():
    run = {
        "id": "run-ok-1",
        "demo_mode": True,
        "status": "success",
        "message": "Original success message",
        "model_results": [
            {"model": "ModelA", "status": "success", "console_lines": ["Line 1"]}
        ],
    }
    masked = mask_run_for_display(run)
    assert masked["status"] == "success"
    assert masked["message"] == "Original success message"
    assert masked["model_results"][0]["console_lines"] == ["Line 1"]


def test_mask_run_for_display_masks_failed_multi_model_run():
    run = {
        "id": "run-fail-1",
        "demo_mode": True,
        "status": "failed",
        "error": "Fatal syntax error in Snowflake view",
        "message": "View generation failed",
        "logs": ["ERROR: Snowflake query crashed"],
        "reconciliation": {"status": "failed", "error": "Table missing"},
        "model_results": [
            {
                "model": "ModelAlpha",
                "status": "failed",
                "error_message": "Column mismatch",
                "console_lines": ["ERROR: Column missing"],
                "console_text": "ERROR: Column missing\nTraceback...",
                "reconciliation": {"status": "failed", "errors": ["Diff count: 5"]},
                "summary": {"status": "FAILED", "errors": [{"message": "Crash"}]},
            },
            {
                "model": "ModelBeta",
                "status": "success",
                "console_lines": ["OK: Deployed view"],
                "summary": {"status": "SUCCESS", "errors": []},
            },
        ],
    }
    masked = mask_run_for_display(run)
    assert masked["status"] == "success"
    assert "error" not in masked
    assert masked["logs"] == [_DEMO_SUCCESS_LOG_LINE]
    assert masked["reconciliation"]["status"] == "success"
    assert "error" not in masked["reconciliation"]

    # Model Alpha was failed -> now masked
    alpha = next(m for m in masked["model_results"] if m["model"] == "ModelAlpha")
    assert alpha["status"] == "success"
    assert alpha["error_message"] is None
    assert alpha["console_lines"] == [_DEMO_SUCCESS_LOG_LINE]
    assert alpha["console_text"] == _DEMO_SUCCESS_LOG_LINE
    assert alpha["reconciliation"]["status"] == "success"
    assert alpha["summary"]["status"] == "SUCCESS"
    assert alpha["summary"]["errors"] == []


@pytest.mark.asyncio
async def test_per_model_endpoints_masked_in_demo_mode():
    project_id = "proj_mask_test"
    run_id = "run-masked-99"
    failed_run = {
        "id": run_id,
        "run_id": run_id,
        "project_id": project_id,
        "demo_mode": True,
        "status": "failed",
        "results": [
            {
                "model": "ModelOne",
                "status": "failed",
                "error": "Crash during deploy",
                "summary": {"status": "FAILED", "errors": [{"message": "Crash"}]},
            }
        ],
        "model_results": [
            {
                "model": "ModelOne",
                "status": "failed",
                "error_message": "Crash during deploy",
            }
        ],
    }
    _compat_project_runs[project_id] = [failed_run]

    # Manifest endpoint
    manifest = await get_run_models_compat(project_id, run_id)
    assert len(manifest) >= 1
    m1 = next(m for m in manifest if m["model"] == "ModelOne")
    assert m1["status"] == "success"
    assert m1.get("error") is None

    # Logs endpoint
    logs = await get_run_model_logs_compat(project_id, run_id, "ModelOne", offset=0)
    assert len(logs["lines"]) == 1
    assert _DEMO_SUCCESS_LOG_LINE in logs["lines"][0]

    # Report data endpoint
    report_data = await get_run_model_report_data_compat(project_id, run_id, "ModelOne")
    if report_data:
        assert report_data.get("status") == "success"
        assert report_data.get("crashed") is False
