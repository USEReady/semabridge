"""Multi-model dry-run: job ownership checks, collision auto-resolve toggle
forwarding, compat-store lock, and preview -> real project synonym migration.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import types

import pytest
from fastapi import BackgroundTasks, HTTPException

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

FABRIC_CFG = {"type": "fabric", "workspace_id": "ws-1"}
TARGET_CFG = {"type": "snowflake"}


@pytest.fixture(autouse=True)
def _ensure_orm_tables():
    """Other test modules reset the shared in-memory SQLite engine, which
    yields an empty database; make sure every ORM table exists here."""
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.session_factory import db_manager

    Base.metadata.create_all(bind=db_manager.get_engine())
    yield


def _ok(name, project_id="preview-x"):
    return {
        "success": True,
        "project_id": project_id,
        "model_name": name,
        "entity_mappings": [{"source_name": "C", "entity_kind": "column"}],
        "extraction_failed": False,
        "dropped_entities": [],
        "summary": {"total_fields": 1},
    }


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def _ctrl_with_user(monkeypatch, user_box, auth=True):
    import semabridge.api.controllers.dry_run_jobs_controller as ctrl

    monkeypatch.setattr(ctrl, "require_request_user_id", lambda request: user_box["user"])
    monkeypatch.setattr(ctrl, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "auth_is_enabled", lambda: auth)
    monkeypatch.setattr(ctrl, "_assert_project_access", lambda project_id, user_id: None)
    return ctrl


def test_preview_job_only_visible_to_its_creator(monkeypatch):
    user = {"user": "user-a"}
    ctrl = _ctrl_with_user(monkeypatch, user)
    request = ctrl.DryRunJobRequest(source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["M1"])
    job = asyncio.run(ctrl.create_dry_run_job_endpoint("preview", object(), request, BackgroundTasks()))
    job_id, file_id = job["job_id"], job["files"][0]["file_id"]

    assert asyncio.run(ctrl.get_dry_run_job_status_endpoint("preview", job_id, object()))["job_id"] == job_id

    user["user"] = "user-b"
    for call in (
        lambda: ctrl.get_dry_run_job_status_endpoint("preview", job_id, object()),
        lambda: ctrl.get_dry_run_job_file_endpoint("preview", job_id, file_id, object()),
        lambda: ctrl.rerun_dry_run_job_file_endpoint("preview", job_id, file_id, object(), BackgroundTasks()),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call())
        assert exc.value.status_code == 403


def test_job_not_visible_through_a_different_project(monkeypatch):
    user = {"user": "user-a"}
    ctrl = _ctrl_with_user(monkeypatch, user)
    request = ctrl.DryRunJobRequest(source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["M1"])
    job = asyncio.run(ctrl.create_dry_run_job_endpoint("proj-1", object(), request, BackgroundTasks()))
    job_id, file_id = job["job_id"], job["files"][0]["file_id"]

    assert asyncio.run(ctrl.get_dry_run_job_status_endpoint("proj-1", job_id, object()))["project_id"] == "proj-1"
    for project_id in ("proj-2", "preview"):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(ctrl.get_dry_run_job_file_endpoint(project_id, job_id, file_id, object()))
        assert exc.value.status_code == 404


def test_unknown_job_is_404(monkeypatch):
    ctrl = _ctrl_with_user(monkeypatch, {"user": "u"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ctrl.get_dry_run_job_status_endpoint("preview", "dryrun-nope", object()))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Collision auto-resolve toggle (preview == deploy)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Compat store lock
# ---------------------------------------------------------------------------


def test_concurrent_compat_saves_do_not_raise_or_corrupt(monkeypatch, tmp_path):
    import semabridge.api.services.project_shared as ps

    store = tmp_path / "store.json"
    monkeypatch.setenv("SEMABRIDGE_COMPAT_STORE_PATH", str(store))
    warnings = []
    monkeypatch.setattr(ps.logger, "warning", lambda *a, **k: warnings.append(a))

    keys = [f"preview-locktest-{i}" for i in range(10)]
    errors = []
    barrier = threading.Barrier(len(keys))

    def _worker(key):
        try:
            barrier.wait(timeout=10)
            for n in range(15):
                with ps.compat_store_lock():
                    ps._compat_projects[key] = {"id": key, "n": n, "payload": list(range(50))}
                    ps._compat_project_configs[key] = f"project_name: {key}-{n}\n"
                ps._compat_save_store()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(k,)) for k in keys]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        assert not warnings, warnings
        data = json.loads(store.read_text(encoding="utf-8"))
        for key in keys:
            assert data["projects"][key]["n"] == 14
            assert data["project_configs"][key] == f"project_name: {key}-14\n"
        # No temp files left behind by the atomic writer.
        assert [p.name for p in tmp_path.iterdir()] == ["store.json"]
    finally:
        for key in keys:
            ps._compat_projects.pop(key, None)
            ps._compat_project_configs.pop(key, None)


# ---------------------------------------------------------------------------
# Synonym migration preview -> real project
# ---------------------------------------------------------------------------


def _put_synonym(project_id, model, table, column, synonyms):
    from semabridge.api.services.dry_run_job_service import _ensure_schema
    from semabridge.repository.orm.models import SynonymOverride
    from semabridge.repository.orm.session_factory import db_manager

    _ensure_schema()
    with db_manager.get_session() as session:
        row = SynonymOverride(project_id=project_id, model_name=model, table_name=table, column_name=column)
        row.synonyms = synonyms
        session.add(row)


def test_copy_synonym_overrides_copies_by_model_table_column():
    from semabridge.utils.synonyms import copy_synonym_overrides, load_synonym_overrides

    _put_synonym("preview-syn-a", "ds-1", "Sales", "Amount", ["revenue"])
    _put_synonym("preview-syn-b", "ds-2", "Fin", "Cost", ["spend"])
    _put_synonym("preview-syn-b", "ds-1", "Sales", "Amount", ["ignored-later-source"])

    copied = copy_synonym_overrides(["preview-syn-a", "preview-syn-b"], "real-syn-1")
    assert copied == 2
    real = load_synonym_overrides("real-syn-1")
    assert real[("ds-1", "sales", "amount")] == ["revenue"]
    assert real[("ds-2", "fin", "cost")] == ["spend"]
    # Re-running is idempotent: no duplicate rows, and an unchanged (not
    # newer) preview value never overwrites what the project already has.
    assert copy_synonym_overrides(["preview-syn-a"], "real-syn-1") == 0
    assert len(load_synonym_overrides("real-syn-1")) == 2


def test_create_project_migrates_synonyms_from_dry_run_job_and_preview_id(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc
    from semabridge.api.services import dry_run_job_service as job_service
    import semabridge.api.controllers.mappings_controller as mc
    from semabridge.utils.synonyms import load_synonym_overrides

    async def _fake_pipeline(*, project_id, request_user_id, source_config, target_config, selected_sources, auto_resolve_collisions=False):
        return _ok(selected_sources[0], project_id=f"preview-job-{selected_sources[0].lower()}")

    monkeypatch.setattr(mc, "_run_dry_run_pipeline", _fake_pipeline)
    job = job_service.create_dry_run_job(
        project_id="preview", source_config=dict(FABRIC_CFG), target_config=TARGET_CFG, selected_sources=["Alpha", "Beta"],
    )
    job_service.run_dry_run_job(job["job_id"])

    _put_synonym("preview-job-alpha", "ds-alpha", "T", "ColA", ["a1"])
    _put_synonym("preview-job-beta", "ds-beta", "T", "ColB", ["b1"])
    _put_synonym("preview-single-xyz", "ds-single", "T", "ColS", ["s1"])

    captured = {}

    async def _fake_create(payload_dict):
        captured.update(payload_dict)
        return {"id": "real-from-job"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="p", source=dict(FABRIC_CFG), dry_run_job_id=job["job_id"], preview_project_id="preview-single-xyz",
    )
    resp = asyncio.run(pc.create_project(object(), payload))

    assert "dry_run_job_id" not in captured and "preview_project_id" not in captured
    assert resp["id"] == "real-from-job"
    assert resp["synonym_migration"]["copied"] == 3
    real = load_synonym_overrides("real-from-job")
    assert real[("ds-alpha", "t", "cola")] == ["a1"]
    assert real[("ds-beta", "t", "colb")] == ["b1"]
    assert real[("ds-single", "t", "cols")] == ["s1"]


def test_create_project_without_preview_fields_has_no_migration_report(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc

    async def _fake_create(payload_dict):
        return {"id": "plain"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)
    resp = asyncio.run(pc.create_project(object(), pc.CreateProjectRequest(name="p")))
    assert resp == {"id": "plain"}
