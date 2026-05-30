from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.api.controllers import projects_controller


def _build_client(monkeypatch) -> TestClient:
    async def _fake_compare(
        project_id: str,
        from_snapshot_id: str,
        to_snapshot_id: str,
        max_changes: int = 200,
        include_states: bool = False,
    ):
        return {
            "project_id": project_id,
            "from_snapshot_id": from_snapshot_id,
            "to_snapshot_id": to_snapshot_id,
            "max_changes": max_changes,
            "include_states": include_states,
        }

    async def _fake_list_snapshots(
        project_id: str,
        role=None,
        stage=None,
        origin=None,
        run_id=None,
        group_id=None,
        include_state=False,
        limit=200,
    ):
        return {
            "project_id": project_id,
            "role": role,
            "stage": stage,
            "origin": origin,
            "run_id": run_id,
            "group_id": group_id,
            "include_state": include_state,
            "limit": limit,
        }

    async def _fake_list_snapshot_groups(project_id: str, limit: int = 200):
        return {"project_id": project_id, "limit": limit}

    async def _fake_capture_snapshots(project_id: str, payload: dict):
        return {"project_id": project_id, "payload": payload}

    monkeypatch.setattr(projects_controller, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(projects_controller, "auth_is_enabled", lambda: False)
    monkeypatch.setattr(
        projects_controller,
        "compare_project_snapshots_compat",
        _fake_compare,
    )
    monkeypatch.setattr(
        projects_controller,
        "list_project_snapshots_compat",
        _fake_list_snapshots,
    )
    monkeypatch.setattr(
        projects_controller,
        "list_snapshot_groups_compat",
        _fake_list_snapshot_groups,
    )
    monkeypatch.setattr(
        projects_controller,
        "capture_manual_snapshots_compat",
        _fake_capture_snapshots,
    )

    app = FastAPI()
    app.include_router(projects_controller.router)
    return TestClient(app)


def test_compare_snapshots_accepts_current_query_param_names(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get(
        "/api/projects/proj-123/snapshots/compare",
        params={"from_snapshot_id": "snap-a", "to_snapshot_id": "snap-b"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "proj-123",
        "from_snapshot_id": "snap-a",
        "to_snapshot_id": "snap-b",
        "max_changes": 200,
        "include_states": False,
    }


def test_compare_snapshots_accepts_legacy_query_param_names(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get(
        "/api/projects/proj-123/snapshots/compare",
        params={"s1": "snap-old-a", "s2": "snap-old-b"},
    )

    assert response.status_code == 200
    assert response.json()["from_snapshot_id"] == "snap-old-a"
    assert response.json()["to_snapshot_id"] == "snap-old-b"


def test_compare_snapshots_forwards_optional_compare_flags(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get(
        "/api/projects/proj-123/snapshots/compare",
        params={
            "from_snapshot_id": "snap-a",
            "to_snapshot_id": "snap-b",
            "max_changes": "150",
            "include_states": "true",
        },
    )

    assert response.status_code == 200
    assert response.json()["max_changes"] == 150
    assert response.json()["include_states"] is True


def test_compare_snapshots_returns_validation_details_when_params_missing(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get(
        "/api/projects/proj-123/snapshots/compare",
        params={"from_snapshot_id": "snap-a"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["expected"]["from_snapshot_id"] == [
        "from_snapshot_id",
        "base_id",
        "s1",
    ]
    assert payload["detail"]["expected"]["to_snapshot_id"] == [
        "to_snapshot_id",
        "target_id",
        "s2",
    ]
    assert payload["detail"]["actual"] == {"from_snapshot_id": "snap-a"}


def test_list_project_snapshots_forwards_query_params(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get(
        "/api/projects/proj-123/snapshots",
        params={
            "role": "source",
            "stage": "before",
            "origin": "RUN_BEFORE",
            "run_id": "run-1",
            "group_id": "sg-1",
            "include_state": "true",
            "limit": "25",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "project_id": "proj-123",
        "role": "source",
        "stage": "before",
        "origin": "RUN_BEFORE",
        "run_id": "run-1",
        "group_id": "sg-1",
        "include_state": True,
        "limit": 25,
    }


def test_list_snapshot_groups_forwards_limit(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.get(
        "/api/projects/proj-123/snapshot-groups",
        params={"limit": "10"},
    )

    assert response.status_code == 200
    assert response.json() == {"project_id": "proj-123", "limit": 10}


def test_capture_snapshots_forwards_payload(monkeypatch):
    client = _build_client(monkeypatch)

    response = client.post(
        "/api/projects/proj-123/snapshots/capture",
        json={"scope": {"source": True}, "label": "manual"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == "proj-123"
    payload = body["payload"]
    assert payload["scope"] == {"source": True}
    assert payload["label"] == "manual"
