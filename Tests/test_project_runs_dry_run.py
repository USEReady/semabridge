from __future__ import annotations

import os
import sys
import types

import pytest


os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_project_runs_dry_run.sqlite")

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
async def test_auto_map_dry_run_prefers_yaml_preview_model(monkeypatch):
    build_calls = []
    compat_calls = []

    def fake_build_entity_mappings(**kwargs):
        build_calls.append(kwargs)
        return {
            "project_id": kwargs["project_id"],
            "session_key": kwargs.get("session_key"),
            "model_name": kwargs["model"].get("unique_name"),
            "source_fields": [],
            "target_fields": [],
            "mappings": [],
            "collisions": [],
        }

    def fake_compat_build_project_entity_mappings(*args, **kwargs):
        compat_calls.append((args, kwargs))
        return {
            "project_id": args[0],
            "session_key": "compat-session",
            "model_name": "compat-model",
            "source_fields": [],
            "target_fields": [],
            "mappings": [],
            "collisions": [],
        }

    monkeypatch.setattr(pri, "build_entity_mappings", fake_build_entity_mappings)
    monkeypatch.setattr(pri, "_compat_build_project_entity_mappings", fake_compat_build_project_entity_mappings)
    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "legacy", "source": "pbix"}})

    payload = {
        "project_id": "project-1",
        "dry_run": True,
        "config_yaml": "project_name: map\nsource:\n  type: fabric\n  models:\n    - Device\ntarget:\n  type: snowflake\n",
        "selected_model_names": ["Device"],
        "target_connector": "snowflake",
    }

    result = await pri.auto_map_compat(payload)

    assert len(build_calls) == 1
    assert len(compat_calls) == 0
    assert build_calls[0]["project_id"].startswith("preview-")
    assert build_calls[0]["model"]["unique_name"] == "map"
    assert [dataset["unique_name"] for dataset in build_calls[0]["model"]["datasets"]] == ["Device"]
    assert result["project_id"].startswith("preview-")
    assert result["status"] == "ok"
