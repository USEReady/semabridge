from __future__ import annotations

import asyncio

import pytest
import yaml
from fastapi import HTTPException

from semabridge.api.controllers.versioning_controller import contract_preflight


def test_contract_preflight_rejects_breaking_change_without_deprecation_date(monkeypatch):
    current_yaml = yaml.safe_dump(
        {
            "project_id": "sales-model",
            "model_manifest": {
                "manifest_version": "1",
                "model_id": "sales-model",
                "topology_layer": "spoke",
                "owner": "analytics",
                "contract_id": "contract.sales-model",
                "contract_version": "1.0.0",
                "published_contract_versions": ["1.0.0"],
                "dependency_pins": {"shared-model": "1.0.1"},
                "dependencies": ["shared-model"],
            },
        },
        sort_keys=False,
    )

    monkeypatch.setattr(
        "semabridge.api.controllers.versioning_controller._compat_load_project_yaml_text",
        lambda project_id: current_yaml if project_id == "sales-model" else current_yaml,
    )

    payload = {
        "model_manifest": {
            "manifest_version": "1",
            "model_id": "sales-model",
            "topology_layer": "spoke",
            "owner": "analytics",
            "contract_id": "contract.sales-model",
            "contract_version": "1.0.1",
            "published_contract_versions": ["1.0.0", "1.0.1"],
            "dependency_pins": {"shared-model": "1.0.0"},
            "dependencies": [],
        }
    }

    with pytest.raises(HTTPException) as exc:
        asyncio.run(contract_preflight("sales-model", payload))

    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert detail["message"].startswith("Breaking changes require deprecation_date")
    assert any("Dependency removed" in item for item in detail["result"]["breaking_changes"])


def test_contract_preflight_returns_unresolved_dependency_pins(monkeypatch):
    current_yaml = yaml.safe_dump(
        {
            "project_id": "sales-model",
            "model_manifest": {
                "manifest_version": "1",
                "model_id": "sales-model",
                "topology_layer": "spoke",
                "owner": "analytics",
                "contract_id": "contract.sales-model",
                "contract_version": "1.0.0",
                "published_contract_versions": ["1.0.0"],
                "dependency_pins": {"shared-model": "1.0.1"},
                "dependencies": ["shared-model"],
            },
        },
        sort_keys=False,
    )
    shared_yaml = yaml.safe_dump(
        {
            "project_id": "shared-model",
            "model_manifest": {
                "manifest_version": "1",
                "model_id": "shared-model",
                "topology_layer": "hub",
                "owner": "platform",
                "contract_id": "contract.shared-model",
                "contract_version": "2.0.0",
                "published_contract_versions": ["2.0.0"],
                "dependency_pins": {},
                "dependencies": [],
            },
        },
        sort_keys=False,
    )

    def _load_yaml(project_id: str) -> str:
        if project_id == "sales-model":
            return current_yaml
        if project_id == "shared-model":
            return shared_yaml
        return ""

    monkeypatch.setattr(
        "semabridge.api.controllers.versioning_controller._compat_load_project_yaml_text",
        _load_yaml,
    )

    payload = {
        "model_manifest": {
            "manifest_version": "1",
            "model_id": "sales-model",
            "topology_layer": "spoke",
            "owner": "analytics",
            "contract_id": "contract.sales-model",
            "contract_version": "1.0.0",
            "published_contract_versions": ["1.0.0"],
            "dependency_pins": {"shared-model": "1.0.1"},
            "dependencies": ["shared-model"],
            "deprecation_date": "",
        }
    }

    result = asyncio.run(contract_preflight("sales-model", payload))

    assert result["is_breaking"] is False
    assert result["unresolved_dependency_pins"] == {"shared-model": "1.0.1"}
    assert result["downstream_actions"]
    assert result["requires_deprecation_date"] is False