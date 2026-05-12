from __future__ import annotations

import pytest
import yaml
from fastapi import HTTPException

from semabridge.api.services import project_projects_impl as ppi


def test_normalize_project_yaml_adds_default_model_manifest():
    normalized = ppi._normalize_project_config_yaml(
        "proj-sales",
        "project_name: Sales\nsource:\n  type: fabric\n",
        "Sales",
    )
    parsed = yaml.safe_load(normalized)
    manifest = parsed.get("model_manifest") or {}

    assert manifest["manifest_version"] == "1"
    assert manifest["model_id"] == "proj-sales"
    assert manifest["topology_layer"] == "spoke"
    assert manifest["owner"] == "unknown"
    assert manifest["contract_id"] == "contract.proj-sales"
    assert manifest["contract_version"] == "1.0.0"
    assert manifest["published_contract_versions"] == ["1.0.0"]
    assert manifest["dependency_pins"] == {}
    assert manifest["dependencies"] == []


def test_normalize_project_yaml_rejects_invalid_topology_layer():
    with pytest.raises(HTTPException) as exc:
        ppi._normalize_project_config_yaml(
            "proj-sales",
            (
                "project_name: Sales\n"
                "model_manifest:\n"
                "  topology_layer: invalid-layer\n"
            ),
            "Sales",
        )

    assert exc.value.status_code == 400
    assert "topology_layer" in str(exc.value.detail)


def test_normalize_project_yaml_rejects_invalid_manifest_version():
    with pytest.raises(HTTPException) as exc:
        ppi._normalize_project_config_yaml(
            "proj-sales",
            (
                "project_name: Sales\n"
                "model_manifest:\n"
                "  manifest_version: \"2\"\n"
            ),
            "Sales",
        )

    assert exc.value.status_code == 400
    assert "manifest_version" in str(exc.value.detail)


def test_normalize_project_yaml_rejects_non_string_dependencies():
    with pytest.raises(HTTPException) as exc:
        ppi._normalize_project_config_yaml(
            "proj-sales",
            (
                "project_name: Sales\n"
                "model_manifest:\n"
                "  dependencies:\n"
                "    - base-model\n"
                "    - 123\n"
            ),
            "Sales",
        )

    assert exc.value.status_code == 400
    assert "dependencies entries" in str(exc.value.detail)


def test_normalize_project_yaml_rejects_invalid_dependency_pins_shape():
    with pytest.raises(HTTPException) as exc:
        ppi._normalize_project_config_yaml(
            "proj-sales",
            (
                "project_name: Sales\n"
                "model_manifest:\n"
                "  dependency_pins:\n"
                "    - bad\n"
            ),
            "Sales",
        )

    assert exc.value.status_code == 400
    assert "dependency_pins" in str(exc.value.detail)
