"""Tests for per-model run results persistence, model log isolation, and endpoints."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import sys
import types
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_run_model_results_and_logs.sqlite")
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

from semabridge.core.run_model_logs import (
    model_log_scope,
    read_model_log,
    model_log_path,
    RUN_LOGS_ROOT,
)
from semabridge.api.services.run_model_results import (
    persist_run_model_results,
    load_run_model_results,
    run_belongs_to_project,
    run_model_manifest,
    load_model_summary,
    model_summary_path,
    model_report_path,
)
from semabridge.api.services import run_service
from semabridge.api.main import app


def test_concurrent_models_log_lines_never_mix(tmp_path, monkeypatch):
    """Run two jobs in concurrent threads with different model scopes, verify logs do not mix."""
    monkeypatch.setattr("semabridge.core.run_model_logs.RUN_LOGS_ROOT", tmp_path / "runs")
    logger = logging.getLogger("semabridge.test_logger")

    project_id = "test_project_concurrency"
    run_id = "run_123"
    model_a = "ModelAlpha"
    model_b = "ModelBeta"

    def emit_logs(model: str, messages: list[str]):
        with model_log_scope(project_id, run_id, model):
            for msg in messages:
                logger.info("Message for %s: %s", model, msg)

    messages_a = [f"AlphaMsg_{i}" for i in range(25)]
    messages_b = [f"BetaMsg_{i}" for i in range(25)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f_a = executor.submit(emit_logs, model_a, messages_a)
        f_b = executor.submit(emit_logs, model_b, messages_b)
        f_a.result()
        f_b.result()

    log_a = read_model_log(project_id, run_id, model_a)
    log_b = read_model_log(project_id, run_id, model_b)

    assert log_a["exists"] is True
    assert log_b["exists"] is True

    lines_a = log_a["lines"]
    lines_b = log_b["lines"]

    assert len(lines_a) == 25
    assert len(lines_b) == 25

    # Check that each file contains only its own model tag and messages
    for line in lines_a:
        assert f"[model={model_a}]" in line
        assert "AlphaMsg_" in line
        assert "BetaMsg_" not in line

    for line in lines_b:
        assert f"[model={model_b}]" in line
        assert "BetaMsg_" in line
        assert "AlphaMsg_" not in line


def test_persist_simulated_restart_hydrate(tmp_path, monkeypatch):
    """Persisting run results and then clearing in-memory run store retains full model results on hydration."""
    monkeypatch.setattr("semabridge.api.services.run_report_service.REPORTS_ROOT", tmp_path / "reports")
    monkeypatch.setattr("semabridge.core.run_model_logs.RUN_LOGS_ROOT", tmp_path / "runs")

    project_id = "test_proj_persist"
    run_id = "run_persist_001"

    sample_run = {
        "id": run_id,
        "run_id": run_id,
        "project_id": project_id,
        "project_name": "Test Project",
        "status": "success",
        "results": [
            {
                "model": "ModelA",
                "status": "success",
                "view_name": "VIEW_MODEL_A",
                "physical_namespace": "M1",
                "duration_ms": 1250,
                "dropped_entities": [{"name": "ColA", "reason": "unsupported type"}],
                "reconciliation": {"is_clean": True, "deployed_live_count": 5},
                "summary": {"steps_completed": []},
            },
            {
                "model": "ModelB",
                "status": "failed",
                "view_name": "VIEW_MODEL_B",
                "physical_namespace": "M2",
                "duration_ms": 850,
                "dropped_entities": [],
                "reconciliation": None,
                "summary": {"errors": [{"message": "Syntax error in query"}]},
            },
        ],
    }

    # Persist
    count = persist_run_model_results(sample_run, "")
    assert count == 2

    # Verify rows loaded from DB
    loaded = load_run_model_results(project_id, run_id)
    assert len(loaded) == 2
    assert loaded[0]["model"] == "ModelA"
    assert loaded[0]["status"] == "success"
    assert loaded[0]["view_name"] == "VIEW_MODEL_A"
    assert len(loaded[0]["dropped_entities"]) == 1
    assert loaded[0]["reconciliation"]["is_clean"] is True

    assert loaded[1]["model"] == "ModelB"
    assert loaded[1]["status"] == "failed"
    assert loaded[1]["error"] == "Syntax error in query"

    # Test _hydrate_model_results() on a stripped run record (simulated restart)
    stripped_run = {
        "id": run_id,
        "run_id": run_id,
        "project_id": project_id,
        "status": "success",
    }
    hydrated = run_service._hydrate_model_results(stripped_run)
    assert "results" in hydrated
    assert len(hydrated["results"]) == 2
    assert hydrated["results"][0]["model"] == "ModelA"


def test_manifest_logs_and_summary_fallback(tmp_path, monkeypatch):
    """Manifest lists models, artifact presence, and summary fallback loads persisted JSON."""
    monkeypatch.setattr("semabridge.api.services.run_report_service.REPORTS_ROOT", tmp_path / "reports")
    monkeypatch.setattr("semabridge.core.run_model_logs.RUN_LOGS_ROOT", tmp_path / "runs")

    project_id = "test_proj_manifest"
    run_id = "run_manifest_001"

    sample_run = {
        "id": run_id,
        "run_id": run_id,
        "project_id": project_id,
        "status": "success",
        "results": [
            {
                "model": "ModelAlpha",
                "status": "success",
                "view_name": "VIEW_ALPHA",
                "duration_ms": 1000,
                "summary": {},
            }
        ],
    }

    persist_run_model_results(sample_run, "")

    # Create dummy report and log file for ModelAlpha
    rep_path = model_report_path(project_id, run_id, "ModelAlpha")
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    rep_path.write_text("# Report for Alpha", encoding="utf-8")

    l_path = model_log_path(project_id, run_id, "ModelAlpha")
    l_path.parent.mkdir(parents=True, exist_ok=True)
    l_path.write_text("[run=run_manifest_001] [model=ModelAlpha] Step 1 OK\n", encoding="utf-8")

    manifest = run_model_manifest(project_id, run_id)
    assert len(manifest) == 1
    assert manifest[0]["model"] == "ModelAlpha"
    assert manifest[0]["has_report"] is True
    assert manifest[0]["has_summary"] is True
    assert manifest[0]["has_log"] is True

    # Summary fallback
    summary_data = load_model_summary(project_id, run_id, "ModelAlpha")
    assert summary_data is not None

    # Unknown model returns None
    assert load_model_summary(project_id, run_id, "NonExistent") is None


@pytest.mark.asyncio
async def test_run_endpoints_404_for_wrong_project(tmp_path, monkeypatch):
    """Run endpoints validate run_belongs_to_project and return 404 for another project's run."""
    monkeypatch.setattr("semabridge.api.services.run_report_service.REPORTS_ROOT", tmp_path / "reports")
    monkeypatch.setattr("semabridge.core.run_model_logs.RUN_LOGS_ROOT", tmp_path / "runs")

    proj_a = "project_owner"
    proj_b = "project_other"
    run_id = "run_secret_123"

    sample_run = {
        "id": run_id,
        "run_id": run_id,
        "project_id": proj_a,
        "status": "success",
        "results": [
            {
                "model": "ModelSec",
                "status": "success",
                "view_name": "VIEW_SEC",
                "summary": {},
            }
        ],
    }
    persist_run_model_results(sample_run, "")

    # Mock projects in memory
    from semabridge.api.services import project_shared
    project_shared._compat_ensure_loaded()
    project_shared._compat_projects[proj_a] = {"id": proj_a, "name": "Owner Proj"}
    project_shared._compat_projects[proj_b] = {"id": proj_b, "name": "Other Proj"}

    # Test run_belongs_to_project directly
    assert run_belongs_to_project(proj_a, run_id) is True
    assert run_belongs_to_project(proj_b, run_id) is False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Request for valid project
        res_valid = await client.get(f"/api/projects/{proj_a}/runs/{run_id}/models")
        assert res_valid.status_code == 200

        # Request for wrong project -> 404
        res_models = await client.get(f"/api/projects/{proj_b}/runs/{run_id}/models")
        assert res_models.status_code == 404

        res_logs = await client.get(f"/api/projects/{proj_b}/runs/{run_id}/models/ModelSec/logs")
        assert res_logs.status_code == 404

        res_rep = await client.get(f"/api/projects/{proj_b}/runs/{run_id}/report")
        assert res_rep.status_code == 404

        res_rep_m = await client.get(f"/api/projects/{proj_b}/runs/{run_id}/report/ModelSec")
        assert res_rep_m.status_code == 404

        res_sum = await client.get(f"/api/projects/{proj_b}/runs/{run_id}/report-summary")
        assert res_sum.status_code == 404

        res_sum_m = await client.get(f"/api/projects/{proj_b}/runs/{run_id}/report-summary/ModelSec")
        assert res_sum_m.status_code == 404
