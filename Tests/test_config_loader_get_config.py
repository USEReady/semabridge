from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import yaml

from semabridge.core.config_loader import get_config


def test_get_config_reads_self_contained_project_yaml_from_Config_projects(monkeypatch):
    root = Path.cwd() / ".tmp_test_configs" / f"cfg-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        projects_dir = root / "Config" / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        project_path = projects_dir / "proj-core_sf.yaml"
        project_payload = {
            "project_id": "proj-core_sf",
            "project_name": "core_sf",
            "source": {"type": "fabric", "models": ["Core_Finance_v1"]},
            "targets": [{"type": "snowflake"}],
        }
        project_path.write_text(yaml.safe_dump(project_payload, sort_keys=False), encoding="utf-8")

        monkeypatch.chdir(root)
        loaded = get_config("proj-core_sf")
        assert loaded.get("project_id") == "proj-core_sf"
        assert loaded.get("source", {}).get("type") == "fabric"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_get_config_merges_mapping_profile_when_present(monkeypatch):
    root = Path.cwd() / ".tmp_test_configs" / f"cfg-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        projects_dir = root / "config" / "projects"
        profiles_dir = root / "config" / "profiles"
        projects_dir.mkdir(parents=True, exist_ok=True)
        profiles_dir.mkdir(parents=True, exist_ok=True)

        (projects_dir / "proj-x.yaml").write_text(
            yaml.safe_dump(
                {
                    "project_id": "proj-x",
                    "mapping_profile": "mp1",
                    "source": {"type": "fabric", "models": ["X"]},
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (profiles_dir / "mp1.yaml").write_text(
            yaml.safe_dump({"tables": [{"source": "X", "target": "X"}]}, sort_keys=False),
            encoding="utf-8",
        )

        monkeypatch.chdir(root)
        loaded = get_config("proj-x")
        assert loaded.get("mapping_profile") == "mp1"
        assert isinstance(loaded.get("mappings"), dict)
        assert loaded["mappings"].get("tables")
    finally:
        shutil.rmtree(root, ignore_errors=True)
