from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

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


def test_dependency_graph_builds_nodes_edges_from_project_yaml(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies:\n    - proj-b\n    - ext-core\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies: []\n",
        encoding="utf-8",
    )

    payload = asyncio.run(ppi.get_project_dependency_graph_compat())
    node_ids = [n["id"] for n in payload["nodes"]]
    edge_ids = [e["id"] for e in payload["edges"]]

    assert "proj-a" in node_ids
    assert "proj-b" in node_ids
    assert "ext-core" in node_ids
    assert "proj-a->proj-b" in edge_ids
    assert "proj-a->ext-core" in edge_ids
    assert payload["project_count"] == 2


def test_dependency_impact_returns_upstream_and_downstream(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies:\n    - proj-b\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-c.yaml").write_text(
        "project_id: proj-c\nmodel_manifest:\n  dependencies:\n    - proj-a\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies: []\n",
        encoding="utf-8",
    )

    impact = asyncio.run(ppi.get_project_dependency_impact_compat("proj-a"))
    assert impact["project_id"] == "proj-a"
    assert impact["upstream_dependencies"] == ["proj-b"]
    assert impact["downstream_dependents"] == ["proj-c"]


def test_dependency_impact_404_for_unknown_project(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    with pytest.raises(ppi.HTTPException) as exc:
        asyncio.run(ppi.get_project_dependency_impact_compat("missing"))

    assert exc.value.status_code == 404


def test_dependency_graph_reports_cycles(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    _reset_compat_state()
    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies:\n    - proj-b\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies:\n    - proj-a\n",
        encoding="utf-8",
    )

    payload = asyncio.run(ppi.get_project_dependency_graph_compat())
    assert payload["cycles"] == [["proj-a", "proj-b", "proj-a"]]


def test_dependency_graph_strict_mode_rejects_cycles(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)

    _reset_compat_state()
    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies:\n    - proj-b\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies:\n    - proj-a\n",
        encoding="utf-8",
    )

    with pytest.raises(ppi.HTTPException) as exc:
        asyncio.run(ppi.get_project_dependency_graph_compat(strict_cycles=True))

    assert exc.value.status_code == 409


def test_snapshot_graph_payload_includes_column_lineage():
    snapshot = SimpleNamespace(
        snapshot_id="snap-1",
        project_id="proj-a",
        timestamp="2026-05-12T00:00:00Z",
        sml_blob={
            "model_name": "Model A",
            "datasets": [
                {
                    "schema": "PUBLIC",
                    "table": "ORDERS",
                    "columns": [
                        {"name": "ORDER_ID"},
                        {"name": "CUSTOMER_ID"},
                    ],
                },
                {
                    "schema": "PUBLIC",
                    "table": "CUSTOMERS",
                    "columns": [
                        {"name": "CUSTOMER_ID"},
                        {"name": "CUSTOMER_NAME"},
                    ],
                },
            ],
            "relationships": [
                {
                    "from_table": "ORDERS",
                    "to_table": "CUSTOMERS",
                    "from_column": "CUSTOMER_ID",
                    "to_column": "CUSTOMER_ID",
                    "cardinality": "many-to-one",
                }
            ],
        },
    )

    default_payload = ppi._snapshot_graph_payload(snapshot, "proj-a")
    payload = ppi._snapshot_graph_payload(snapshot, "proj-a", include_column_lineage=True)

    default_column_nodes = [node for node in default_payload["nodes"] if node.get("data", {}).get("nodeType") == "column"]
    column_nodes = [node for node in payload["nodes"] if node.get("data", {}).get("nodeType") == "column"]
    column_labels = sorted(node.get("data", {}).get("label") for node in column_nodes)
    column_edges = [edge for edge in payload["edges"] if edge.get("source", "").startswith("table-") and edge.get("target", "").startswith("column-")]
    lineage_edges = [edge for edge in payload["edges"] if edge.get("data", {}).get("nodeType") == "column_relationship"]

    assert default_column_nodes == []
    assert column_labels == ["CUSTOMER_ID", "CUSTOMER_ID", "CUSTOMER_NAME", "ORDER_ID"]
    assert len(column_edges) == 4
    assert len(lineage_edges) == 1
    assert payload["meta"]["columns_included"] == 4
    assert payload["meta"]["column_relationships_included"] == 1


def test_graph_snapshot_compat_exposes_column_lineage_only_when_requested(monkeypatch):
    snapshot = SimpleNamespace(
        snapshot_id="snap-1",
        project_id="proj-a",
        timestamp="2026-05-12T00:00:00Z",
        sml_blob={
            "model_name": "Model A",
            "datasets": [
                {
                    "schema": "PUBLIC",
                    "table": "ORDERS",
                    "columns": [{"name": "ORDER_ID"}],
                }
            ],
        },
    )

    monkeypatch.setattr(ppi.db_manager, "get_snapshot", lambda snapshot_id: snapshot if snapshot_id == "snap-1" else None)

    default_payload = asyncio.run(ppi.graph_snapshot_compat("proj-a", "snap-1"))
    lineage_payload = asyncio.run(ppi.graph_snapshot_compat("proj-a", "snap-1", include_column_lineage=True))

    assert not any(node.get("data", {}).get("nodeType") == "column" for node in default_payload["nodes"])
    assert any(node.get("data", {}).get("nodeType") == "column" for node in lineage_payload["nodes"])


def test_contract_preflight_non_breaking_minor_version(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        (
            "project_id: proj-a\n"
            "model_manifest:\n"
            "  contract_id: contract.proj-a\n"
            "  contract_version: 1.2.0\n"
            "  dependencies: []\n"
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        ppi.preflight_project_contract_change_compat(
            "proj-a",
            {"model_manifest": {"contract_version": "1.3.0"}},
        )
    )
    assert result["is_breaking"] is False
    assert any("contract_version changed" in msg for msg in result["non_breaking_reasons"])


def test_contract_preflight_breaking_major_version_requires_deprecation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        (
            "project_id: proj-a\n"
            "model_manifest:\n"
            "  contract_id: contract.proj-a\n"
            "  contract_version: 1.2.0\n"
            "  dependencies: []\n"
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        ppi.preflight_project_contract_change_compat(
            "proj-a",
            {"model_manifest": {"contract_version": "2.0.0"}},
        )
    )
    assert result["is_breaking"] is True
    assert any("major contract_version changed" in msg for msg in result["breaking_reasons"])
    assert any("deprecation_date is required" in msg for msg in result["breaking_reasons"])
    assert result["policy_mode"] == "warn"
    assert result["allow_merge"] is True
    assert "publish_new_contract_version" in result["required_actions"]


def test_contract_preflight_reports_transitive_dependents(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies: []\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies:\n    - proj-a\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-c.yaml").write_text(
        "project_id: proj-c\nmodel_manifest:\n  dependencies:\n    - proj-b\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        ppi.preflight_project_contract_change_compat(
            "proj-a",
            {"model_manifest": {"contract_version": "2.0.0"}, "deprecation_date": "2026-12-31"},
        )
    )
    assert result["direct_downstream_dependents"] == ["proj-b"]
    assert result["transitive_downstream_dependents"] == ["proj-b", "proj-c"]
    assert "review_transitive_dependents" in result["required_actions"]


def test_contract_preflight_enforce_blocks_breaking_changes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        (
            "project_id: proj-a\n"
            "model_manifest:\n"
            "  contract_id: contract.proj-a\n"
            "  contract_version: 1.2.0\n"
            "  dependencies: []\n"
        ),
        encoding="utf-8",
    )

    result = asyncio.run(
        ppi.preflight_project_contract_change_compat(
            "proj-a",
            {"policy_mode": "enforce", "model_manifest": {"contract_version": "2.0.0"}},
        )
    )
    assert result["is_breaking"] is True
    assert result["policy_mode"] == "enforce"
    assert result["allow_merge"] is False


def test_contract_preflight_rejects_invalid_policy_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ppi.HTTPException) as exc:
        asyncio.run(
            ppi.preflight_project_contract_change_compat(
                "proj-a",
                {"policy_mode": "block-all"},
            )
        )
    assert exc.value.status_code == 400


def test_premerge_validate_allows_when_no_blockers(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  contract_version: 1.0.0\n  dependencies: []\n",
        encoding="utf-8",
    )
    payload = asyncio.run(
        ppi.premerge_validate_projects_compat(
            {"project_ids": ["proj-a"], "policy_mode": "enforce", "strict_cycles": True}
        )
    )
    assert payload["allow_merge"] is True
    assert payload["blocking_reasons"] == []


def test_premerge_validate_blocks_on_cycles_when_strict(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  dependencies:\n    - proj-b\n",
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        "project_id: proj-b\nmodel_manifest:\n  dependencies:\n    - proj-a\n",
        encoding="utf-8",
    )
    payload = asyncio.run(
        ppi.premerge_validate_projects_compat(
            {"project_ids": ["proj-a"], "strict_cycles": True}
        )
    )
    assert payload["allow_merge"] is False
    assert "dependency_cycles_detected" in payload["blocking_reasons"]


def test_premerge_validate_blocks_on_contract_policy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        "project_id: proj-a\nmodel_manifest:\n  contract_version: 1.0.0\n  dependencies: []\n",
        encoding="utf-8",
    )
    payload = asyncio.run(
        ppi.premerge_validate_projects_compat(
            {
                "project_ids": ["proj-a"],
                "policy_mode": "enforce",
                "proposed_manifests": {"proj-a": {"contract_version": "2.0.0"}},
            }
        )
    )
    assert payload["allow_merge"] is False
    assert "contract_policy_block" in payload["blocking_reasons"]


def test_dependency_resolution_prefers_valid_pin(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        (
            "project_id: proj-a\n"
            "model_manifest:\n"
            "  dependencies:\n"
            "    - proj-b\n"
            "  dependency_pins:\n"
            "    proj-b: \"1.0.0\"\n"
        ),
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        (
            "project_id: proj-b\n"
            "model_manifest:\n"
            "  contract_version: \"2.0.0\"\n"
            "  published_contract_versions:\n"
            "    - \"1.0.0\"\n"
            "    - \"2.0.0\"\n"
            "  dependencies: []\n"
        ),
        encoding="utf-8",
    )

    payload = asyncio.run(ppi.get_project_dependency_resolution_compat("proj-a"))
    row = payload["dependency_resolutions"][0]
    assert row["dependency_project_id"] == "proj-b"
    assert row["status"] == "pinned"
    assert row["resolved_version"] == "1.0.0"


def test_dependency_resolution_marks_unresolved_pin(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    projects_dir = Path("Config/projects")
    projects_dir.mkdir(parents=True, exist_ok=True)
    _reset_compat_state()

    (projects_dir / "proj-a.yaml").write_text(
        (
            "project_id: proj-a\n"
            "model_manifest:\n"
            "  dependencies:\n"
            "    - proj-b\n"
            "  dependency_pins:\n"
            "    proj-b: \"9.9.9\"\n"
        ),
        encoding="utf-8",
    )
    (projects_dir / "proj-b.yaml").write_text(
        (
            "project_id: proj-b\n"
            "model_manifest:\n"
            "  contract_version: \"2.0.0\"\n"
            "  published_contract_versions:\n"
            "    - \"2.0.0\"\n"
            "  dependencies: []\n"
        ),
        encoding="utf-8",
    )

    payload = asyncio.run(ppi.get_project_dependency_resolution_compat("proj-a"))
    row = payload["dependency_resolutions"][0]
    assert row["status"] == "unresolved_pin"
    assert row["resolved_version"] == "2.0.0"
