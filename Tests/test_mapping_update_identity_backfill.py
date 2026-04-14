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


@pytest.mark.asyncio
async def test_update_mapping_backfills_identity_when_missing(monkeypatch):
    mapping_id = "project-1-abc123"

    monkeypatch.setattr(pri, "_compat_mappings", {})

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

    monkeypatch.setattr(pri, "_compat_build_project_entity_mappings", fake_build)

    updated = await pri.update_mapping_compat(mapping_id, {"project_id": "project-1", "target_name": "REV_TOTAL"})

    assert updated["id"] == mapping_id
    assert updated["source_path"] == "metrics.Revenue"
    assert updated["target_name"] == "REV_TOTAL"
    assert updated["is_user_edited"] is True
