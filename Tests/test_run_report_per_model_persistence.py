"""Regression tests for per-model run reports surviving a server restart.

Report state (which model has its own report, and where the file lives) has
never been persisted in the ORM -- the `runs` table has no report columns at
all (see repository/orm/run_models.py's Run model). Before this fix,
run["model_reports"] existed only in the in-memory compat-store run dict
(project_shared._compat_project_runs), which is evicted whenever the server
restarts. get_project_runs_compat()'s ORM-fallback branch (used once that
in-memory store is gone) never repopulated it, so a multi-PBIX batch run's
per-file download buttons would silently disappear from the UI after a
restart even though the .md files were still sitting on disk untouched.

The fix: write_per_model_run_reports() also writes a small JSON manifest
(run_id__manifest.json) recording {model, path} for each file, and
load_persisted_model_reports() reads it back. get_project_runs_compat()'s
ORM-fallback branch now calls that loader for every reconstructed run.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_run_report_per_model_persistence.sqlite")
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

from semabridge.api.services import run_report_service as rrs

MULTI_MODEL_RUN = {
    "project_id": "proj1",
    "project_name": "Proj One",
    "run_id": "run-restart-test",
    "status": "success",
    "results": [
        {"model": "Sales Report", "summary": {}, "dropped_entities": [], "pbix_path": "C:/tmp/abc_Sales Report.pbix"},
        {"model": "Marketing Analysis", "summary": {}, "dropped_entities": [], "pbix_path": "C:/tmp/def_Marketing Analysis.pbix"},
    ],
}


def test_write_per_model_run_reports_writes_a_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(rrs, "REPORTS_ROOT", tmp_path / "reports")

    written = rrs.write_per_model_run_reports(MULTI_MODEL_RUN, "")
    assert len(written) == 2

    manifest_path = rrs._manifest_path("proj1", "run-restart-test")
    assert manifest_path.exists()
    manifest_entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert {e["model"] for e in manifest_entries} == {"Sales Report", "Marketing Analysis"}


def test_load_persisted_model_reports_reconstructs_exact_model_labels_after_restart(tmp_path, monkeypatch):
    """Simulates the restart scenario directly: write once (as if from the
    live process that ran the sync), then read back via ONLY the disk-based
    loader -- no in-memory run dict involved -- exactly what
    get_project_runs_compat()'s ORM-fallback branch does.
    """
    monkeypatch.setattr(rrs, "REPORTS_ROOT", tmp_path / "reports")

    rrs.write_per_model_run_reports(MULTI_MODEL_RUN, "")

    reconstructed = rrs.load_persisted_model_reports("proj1", "run-restart-test")
    assert len(reconstructed) == 2
    by_model = {e["model"]: e["path"] for e in reconstructed}
    # Exact original labels (spaces, case) survive -- NOT the lossily
    # sanitized filename fragment ("Sales_Report").
    assert "Sales Report" in by_model
    assert "Marketing Analysis" in by_model
    for path in by_model.values():
        assert Path(path).exists()


def test_load_persisted_model_reports_returns_empty_for_single_model_run(tmp_path, monkeypatch):
    monkeypatch.setattr(rrs, "REPORTS_ROOT", tmp_path / "reports")

    single_run = dict(MULTI_MODEL_RUN, run_id="run-single", results=[MULTI_MODEL_RUN["results"][0]])
    written = rrs.write_per_model_run_reports(single_run, "")
    assert written == []
    assert rrs.load_persisted_model_reports("proj1", "run-single") == []


def test_load_persisted_model_reports_returns_empty_when_manifest_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(rrs, "REPORTS_ROOT", tmp_path / "reports")
    assert rrs.load_persisted_model_reports("proj1", "never-ran") == []


def test_load_persisted_model_reports_ignores_entries_whose_file_was_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(rrs, "REPORTS_ROOT", tmp_path / "reports")

    written = rrs.write_per_model_run_reports(MULTI_MODEL_RUN, "")
    # Simulate one report file being cleaned up/lost independently of the
    # manifest (e.g. a retention job) -- must not resurface a dead link.
    Path(written[0]["path"]).unlink()

    reconstructed = rrs.load_persisted_model_reports("proj1", "run-restart-test")
    assert len(reconstructed) == 1
    assert reconstructed[0]["model"] == written[1]["model"]


def test_get_project_runs_compat_orm_fallback_repopulates_model_reports(tmp_path, monkeypatch):
    """End-to-end at the real call site: a run known only to the ORM (as if
    the in-memory compat store was evicted by a restart) comes back from
    get_project_runs_compat() with model_reports already populated, so the
    frontend can render per-file download buttons without any other change.
    """
    from datetime import datetime, timezone
    from semabridge.api.services import run_service

    monkeypatch.setattr(rrs, "REPORTS_ROOT", tmp_path / "reports")
    rrs.write_per_model_run_reports(MULTI_MODEL_RUN, "")

    class _FakeRow:
        run_id = "run-restart-test"
        project_id = "proj1"
        run_type = "SYNC"
        sync_mode = "copy"
        status = "success"
        started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        completed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        before_target_snapshot_ids = None
        after_target_snapshot_ids = None

    class _FakeScalars:
        def all(self):
            return [_FakeRow()]

    class _FakeExecuteResult:
        def scalars(self):
            return _FakeScalars()

    class _FakeSession:
        def execute(self, *_a, **_k):
            return _FakeExecuteResult()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeDbManager:
        def get_session(self):
            return _FakeSession()

    monkeypatch.setattr(run_service, "_compat_project_runs", {})
    monkeypatch.setattr(
        "semabridge.repository.orm.session_factory.db_manager", _FakeDbManager(), raising=False
    )

    import asyncio

    results = asyncio.run(run_service.get_project_runs_compat("proj1"))
    assert len(results) == 1
    model_reports = results[0]["model_reports"]
    assert {e["model"] for e in model_reports} == {"Sales Report", "Marketing Analysis"}
