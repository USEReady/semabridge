from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from semabridge.api.services import project_projects_impl as ppi


def test_project_config_is_saved_to_project_yaml_not_repo_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

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
    ppi._compat_folders.clear()
    ppi._compat_mappings.clear()
    ppi._compat_project_schedules.clear()
    ppi._compat_store_loaded = False

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
