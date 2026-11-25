"""Persistence phase: Postgres-authoritative project config + mapping overrides."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest
import yaml

import semabridge.api.services.project_shared as ps
from semabridge.api.services import project_config_store as store


@pytest.fixture
def projects_dir(tmp_path, monkeypatch):
    pdir = tmp_path / "Config" / "projects"
    pdir.mkdir(parents=True)
    monkeypatch.setattr(ps, "_compat_projects_dirs", lambda: [pdir])
    monkeypatch.setattr(ps, "_compat_projects_dir", lambda: pdir)
    monkeypatch.setattr(ps, "_compat_project_yaml_path", lambda pid: pdir / f"{pid}.yaml")
    monkeypatch.setattr(ps, "_compat_project_yaml_paths", lambda pid: [pdir / f"{pid}.yaml"])
    monkeypatch.setattr(ps, "_compat_project_configs", {})
    monkeypatch.setattr(ps, "_compat_mappings", {})
    monkeypatch.setattr(ps, "_compat_deleted_project_ids", set())
    yield pdir


def _pid(name: str) -> str:
    return f"proj-test-{name}-{time.time_ns()}"


def test_save_versions_cache_and_export(projects_dir):
    pid = _pid("save")
    r1 = store.save_project_config(pid, "project_name: A\nsource:\n  type: fabric\n", reason="create")
    r2 = store.save_project_config(pid, "project_name: B\nsource:\n  type: fabric\n", reason="save")
    r3 = store.save_project_config(pid, "project_name: B\nsource:\n  type: fabric\n", reason="save")
    assert (r1["version"], r2["version"], r3["version"]) == (1, 2, 2)  # unchanged text: no new version
    assert [v["reason"] for v in store.list_config_versions(pid)] == ["save", "create"]
    assert yaml.safe_load(ps._compat_project_configs[pid])["project_name"] == "B"
    assert yaml.safe_load((projects_dir / f"{pid}.yaml").read_text())["project_name"] == "B"
    # The reader serves the DB even when the cache is empty.
    ps._compat_project_configs.pop(pid)
    assert yaml.safe_load(store.get_project_config_text(pid))["project_name"] == "B"


def test_yaml_overrides_go_to_table_and_reset_cannot_bring_them_back(projects_dir):
    pid = _pid("reset")
    cfg = {
        "project_name": "R",
        "source": {"type": "pbix", "models": ["/up/0123456789abcdef0123456789abcdef_Sales.pbix"]},
        "mappings_overrides": [
            {"model": "/up/0123456789abcdef0123456789abcdef_Sales.pbix",
             "source_path": "datasets.T.columns.A", "target_name": "A_NEW"},
            {"source_path": "datasets.T.columns.B", "target_name": "B_NEW"},
        ],
    }
    store.save_project_config(pid, yaml.safe_dump(cfg), reason="create")
    rows = store.list_override_rows(pid)
    assert {(r.model_key, r.source_path) for r in rows} == {
        ("sales", "datasets.T.columns.A"), ("", "datasets.T.columns.B")}

    # A later mapping edit wins and is exported.
    store.upsert_override(pid, model="/other/fedcba9876543210fedcba9876543210_Sales.pbix",
                          source_path="datasets.T.columns.A", target_name="A_EDIT")
    exported = yaml.safe_load((projects_dir / f"{pid}.yaml").read_text())
    assert {o["source_path"]: o["target_name"] for o in exported["mappings_overrides"]} == {
        "datasets.T.columns.A": "A_EDIT", "datasets.T.columns.B": "B_NEW"}

    # Reset: the table is emptied and neither cache, reader nor export bring
    # the stored version's old names back.
    store.delete_overrides(pid)
    assert store.list_overrides(pid) == []
    assert "mappings_overrides" not in yaml.safe_load(store.get_project_config_text(pid, use_cache=False))
    assert "mappings_overrides" not in yaml.safe_load((projects_dir / f"{pid}.yaml").read_text())


def test_startup_import_new_file_and_hand_edit(projects_dir):
    pid = _pid("import")
    path = projects_dir / f"{pid}.yaml"
    path.write_text("project_name: FROM_FILE\nsource:\n  type: fabric\n")
    report = store.import_project_config_files()
    assert [e["project_id"] for e in report["imported"]] == [pid]
    assert store.latest_config_row(pid).reason == "import"

    # Unchanged file: nothing imported.
    report = store.import_project_config_files()
    assert pid in report["unchanged"]

    # Hand edit after the latest version -> imported as reason=file_edit.
    path.write_text("project_name: HAND_EDITED\nsource:\n  type: fabric\n")
    future = time.time() + 5
    os.utime(path, (future, future))
    report = store.import_project_config_files()
    assert [e["project_id"] for e in report["file_edits"]] == [pid]
    row = store.latest_config_row(pid)
    assert (row.version, row.reason) == (2, "file_edit")


def test_import_merges_legacy_store_edits_store_wins(projects_dir):
    pid = _pid("legacy")
    (projects_dir / f"{pid}.yaml").write_text(yaml.safe_dump({
        "project_name": "L",
        "source": {"type": "pbix", "models": ["/a/11111111111111111111111111111111_M.pbix"]},
        "mappings_overrides": [
            {"model": "/a/11111111111111111111111111111111_M.pbix", "source_path": "p1", "target_name": "YAML_1"},
            {"model": "/a/11111111111111111111111111111111_M.pbix", "source_path": "p2", "target_name": "YAML_2"},
        ],
    }))
    ps._compat_mappings["m1"] = {"project_id": pid, "is_user_edited": True, "source_path": "p1",
                                 "model": "/tmp/22222222222222222222222222222222_M.pbix",
                                 "target_name": "STORE_1"}
    store.import_project_config_files()
    names = {r.source_path: r.target_name for r in store.list_override_rows(pid)}
    assert names == {"p1": "STORE_1", "p2": "YAML_2"}


def test_delete_mappings_requires_project_id():
    from semabridge.api.services.mapping_service import delete_mappings_compat
    from semabridge.domain.exceptions import ValidationError

    with pytest.raises(ValidationError):
        asyncio.run(delete_mappings_compat(None))


def test_save_store_failure_is_logged_and_reported(monkeypatch, caplog):
    def _boom(path, text):
        raise OSError("disk full")

    # Patch the module-level store state BEFORE the failing save so teardown
    # restores the clean values. (Patching after the failure recorded the
    # failed state as the "original" and leaked "disk full" into later tests
    # on the same xdist worker.)
    monkeypatch.setattr(ps, "_compat_store_dirty", False)
    monkeypatch.setattr(ps, "_compat_store_last_error", None)
    monkeypatch.setattr(ps, "_compat_atomic_write_text", _boom)
    monkeypatch.setattr(ps, "_compat_schedule_store_flush", lambda delay=1.0: None)
    with caplog.at_level("ERROR"):
        assert ps._compat_save_store() is False
    assert any("Failed to persist compat store" in r.message for r in caplog.records)
    with pytest.raises(ps.CompatStorePersistenceError):
        ps._compat_raise_if_store_failed()


def test_preview_cleanup_report_mode_never_deletes():
    from semabridge.api.services.preview_cleanup_service import cleanup_preview_projects
    from semabridge.repository.orm.models import Project
    from semabridge.repository.orm.session_factory import db_manager

    from semabridge.repository.orm.base import Base

    Base.metadata.create_all(bind=db_manager.get_engine(), checkfirst=True)
    pid = f"preview-{time.time_ns()}"
    with db_manager.get_session() as session:
        session.add(Project(project_id=pid, name="dry-run-preview"))
    report = cleanup_preview_projects(0, apply=False)
    assert report["mode"] == "report"
    assert pid in [c["project_id"] for c in report["would_delete"]]
    with db_manager.get_session() as session:
        assert session.get(Project, pid) is not None
        session.delete(session.get(Project, pid))
