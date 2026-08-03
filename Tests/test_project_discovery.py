from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from semabridge.api.services import project_projects_impl as ppi


def _reset_compat_state() -> None:
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


def test_project_discovery_returns_display_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

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


def test_save_project_config_persists_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
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

    result = asyncio.run(
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

    assert result["project_id"] == project_id
    assert result["status"] == "saved"
    assert ppi._compat_projects[project_id]["semantic_name"] == "Demo"


def test_create_project_reuses_existing_semantic_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _reset_compat_state()

    first = asyncio.run(
        ppi.create_project_compat(
            {
                "name": "Competitive Marketing Analysis",
                "source": {"type": "fabric", "workspace_id": "ws-1"},
                "target": {"type": "snowflake"},
            }
        )
    )

    second = asyncio.run(
        ppi.create_project_compat(
            {
                "name": "Competitive Marketing Analysis",
                "source": {"type": "fabric", "workspace_id": "ws-2"},
                "target": {"type": "snowflake"},
            }
        )
    )

    assert first["project_id"] == second["project_id"]
    assert first["semantic_name"] == "Competitive Marketing Analysis"
    assert len([p for p in ppi._compat_projects.values() if p.get("semantic_name") == "Competitive Marketing Analysis"]) == 1


def test_list_projects_keeps_distinct_ids_sharing_same_display_name(monkeypatch, tmp_path):
    """Regression: list_projects_compat() used to dedup by casefolded
    display name instead of project_id, so two genuinely different
    projects that happen to share a name (e.g. after a rename collides
    with an existing project) would silently collapse to one — the
    project that lost would vanish from the Home page even though its
    runs still show up on the Runs page (which lists by project_id with
    no name-based dedup at all). project_id is the real unique
    identifier here — it's what runs are filed under and it's guaranteed
    unique by construction — so dedup must key on it, not on name.

    Asserts both ids are present as a subset, not that the result is
    exactly these two and nothing else: _compat_config_roots() always
    additionally includes this machine's real on-disk Config/projects
    directory regardless of monkeypatch.chdir (a separate, tracked
    pre-existing test-isolation gap — see _compat_repo_root() in
    project_shared.py), so an exact-count assertion here would be
    fragile to whatever real project files happen to exist locally.
    What this regression actually cares about — that dedup never drops
    either id — doesn't need an exact count to prove it."""
    monkeypatch.chdir(tmp_path)
    _reset_compat_state()

    ppi._compat_projects["proj-aaa"] = {
        "id": "proj-aaa",
        "project_id": "proj-aaa",
        "name": "Q1 Sales Analytics",
        "display_name": "Q1 Sales Analytics",
        "source": "fabric",
        "target_type": "snowflake",
    }
    ppi._compat_projects["proj-bbb"] = {
        "id": "proj-bbb",
        "project_id": "proj-bbb",
        "name": "Q1 Sales Analytics",
        "display_name": "Q1 Sales Analytics",
        "source": "fabric",
        "target_type": "snowflake",
    }

    rows = asyncio.run(ppi.list_projects_compat())

    ids = {row["id"] for row in rows}
    assert {"proj-aaa", "proj-bbb"}.issubset(ids), (
        "both distinct-id projects sharing a display name must survive "
        "dedup — neither should be silently dropped"
    )


def test_list_projects_includes_semantic_models(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_id = "proj-ffnew"
    project_yaml = (
        'project_id: "proj-ffnew"\n'
        'display_name: "Regional Sales Sample"\n'
        'project_name: "Regional Sales Sample"\n'
        "source:\n"
        "  type: fabric\n"
        "  models:\n"
        "    - continent\n"
        "    - annual\n"
        "    - Regional Sales Sample\n"
        "target:\n"
        "  type: snowflake\n"
    )
    (projects_dir / f"{project_id}.yaml").write_text(project_yaml, encoding="utf-8")

    _reset_compat_state()
    rows = asyncio.run(ppi.list_projects_compat())

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == project_id
    assert row["name"] == "Regional Sales Sample"
    assert row["semantic_models"] == ["continent", "annual", "Regional Sales Sample"]
    assert row["model_count"] == 3
