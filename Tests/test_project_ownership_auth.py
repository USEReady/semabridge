from __future__ import annotations

from contextlib import contextmanager
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.api.controllers import jobs_controller, mappings_controller, project_runs_controller, versioning_controller
from semabridge.api.services import project_ownership_service as pos
from semabridge.api.services import project_projects_impl as ppi


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return _FakeResult(self._rows)


class _FakeDBManager:
    def __init__(self, rows):
        self._rows = rows

    @contextmanager
    def get_session(self):
        yield _FakeSession(self._rows)


def test_ensure_project_owner_backfills_from_project_yaml(monkeypatch):
    saved_yaml = {}
    save_store_calls = []
    project = {"id": "proj-1", "project_id": "proj-1", "name": "P1"}

    monkeypatch.setattr(pos, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(pos, "_compat_projects", {"proj-1": project})
    monkeypatch.setattr(pos, "_compat_project_configs", {"proj-1": "owner_user_id: '11'\nproject_name: test\n"})
    monkeypatch.setattr(pos, "_compat_save_store", lambda: save_store_calls.append(True))
    monkeypatch.setattr(pos, "_compat_save_project_yaml_text", lambda project_id, yaml_text: saved_yaml.setdefault(project_id, yaml_text))

    context = pos.ensure_project_owner("proj-1")

    assert context["owner_user_id"] == "11"
    assert context["ownership_source"] == "project_yaml"
    assert context["recovered"] is True
    assert project["owner_user_id"] == "11"
    assert project["user_id"] == "11"
    assert save_store_calls == [True]
    assert "owner_user_id: '11'" in saved_yaml["proj-1"]


def test_ensure_project_owner_backfills_from_legacy_account_owner(monkeypatch):
    project = {
        "id": "proj-2",
        "project_id": "proj-2",
        "name": "P2",
        "source_account_id": "acct-1",
    }

    monkeypatch.setattr(pos, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(pos, "_compat_projects", {"proj-2": project})
    monkeypatch.setattr(pos, "_compat_project_configs", {})
    monkeypatch.setattr(pos, "_compat_save_store", lambda: None)
    monkeypatch.setattr(pos, "_compat_save_project_yaml_text", lambda project_id, yaml_text: None)
    monkeypatch.setattr(pos, "db_manager", _FakeDBManager([("acct-1", 42)]))

    context = pos.ensure_project_owner("proj-2")

    assert context["owner_user_id"] == "42"
    assert context["ownership_source"] == "legacy_account_owner"
    assert context["recovered"] is True
    assert project["owner_user_id"] == "42"


def test_is_project_owned_by_user_uses_direct_owner_only(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(
        pos,
        "ensure_project_owner",
        lambda project_id: {
            "project": {"id": project_id},
            "owner_user_id": "7",
            "ownership_source": "project_payload",
            "recovered": False,
        },
    )

    assert pos.is_project_owned_by_user("proj-1", "7") is True
    assert pos.is_project_owned_by_user("proj-1", "8", log_denied=False) is False


def test_project_run_route_requires_owned_project_and_injects_user_id(monkeypatch):
    captured = {}

    async def fake_run_project_now(project_id, background_tasks, body):
        captured["project_id"] = project_id
        captured["body"] = body
        return {"project_id": project_id, "status": "running"}

    monkeypatch.setattr(project_runs_controller, "require_request_user_id", lambda request: "7")
    monkeypatch.setattr(project_runs_controller, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(
        project_runs_controller,
        "is_project_owned_by_user",
        lambda project_id, user_id, **kwargs: project_id == "proj-1" and user_id == "7",
    )
    monkeypatch.setattr(project_runs_controller, "run_project_now_compat", fake_run_project_now)

    app = FastAPI()
    app.include_router(project_runs_controller.router)
    client = TestClient(app)

    allowed = client.post("/api/projects/proj-1/run", json={"force": True})
    denied = client.post("/api/projects/proj-2/run", json={"force": True})

    assert allowed.status_code == 200
    assert captured["project_id"] == "proj-1"
    assert captured["body"]["user_id"] == "7"
    assert denied.status_code == 403


def test_jobs_trigger_requires_owned_project_and_injects_user_id(monkeypatch):
    captured = {}

    async def fake_trigger_job(payload, background_tasks):
        captured["payload"] = payload
        return {"status": "queued"}

    monkeypatch.setattr(jobs_controller, "require_request_user_id", lambda request: "9")
    monkeypatch.setattr(jobs_controller, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(
        jobs_controller,
        "is_project_owned_by_user",
        lambda project_id, user_id, **kwargs: project_id == "proj-9" and user_id == "9",
    )
    monkeypatch.setattr(jobs_controller, "trigger_job_compat", fake_trigger_job)

    app = FastAPI()
    app.include_router(jobs_controller.router)
    client = TestClient(app)

    allowed = client.post("/api/jobs/trigger", json={"project_id": "proj-9"})
    denied = client.post("/api/jobs/trigger", json={"project_id": "proj-other"})

    assert allowed.status_code == 200
    assert captured["payload"]["user_id"] == "9"
    assert denied.status_code == 403


def test_version_control_stats_requires_owned_project(monkeypatch):
    async def fake_get_storage_stats(project_id: str):
        return {"project_id": project_id, "storage": 1}

    monkeypatch.setattr(versioning_controller, "require_request_user_id", lambda request: "5")
    monkeypatch.setattr(versioning_controller, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(
        versioning_controller,
        "is_project_owned_by_user",
        lambda project_id, user_id, **kwargs: project_id == "proj-5" and user_id == "5",
    )
    monkeypatch.setattr(versioning_controller.version_backend, "get_storage_stats", fake_get_storage_stats)

    app = FastAPI()
    app.include_router(versioning_controller.router)
    client = TestClient(app)

    allowed = client.get("/api/version-control/stats", params={"project_id": "proj-5"})
    denied = client.get("/api/version-control/stats", params={"project_id": "proj-6"})

    assert allowed.status_code == 200
    assert allowed.json()["project_id"] == "proj-5"
    assert denied.status_code == 403


def test_mappings_list_requires_owned_project(monkeypatch):
    async def fake_list_mappings(project_id):
        return {"project_id": project_id, "entity_mappings": []}

    monkeypatch.setattr(mappings_controller, "require_request_user_id", lambda request: "3")
    monkeypatch.setattr(mappings_controller, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(
        mappings_controller,
        "is_project_owned_by_user",
        lambda project_id, user_id, **kwargs: project_id == "proj-3" and user_id == "3",
    )
    monkeypatch.setattr(mappings_controller, "list_mappings_compat", fake_list_mappings)

    app = FastAPI()
    app.include_router(mappings_controller.router)
    client = TestClient(app)

    allowed = client.get("/api/mappings", params={"project_id": "proj-3"})
    denied = client.get("/api/mappings", params={"project_id": "proj-4"})

    assert allowed.status_code == 200
    assert allowed.json()["project_id"] == "proj-3"
    assert denied.status_code == 403


def test_preview_deploy_creates_owned_project_and_runs_with_user_id(monkeypatch):
    captured = {}

    async def fake_create_project(payload):
        captured["create_payload"] = dict(payload)
        return {"id": "proj-created", "project_id": "proj-created"}

    async def fake_run_project_now(project_id, background_tasks, payload):
        captured["run_project_id"] = project_id
        captured["run_payload"] = dict(payload)
        return {"run_id": "run-1", "status": "running"}

    monkeypatch.setattr(mappings_controller, "require_request_user_id", lambda request: "12")
    monkeypatch.setattr(mappings_controller, "auth_is_enabled", lambda: True)
    monkeypatch.setattr(
        mappings_controller,
        "is_project_owned_by_user",
        lambda project_id, user_id, **kwargs: project_id.startswith("preview-") or (project_id == "proj-created" and user_id == "12"),
    )
    monkeypatch.setattr(
        mappings_controller,
        "get_mapping_service",
        lambda: mappings_controller.MappingService(),
    )

    import semabridge.api.services.project_shared as project_shared
    monkeypatch.setattr(project_shared, "_compat_project_configs", {"preview-abc": "targets:\n  - type: snowflake\n"})
    monkeypatch.setattr(project_shared, "_compat_mappings", {})
    monkeypatch.setattr(project_shared, "_compat_save_store", lambda: None)
    monkeypatch.setattr(project_shared, "_compat_now_iso", lambda: "2026-05-25T00:00:00Z")

    import semabridge.api.services.project_domain_service as project_domain_service
    monkeypatch.setattr(project_domain_service, "create_project_compat", fake_create_project)
    monkeypatch.setattr(project_domain_service, "run_project_now_compat", fake_run_project_now)

    app = FastAPI()
    app.include_router(mappings_controller.router)
    client = TestClient(app)

    response = client.post(
        "/api/projects/preview-abc/deploy",
        json={"field_mappings": [{"id": "m1", "source_name": "Revenue", "target_name": "REVENUE"}]},
    )

    assert response.status_code == 200
    assert captured["create_payload"]["user_id"] == "12"
    assert captured["run_project_id"] == "proj-created"
    assert captured["run_payload"]["user_id"] == "12"


def test_normalize_project_config_yaml_persists_owner_metadata():
    yaml_text = "project_name: Demo\nsource:\n  type: fabric\n"

    normalized = ppi._normalize_project_config_yaml("proj-demo", yaml_text, "Demo", "21")

    assert "project_id: proj-demo" in normalized
    assert "owner_user_id: '21'" in normalized or 'owner_user_id: "21"' in normalized
    assert "project_metadata:" in normalized


def test_create_project_compat_avoids_stale_yaml_id_collision(monkeypatch):
    compat_projects = {}
    compat_configs = {}

    monkeypatch.setattr(ppi, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(ppi, "_compat_projects", compat_projects)
    monkeypatch.setattr(ppi, "_compat_project_configs", compat_configs)
    monkeypatch.setattr(ppi, "_compat_deleted_project_ids", set())
    monkeypatch.setattr(ppi, "_compat_project_runs", {})
    monkeypatch.setattr(ppi, "_compat_save_store", lambda: None)
    monkeypatch.setattr(ppi, "_compat_save_project_yaml_text", lambda project_id, yaml_text: None)
    monkeypatch.setattr(ppi, "_compat_load_project_yaml_text", lambda project_id: "project_name: stale\n" if project_id == "proj-dup" else "")
    monkeypatch.setattr(ppi, "_compat_load_repo_yaml_text", lambda: "")
    monkeypatch.setattr(ppi, "_upsert_project_in_orm", lambda project_id, project, payload: None)
    monkeypatch.setattr(ppi, "_clear_project_mapping_cache", lambda project_id: None)

    created = asyncio.run(
        ppi.create_project_compat(
            {
                "name": "dup",
                "user_id": "44",
                "source": {"type": "fabric"},
                "targets": [{"type": "snowflake"}],
            }
        )
    )

    assert created["id"].startswith("proj-dup-")
    assert created["owner_user_id"] == "44"
