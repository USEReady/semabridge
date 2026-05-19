from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from semabridge.api.services import project_projects_impl as ppi
from semabridge.api.services import project_shared


def _reset_compat_state() -> None:
    project_shared._compat_projects.clear()
    project_shared._compat_project_configs.clear()
    project_shared._compat_project_runs.clear()
    project_shared._compat_project_snapshots.clear()
    project_shared._compat_snapshot_groups.clear()
    project_shared._compat_run_snapshots.clear()
    project_shared._compat_folders.clear()
    project_shared._compat_mappings.clear()
    project_shared._compat_project_schedules.clear()
    project_shared._compat_deleted_project_ids.clear()
    project_shared._compat_store_loaded = False


def test_project_discovery_returns_display_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(project_shared, "_compat_repo_root", lambda: tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_id = "proj-1777017550799"
    project_yaml = (
        'project_id: "proj-1777017550799"\n'
        'display_name: "Q1 Sales Analytics Sync"\n'
        "project_name: cust\n"
        "source:\n"
        "  type: fabric\n"
        "target:\n"
        "  type: snowflake\n"
    )
    (projects_dir / f"{project_id}.yaml").write_text(project_yaml, encoding="utf-8")

    _reset_compat_state()
    rows = asyncio.run(ppi.list_project_discovery_compat())

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == project_id
    assert row["name"] == "Q1 Sales Analytics Sync"
    assert row["display_name"] == "Q1 Sales Analytics Sync"
    assert row["file_name"] == f"{project_id}.yaml"


def test_delete_project_removes_config_file_and_prevents_refresh_restore(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(project_shared, "_compat_repo_root", lambda: tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_id = "proj-delete-me"
    project_path = projects_dir / f"{project_id}.yaml"
    project_path.write_text(
        (
            'project_id: "proj-delete-me"\n'
            'display_name: "Delete Me"\n'
            "project_name: Delete Me\n"
            "source:\n"
            "  type: fabric\n"
            "target:\n"
            "  type: snowflake\n"
        ),
        encoding="utf-8",
    )

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())
    assert [row["id"] for row in rows] == [project_id]

    response = asyncio.run(ppi.delete_project_compat(project_id))
    assert response.status_code == 204
    assert not project_path.exists()

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())
    assert rows == []


def test_delete_project_removes_repo_config_when_backend_cwd_differs(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    server_cwd = tmp_path / "server"
    projects_dir = repo_root / "config" / "projects"
    projects_dir.mkdir(parents=True)
    server_cwd.mkdir()

    project_id = "proj-repo-delete"
    project_path = projects_dir / f"{project_id}.yaml"
    project_path.write_text(
        (
            'project_id: "proj-repo-delete"\n'
            'display_name: "Repo Delete"\n'
            "project_name: Repo Delete\n"
            "source:\n"
            "  type: fabric\n"
            "target:\n"
            "  type: snowflake\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(server_cwd)
    monkeypatch.setattr(project_shared, "_compat_repo_root", lambda: repo_root)

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())
    assert [row["id"] for row in rows] == [project_id]

    response = asyncio.run(ppi.delete_project_compat(project_id))
    assert response.status_code == 204
    assert not project_path.exists()
    assert not (server_cwd / "config" / "projects" / f"{project_id}.yaml").exists()

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())
    assert rows == []


def test_deleted_project_is_not_recovered_by_stale_detail_requests(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(project_shared, "_compat_repo_root", lambda: tmp_path)

    project_id = "proj-stale-detail"
    _reset_compat_state()
    asyncio.run(ppi.create_project_compat({
        "id": project_id,
        "project_id": project_id,
        "name": "Stale Detail",
        "source": {"type": "fabric"},
        "target": {"type": "snowflake"},
    }))

    response = asyncio.run(ppi.delete_project_compat(project_id))
    assert response.status_code == 204

    with pytest.raises(ppi.HTTPException) as detail_exc:
        asyncio.run(ppi.get_project_compat(project_id))
    assert detail_exc.value.status_code == 404

    with pytest.raises(ppi.HTTPException) as config_exc:
        asyncio.run(ppi.get_project_config_compat(project_id))
    assert config_exc.value.status_code == 404

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())
    assert rows == []


def test_deleted_project_tombstone_suppresses_stale_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(project_shared, "_compat_repo_root", lambda: tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_id = "proj-stale-yaml"
    (projects_dir / f"{project_id}.yaml").write_text(
        (
            'project_id: "proj-stale-yaml"\n'
            'display_name: "Stale YAML"\n'
            "project_name: Stale YAML\n"
            "source:\n"
            "  type: fabric\n"
            "target:\n"
            "  type: snowflake\n"
        ),
        encoding="utf-8",
    )

    _reset_compat_state()
    project_shared._compat_deleted_project_ids.add(project_id)
    project_shared._compat_save_store()

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())
    assert rows == []


def test_save_project_config_requires_display_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(project_shared, "_compat_repo_root", lambda: tmp_path)
    _reset_compat_state()

    project_id = "proj-2000"
    ppi._compat_projects[project_id] = {
        "id": project_id,
        "project_id": project_id,
        "name": "Demo",
        "display_name": "Demo",
        "source": "fabric",
        "target_type": "snowflake",
    }

    with pytest.raises(ppi.HTTPException) as exc_info:
        asyncio.run(
            ppi.save_project_config_compat(
                project_id,
                {
                    "config_yaml": (
                        'project_id: "proj-2000"\n'
                        "project_name: Demo\n"
                        "source:\n"
                        "  type: fabric\n"
                    )
                },
            )
        )

    assert exc_info.value.status_code == 400
    assert "display_name is required" in str(exc_info.value.detail)
