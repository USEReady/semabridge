from __future__ import annotations

import os
import sys
import types

import pytest


os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_mapping_identifier_diagnostics.sqlite")

if "psycopg2" not in sys.modules:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_stub.__version__ = "2.9.9"
    psycopg2_stub.apilevel = "2.0"
    psycopg2_stub.threadsafety = 2
    psycopg2_stub.paramstyle = "pyformat"
    psycopg2_stub.Error = Exception
    psycopg2_stub.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services import project_runs_impl as pri


# project_runs_impl is a re-export shim (4203016 split it into
# mapping_service / run_service / snapshot_service / ... and project_shared).
# The functions read their globals from the module that defines them, so a
# patch on the shim alone is invisible to them: patch every module that has
# the name.
from semabridge.api.services import (  # noqa: E402
    folder_service as _folder_service,
    mapping_service as _mapping_service,
    project_shared as _project_shared,
    run_service as _run_service,
    schedule_service as _schedule_service,
    snapshot_service as _snapshot_service,
    version_service as _version_service,
)

_COMPAT_MODULES = (
    pri,
    _project_shared,
    _mapping_service,
    _run_service,
    _snapshot_service,
    _version_service,
    _folder_service,
    _schedule_service,
)


def _patch_compat(monkeypatch, name, value):
    patched = False
    for module in _COMPAT_MODULES:
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
            patched = True
    assert patched, f"no compat module defines {name}"


@pytest.mark.asyncio
async def test_auto_map_attaches_invalid_identifier_diagnostics(monkeypatch):
    _patch_compat(monkeypatch, "_compat_projects", {"project-1": {"name": "Core_Finance_v1", "source": "fabric"}})
    from semabridge.api.services import core_domain_service
    async def fake_sync_models(payload):
        return {"status": "success", "summary": {}, "results": []}

    monkeypatch.setattr(core_domain_service, "sync_models", fake_sync_models)
    _patch_compat(monkeypatch, "_compat_project_runs",
        {
            "project-1": [
                {
                    "status": "failed",
                    "logs": [
                        "ERROR Stage 9 (Deploy to Target) - Deployment failed: 000904 (42000): SQL compilation error: invalid identifier 'ORDERS.QUANTITY'"
                    ],
                }
            ]
        },
    )

    def fake_build_project_entity_mappings(project_id: str, save_store: bool = True, target_connector=None, **kwargs):
        return {
            "project_id": project_id,
            "session_key": "session",
            "model_name": "Core_Finance_v1",
            "source_fields": [],
            "target_fields": [],
            "collisions": [],
            "mappings": [
                {
                    "id": "m1",
                    "project_id": project_id,
                    "entity_kind": "metric",
                    "source_name": "FABRICMODEL_DATA_SUM_OF_QUANTITY",
                    "source_path": "metrics.FABRICMODEL_DATA_SUM_OF_QUANTITY",
                    "source_expression": "SUM('ORDERS'[QUANTITY])",
                    "target_name": "FABRICMODEL_DATA_SUM_OF_QUANTITY",
                    "is_user_edited": False,
                    "collision_detected": False,
                    "validation_status": "valid",
                    "validation_code": "OK",
                    "validation_message": "",
                }
            ],
        }

    _patch_compat(monkeypatch, "_compat_build_project_entity_mappings", fake_build_project_entity_mappings)

    payload = {
        "project_id": "project-1",
        "dry_run": True,
        "config_yaml": "project_name: Core_Finance_v1\nsource:\n  type: fabric\n  workspace_id: ws-1\n",
        "target_connector": "snowflake",
    }

    result = await pri.auto_map_compat(payload)

    assert result["status"] == "ok"
    assert result.get("diagnostics")
    metric = result["entity_mappings"][0]
    assert metric["validation_code"] == "INVALID_IDENTIFIER_REFERENCE"
    assert metric["validation_status"] == "invalid"
    assert metric["collision_detected"] is True
    assert "ORDERS.QUANTITY" in metric["validation_message"]


def test_apply_identifier_diagnostics_ignores_target_name_only_matches():
    mappings = [
        {
            "id": "m1",
            "entity_kind": "metric",
            "source_name": "Orders - Sum of Revenue",
            "source_path": "metrics.Orders - Sum of Revenue",
            "source_expression": "SUM('ORDERS'[REVENUE])",
            "target_name": "ORDERS_SUM_OF_QUANTITY2",
            "collision_detected": False,
            "validation_status": "valid",
            "validation_code": "OK",
            "validation_message": "",
        }
    ]
    diagnostics = [
        {
            "code": "INVALID_IDENTIFIER_REFERENCE",
            "actual_identifier": "ORDERS.QUANTITY",
            "source_hint": "QUANTITY",
            "message": "Deploy SQL references invalid identifier ORDERS.QUANTITY.",
        }
    ]

    pri._compat_apply_identifier_diagnostics_to_mappings(mappings, diagnostics)

    metric = mappings[0]
    assert metric["validation_code"] == "OK"
    assert metric["validation_status"] == "valid"
    assert metric["collision_detected"] is False
