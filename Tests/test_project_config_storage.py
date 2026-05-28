from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml

from semabridge.api.services import project_projects_impl as ppi
from semabridge.api.services import project_shared as shared


def _isolate_config_roots(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(shared, "_compat_config_roots", lambda: [tmp_path / "Config"])


def test_project_config_is_saved_to_project_yaml_not_repo_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolate_config_roots(monkeypatch, tmp_path)

    config_dir = Path("Config")
    projects_dir = config_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    repo_yaml = config_dir / "semabridge.yaml"
    repo_yaml.write_text("project_name: GLOBAL\nsource:\n  type: fabric\n", encoding="utf-8")

    ppi._compat_projects.clear()
    ppi._compat_project_configs.clear()
    ppi._compat_project_runs.clear()
    ppi._compat_project_snapshots.clear()
    ppi._compat_snapshot_groups.clear()
    ppi._compat_run_snapshots.clear()
    shared._compat_folders.clear()
    shared._compat_mappings.clear()
    shared._compat_project_schedules.clear()
    shared._compat_store_loaded = False
    shared._compat_modular_bootstrapped = False

    project_id = "cust_sf"
    initial_yaml = "project_id: \"cust_sf\"\ndisplay_name: \"Customer SF\"\nproject_name: CUST\nsource:\n  type: fabric\n"

    asyncio.run(
        ppi.create_project_compat(
            {
                "project_id": project_id,
                "name": "Customer SF",
                "source": {"type": "fabric"},
                "target": {"type": "snowflake"},
                "config_yaml": initial_yaml,
            }
        )
    )

    project_yaml_path = projects_dir / f"{project_id}.yaml"
    assert project_yaml_path.exists()
    assert yaml.safe_load(project_yaml_path.read_text(encoding="utf-8")) == yaml.safe_load(initial_yaml)
    assert repo_yaml.read_text(encoding="utf-8") == "project_name: GLOBAL\nsource:\n  type: fabric\n"

    updated_yaml = "project_id: \"cust_sf\"\ndisplay_name: \"Customer SF Updated\"\nproject_name: CUST UPDATED\nsource:\n  type: fabric\n"
    save_result = asyncio.run(ppi.save_project_config_compat(project_id, {"config_yaml": updated_yaml}))

    assert save_result["yaml_path"].replace("\\", "/").endswith(f"Config/projects/{project_id}.yaml")
    assert yaml.safe_load(project_yaml_path.read_text(encoding="utf-8")) == yaml.safe_load(updated_yaml)
    assert repo_yaml.read_text(encoding="utf-8") == "project_name: GLOBAL\nsource:\n  type: fabric\n"

    loaded = asyncio.run(ppi.get_project_config_compat(project_id))
    assert yaml.safe_load(loaded["config_yaml"]) == yaml.safe_load(updated_yaml)
    assert loaded["yaml_path"].replace("\\", "/").endswith(f"Config/projects/{project_id}.yaml")


def test_orm_project_table_wins_over_yaml_and_compat_store(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolate_config_roots(monkeypatch, tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    project_id = "proj-hello"
    project_yaml_path = projects_dir / f"{project_id}.yaml"
    project_yaml_path.write_text(
        'project_id: "proj-hello"\n'
        'display_name: "yaml-hello"\n'
        'project_name: "yaml-hello"\n'
        "source:\n"
        "  type: fabric\n"
        "target:\n"
        "  type: snowflake\n",
        encoding="utf-8",
    )

    class FakeProjectRow:
        def __init__(self, project_id: str, name: str, workspace_id: str, adapter: str) -> None:
            self.project_id = project_id
            self.name = name
            self.workspace_id = workspace_id
            self.adapter = adapter
            self.connection_tag = None
            self.last_updated = None

    class FakeScalarResult:
        def __init__(self, rows) -> None:
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self, rows) -> None:
            self._rows = rows

        def execute(self, stmt):
            return FakeScalarResult(self._rows)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_rows = [FakeProjectRow(project_id, "db-hello", "ws-db", "fabric")]
    monkeypatch.setattr(shared.db_manager, "_session", lambda: FakeSession(fake_rows))

    ppi._compat_projects.clear()
    ppi._compat_project_configs.clear()
    ppi._compat_project_runs.clear()
    ppi._compat_project_snapshots.clear()
    ppi._compat_snapshot_groups.clear()
    ppi._compat_run_snapshots.clear()
    shared._compat_folders.clear()
    shared._compat_mappings.clear()
    shared._compat_project_schedules.clear()
    shared._compat_store_loaded = False
    shared._compat_modular_bootstrapped = False

    shared._compat_ensure_loaded()

    assert ppi._compat_projects[project_id]["name"] == "db-hello"

    yaml_only_project_id = "proj-yaml-only"
    (projects_dir / f"{yaml_only_project_id}.yaml").write_text(
        'project_id: "proj-yaml-only"\n'
        'display_name: "yaml-only"\n'
        'project_name: "yaml-only"\n'
        "source:\n"
        "  type: fabric\n",
        encoding="utf-8",
    )

    assert yaml_only_project_id not in ppi._compat_projects


def test_orm_reconciliation_removes_stale_project_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolate_config_roots(monkeypatch, tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    project_id = "proj-stale"
    project_yaml_path = projects_dir / f"{project_id}.yaml"
    project_yaml_path.write_text(
        'project_id: "proj-stale"\n'
        'display_name: "stale-project"\n'
        'project_name: "stale-project"\n'
        "source:\n"
        "  type: fabric\n"
        "target:\n"
        "  type: snowflake\n",
        encoding="utf-8",
    )

    class FakeScalarResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class FakeSession:
        def execute(self, stmt):
            return FakeScalarResult()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(shared.db_manager, "_session", lambda: FakeSession())

    ppi._compat_projects.clear()
    ppi._compat_project_configs.clear()
    ppi._compat_project_runs.clear()
    ppi._compat_project_snapshots.clear()
    ppi._compat_snapshot_groups.clear()
    ppi._compat_run_snapshots.clear()
    shared._compat_folders.clear()
    shared._compat_mappings.clear()
    shared._compat_project_schedules.clear()
    shared._compat_deleted_project_ids.clear()
    shared._compat_store_loaded = False
    shared._compat_modular_bootstrapped = False

    ppi._compat_projects[project_id] = {
        "id": project_id,
        "project_id": project_id,
        "name": "stale-project",
        "display_name": "stale-project",
        "semantic_name": "stale-project",
        "source": "fabric",
        "target_type": "snowflake",
    }

    shared._compat_ensure_loaded()

    assert project_id not in ppi._compat_projects
    assert not project_yaml_path.exists()


def test_create_project_merges_with_existing_model_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _isolate_config_roots(monkeypatch, tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    # Create a modular YAML project representing the semantic model "continent"
    model_project_id = "proj-continent"
    model_yaml_path = projects_dir / f"{model_project_id}.yaml"
    model_yaml_path.write_text(
        'project_id: "proj-continent"\n'
        'display_name: "continent"\n'
        'project_name: "continent"\n'
        "source:\n"
        "  type: fabric\n"
        "  models:\n"
        "    - continent\n"
        "target:\n"
        "  type: snowflake\n",
        encoding="utf-8",
    )

    # Ensure the modular project is loaded
    shared._compat_store_loaded = False
    shared._compat_modular_bootstrapped = False
    ppi._compat_projects.clear()

    # Now create a new project that uses the semantic model 'continent'
    payload = {
        "name": "hello",
        "source": {"type": "fabric", "models": ["continent"]},
        "target": {"type": "snowflake"},
        "config_yaml": ''
    }

    result = asyncio.run(ppi.create_project_compat(payload))

    # The created project should be present and the old modular YAML project removed
    created_id = result.get("project_id")
    assert created_id is not None
    assert created_id in ppi._compat_projects
    assert model_project_id not in ppi._compat_projects
    assert not model_yaml_path.exists()
