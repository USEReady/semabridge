"""Multi-model dry-run jobs: generic over PBIX + Fabric, per-file isolation,
incremental per-file persistence, rerun safety, stale-job guard, and the
synchronous /dry-run multi-model guard.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import types
from datetime import timedelta

import pytest

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

from semabridge.api.services import dry_run_job_service as job_service


@pytest.fixture(autouse=True)
def _ensure_orm_tables():
    """Other test modules reset the shared in-memory SQLite engine, which
    yields an empty database; make sure every ORM table exists here."""
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(bind=db_manager.get_engine())
    yield
from semabridge.domain.exceptions import ValidationError

FABRIC_CFG = {"type": "fabric", "workspace_id": "ws-1", "identity_id": "acct-1"}
TARGET_CFG = {"type": "snowflake", "database": "DB"}


def _result_for(source_ref: str, project_id: str = "preview-x") -> dict:
    return {
        "success": True,
        "project_id": project_id,
        "model_name": f"ds-{source_ref}",
        "entity_mappings": [
            {"source_name": f"{source_ref}_COL", "source_table": "T", "entity_kind": "column"},
        ],
        "extraction_failed": False,
        "dropped_entities": [],
        "summary": {"total_fields": 1, "auto_mapped": 1, "unmapped": 0, "collisions": 0},
    }


def _patch_pipeline(monkeypatch, fake):
    import semabridge.api.controllers.mappings_controller as mc

    monkeypatch.setattr(mc, "_run_dry_run_pipeline", fake)


# ---------------------------------------------------------------------------
# Fabric jobs: one file per model, each result isolated/scoped
# ---------------------------------------------------------------------------


def test_fabric_job_with_two_models_creates_two_scoped_files(monkeypatch):
    calls = []

    async def _fake(*, project_id, request_user_id, source_config, target_config, selected_sources, auto_resolve_collisions=False):
        calls.append((tuple(selected_sources), dict(source_config)))
        assert len(selected_sources) == 1, "each worker must be scoped to exactly one model"
        return _result_for(selected_sources[0], project_id=f"preview-{selected_sources[0].lower()}")

    _patch_pipeline(monkeypatch, _fake)

    job = job_service.create_dry_run_job(
        project_id="preview",
        source_config=dict(FABRIC_CFG),
        target_config=TARGET_CFG,
        selected_sources=["Sales Model", "Finance Model"],
    )
    assert job["source_type"] == "fabric"
    assert len(job["files"]) == 2
    for f, name in zip(job["files"], ["Sales Model", "Finance Model"]):
        assert f["label"] == name
        assert f["source_ref"] == name
        assert f["pbix_path"] is None
        assert f["status"] == "pending"
        assert set(["file_id", "status", "error_message", "pbix_path", "source_ref", "label", "started_at", "completed_at"]) <= set(f)

    job_service.run_dry_run_job(job["job_id"])

    assert sorted(c[0] for c in calls) == [("Finance Model",), ("Sales Model",)]
    assert all(c[1]["type"] == "fabric" and "models" not in c[1] for c in calls)

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "success"
    for f in status["files"]:
        detail = job_service.get_dry_run_job_file_result(job["job_id"], f["file_id"])
        result = detail["result"]
        assert result["label"] == f["label"]
        assert result["source_file"] == f["label"]
        assert result["model_name"] == f"ds-{f['label']}"
        # Isolation: only this model's rows.
        assert [r["source_name"] for r in result["entity_mappings"]] == [f"{f['label']}_COL"]
        assert all(r["source_file"] == f["label"] for r in result["entity_mappings"])
        assert f["preview_project_id"] == f"preview-{f['label'].lower()}"


def test_fabric_job_validation_dedupes_and_rejects_empty_and_too_many():
    job = job_service.create_dry_run_job(
        project_id="preview",
        source_config=dict(FABRIC_CFG),
        target_config=TARGET_CFG,
        selected_sources=["Sales", "sales ", "SALES", "Finance"],
    )
    assert [f["label"] for f in job["files"]] == ["Sales", "Finance"]

    with pytest.raises(ValidationError, match="at least one Fabric semantic model"):
        job_service.create_dry_run_job(
            project_id="preview", source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=[],
        )
    with pytest.raises(ValidationError, match="Up to 10"):
        job_service.create_dry_run_job(
            project_id="preview",
            source_config=dict(FABRIC_CFG),
            target_config=TARGET_CFG,
            selected_sources=[f"Model {i}" for i in range(11)],
        )


def test_pbix_job_dedupes_case_insensitively_and_keeps_pbix_path():
    job = job_service.create_dry_run_job(
        project_id="preview",
        source_config={"type": "pbix"},
        target_config=TARGET_CFG,
        selected_sources=["C:/R/Sales.pbix", "c:/r/SALES.pbix", "C:/R/Ops.pbix"],
    )
    assert [f["label"] for f in job["files"]] == ["Sales", "Ops"]
    assert job["files"][0]["pbix_path"] == "C:/R/Sales.pbix"
    assert job["files"][0]["source_ref"] == "C:/R/Sales.pbix"


def test_snapshot_ids_by_model_returns_every_model_and_preferred_matches_scope():
    from semabridge.api.services.mapping_service import (
        _compat_preferred_snapshot_id_from_sync_result,
        _compat_snapshot_ids_by_model_from_sync_result,
    )

    sync_result = {
        "results": [
            {"model": "Sales", "summary": {"sml_snapshot_id": "snap-sales"}},
            {"model": "Finance", "summary": {"sml_snapshot_id": "snap-fin"}},
            {"model": "My Report", "summary": {"sml_snapshot_id": "snap-pbix"}},
        ]
    }
    assert _compat_snapshot_ids_by_model_from_sync_result(sync_result, ["Sales", "finance"]) == {
        "Sales": "snap-sales",
        "finance": "snap-fin",
    }
    assert _compat_preferred_snapshot_id_from_sync_result(sync_result, ["Finance"]) == "snap-fin"
    # PBIX path matches a result keyed by the file stem.
    assert _compat_preferred_snapshot_id_from_sync_result(sync_result, ["C:\\x\\My Report.pbix"]) == "snap-pbix"


def test_sync_dry_run_rejects_multiple_fabric_models_with_jobs_hint(monkeypatch):
    import semabridge.api.controllers.mappings_controller as mc

    called = {"n": 0}

    async def _fake(**kwargs):
        called["n"] += 1
        return _result_for("x")

    monkeypatch.setattr(mc, "_run_dry_run_pipeline", _fake)
    monkeypatch.setattr(mc, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(mc, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)

    req = mc.DryRunRequest(source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["A", "B"])
    resp = asyncio.run(mc.dry_run_mapping("preview", object(), req, service=None))
    assert resp.status_code == 400
    body = resp.body.decode()
    assert "dry-run-jobs" in body and '"use_dry_run_jobs":true' in body.replace(" ", "")
    assert called["n"] == 0

    # Single model (and case-insensitive duplicates of it) still goes through
    # the unchanged synchronous path.
    req_one = mc.DryRunRequest(source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["A", "a"])
    out = asyncio.run(mc.dry_run_mapping("preview", object(), req_one, service=None))
    assert out["success"] is True
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# Incremental per-file persistence
# ---------------------------------------------------------------------------


def test_fast_file_result_is_visible_while_slow_file_still_running(monkeypatch):
    release_slow = threading.Event()

    async def _fake(*, project_id, request_user_id, source_config, target_config, selected_sources):
        name = selected_sources[0]
        if name == "Slow":
            assert release_slow.wait(timeout=10)
        return _result_for(name)

    _patch_pipeline(monkeypatch, _fake)
    job = job_service.create_dry_run_job(
        project_id="preview", source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["Fast", "Slow"],
    )
    runner = threading.Thread(target=job_service.run_dry_run_job, args=(job["job_id"],))
    runner.start()
    try:
        deadline = time.time() + 10
        observed = None
        while time.time() < deadline:
            status = job_service.get_dry_run_job_status(job["job_id"])
            by_label = {f["label"]: f for f in status["files"]}
            if by_label["Fast"]["status"] == "success":
                observed = status
                break
            time.sleep(0.02)
        assert observed is not None, "fast file's result was not written before the slow file finished"
        by_label = {f["label"]: f for f in observed["files"]}
        assert by_label["Slow"]["status"] == "running"
        assert by_label["Fast"]["completed_at"] is not None
        assert observed["status"] == "running"
        fast_detail = job_service.get_dry_run_job_file_result(job["job_id"], by_label["Fast"]["file_id"])
        assert fast_detail["result"]["entity_mappings"][0]["source_name"] == "Fast_COL"
    finally:
        release_slow.set()
        runner.join(timeout=10)
    final = job_service.get_dry_run_job_status(job["job_id"])
    assert final["status"] == "success"


# ---------------------------------------------------------------------------
# Rerun race: never run a file twice concurrently
# ---------------------------------------------------------------------------


def test_rerun_while_running_is_rejected_and_second_runner_claims_nothing(monkeypatch):
    release = threading.Event()
    started = threading.Event()
    calls = []

    async def _fake(*, project_id, request_user_id, source_config, target_config, selected_sources):
        calls.append(selected_sources[0])
        started.set()
        assert release.wait(timeout=10)
        return _result_for(selected_sources[0])

    _patch_pipeline(monkeypatch, _fake)
    job = job_service.create_dry_run_job(
        project_id="preview", source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["Only"],
    )
    file_id = job["files"][0]["file_id"]
    runner = threading.Thread(target=job_service.run_dry_run_job, args=(job["job_id"],))
    runner.start()
    try:
        assert started.wait(timeout=10)
        # Retry of a running file is refused...
        with pytest.raises(ValidationError, match="already running"):
            job_service.reset_dry_run_job_file_for_rerun(job["job_id"], file_id)
        # ...and an overlapping background invocation (e.g. duplicate
        # BackgroundTask) does not pick up the running file.
        job_service.run_dry_run_job(job["job_id"])
        assert calls == ["Only"]
    finally:
        release.set()
        runner.join(timeout=10)
    assert calls == ["Only"]

    # After completion a rerun is accepted and runs exactly once more.
    job_service.reset_dry_run_job_file_for_rerun(job["job_id"], file_id)
    # Idempotent while pending.
    assert job_service.reset_dry_run_job_file_for_rerun(job["job_id"], file_id)["status"] == "pending"
    release.set()
    job_service.run_dry_run_job(job["job_id"])
    job_service.run_dry_run_job(job["job_id"])
    assert calls == ["Only", "Only"]
    assert job_service.get_dry_run_job_status(job["job_id"])["status"] == "success"


# ---------------------------------------------------------------------------
# Stale-job guard
# ---------------------------------------------------------------------------


def test_stale_running_file_is_failed_on_status_read():
    from semabridge.repository.orm.dry_run_job_models import DryRunJobFile
    from semabridge.repository.orm.session_factory import db_manager

    job = job_service.create_dry_run_job(
        project_id="preview", source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["Stuck", "Queued"],
    )
    old = job_service._utcnow() - job_service.DRY_RUN_STALE_AFTER - timedelta(minutes=1)
    with db_manager.get_session() as session:
        stuck = session.get(DryRunJobFile, job["files"][0]["file_id"])
        stuck.status = "running"
        stuck.started_at = old
        queued = session.get(DryRunJobFile, job["files"][1]["file_id"])
        queued.queued_at = old

    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["status"] == "failed"
    for f in status["files"]:
        assert f["status"] == "failed"
        assert "timed out" in f["error_message"]


def test_fresh_running_file_is_not_failed():
    from semabridge.repository.orm.dry_run_job_models import DryRunJobFile
    from semabridge.repository.orm.session_factory import db_manager

    job = job_service.create_dry_run_job(
        project_id="preview", source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["Live"],
    )
    with db_manager.get_session() as session:
        f = session.get(DryRunJobFile, job["files"][0]["file_id"])
        f.status = "running"
        f.started_at = job_service._utcnow()
    status = job_service.get_dry_run_job_status(job["job_id"])
    assert status["files"][0]["status"] == "running"
