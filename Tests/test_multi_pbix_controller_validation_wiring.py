"""Confirms both live request boundaries actually call the shared multi-PBIX
validator BEFORE doing any real work (project persistence / pipeline dispatch),
rather than merely asserting the validator function works in isolation.

Covers:
  - POST /api/projects (CreateProjectRequest) -> projects_controller.create_project
  - POST /api/projects/{id}/dry-run (DryRunRequest) -> mappings_controller.dry_run_mapping
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_multi_pbix_wiring.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.domain.exceptions import ValidationError


def test_create_project_rejects_bad_models_before_touching_compat_store(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc

    called = {"create_project_compat": False}

    async def _fake_create_project_compat(request_dict):
        called["create_project_compat"] = True
        return {"id": "should-not-get-here"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create_project_compat)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="multi-pbix-test",
        source={"type": "pbix", "models": ["not-a-pbix-path"]},
    )

    with pytest.raises(ValidationError):
        asyncio.run(pc.create_project(object(), payload))

    assert called["create_project_compat"] is False, (
        "create_project_compat() must not run when source.models fails validation — "
        "otherwise a bad multi-file request could partially persist before failing."
    )


def test_create_project_allows_valid_models_through_to_compat_store(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc

    called = {}

    async def _fake_create_project_compat(request_dict):
        called["source"] = request_dict.get("source")
        return {"id": "proj-ok"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create_project_compat)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="multi-pbix-test",
        source={
            "type": "pbix",
            "models": ["C:/Reports/Sales.pbix", "C:/Reports/Marketing.pbix"],
        },
    )

    result = asyncio.run(pc.create_project(object(), payload))

    assert result == {"id": "proj-ok"}
    assert called["source"]["models"] == [
        "C:/Reports/Sales.pbix",
        "C:/Reports/Marketing.pbix",
    ]


def test_dry_run_mapping_rejects_bad_selected_sources_before_running_pipeline(monkeypatch):
    import semabridge.api.controllers.mappings_controller as mc

    called = {"sync_models": False}

    async def _fake_sync_models(payload):
        called["sync_models"] = True
        return {"status": "success", "results": []}

    # sync_models is imported lazily inside dry_run_mapping via
    # `from semabridge.api.services.core_domain_service import sync_models`,
    # so patch it at that source module.
    import semabridge.api.services.core_domain_service as core_domain_service

    monkeypatch.setattr(core_domain_service, "sync_models", _fake_sync_models)
    monkeypatch.setattr(mc, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(mc, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)

    request = mc.DryRunRequest(
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=["SalesReport"],  # bare stem, not a full .pbix path — invalid
    )

    class _FakeService:
        pass

    response = asyncio.run(mc.dry_run_mapping("preview", object(), request, service=_FakeService()))

    assert called["sync_models"] is False, (
        "sync_models() (the real extraction/OSI/SML pipeline) must not run when "
        "selected_sources fails multi-PBIX validation."
    )
    assert response.status_code == 400
    import json
    body = json.loads(response.body)
    assert body["success"] is False
    assert "does not look like a .pbix file" in body["error"]
