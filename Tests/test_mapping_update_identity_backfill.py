from __future__ import annotations

import os
import sys
import types

import pytest


os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_mapping_update_identity_backfill.sqlite")

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
async def test_update_mapping_backfills_identity_when_missing(monkeypatch):
    mapping_id = "project-1-abc123"

    _patch_compat(monkeypatch, "_compat_mappings", {})

    def fake_build(project_id: str, save_store: bool = True, target_connector=None):
        assert project_id == "project-1"
        return {
            "project_id": project_id,
            "session_key": "session",
            "model_name": "model",
            "source_fields": [],
            "target_fields": [],
            "collisions": [],
            "mappings": [
                {
                    "id": mapping_id,
                    "project_id": project_id,
                    "entity_kind": "metric",
                    "source_name": "Revenue",
                    "source_path": "metrics.Revenue",
                    "target_name": "REVENUE",
                    "is_user_edited": False,
                }
            ],
        }

    _patch_compat(monkeypatch, "_compat_build_project_entity_mappings", fake_build)

    updated = await pri.update_mapping_compat(mapping_id, {"project_id": "project-1", "target_name": "REV_TOTAL"})

    assert updated["id"] == mapping_id
    assert updated["source_path"] == "metrics.Revenue"
    assert updated["target_name"] == "REV_TOTAL"
    assert updated["is_user_edited"] is True
