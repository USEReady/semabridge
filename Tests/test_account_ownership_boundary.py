from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account, Project, User
from semabridge.repository.orm.session_factory import db_manager, reset_engine


@pytest.fixture(autouse=True)
def setup_auth_and_db(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-jwt-secret-key-32-chars-long-1234")
    db_file = tmp_path / "test_ownership.db"
    monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("SEMABRIDGE_DB_BACKEND", "orm")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-bytes-long-1234")

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)

    with db_manager.get_session() as session:
        # Create user A, user B, admin user
        user_a = User(id=1, email="user_a@example.com", username="User A", password_hash="dummy", role="user")
        user_b = User(id=2, email="user_b@example.com", username="User B", password_hash="dummy", role="user")
        admin = User(id=3, email="admin@example.com", username="Admin", password_hash="dummy", role="admin")
        session.add_all([user_a, user_b, admin])

        # Create account owned by User A
        acct_a = Account(
            id="acct_user_a",
            connector_type="FABRIC",
            tag="fabric_a",
            identity_email="user_a@example.com",
            status="Active",
            owner_id="1",
        )
        session.add(acct_a)
        session.commit()

    yield

    reset_engine()
    db_manager.reset()


def _auth_headers(client: TestClient, user_id: int, role: str = "user") -> dict[str, str]:
    from semabridge.auth.tokens import create_access_token

    csrf_token = f"csrf-token-user-{user_id}"
    client.cookies.set("csrf_token", csrf_token)

    token = create_access_token(
        {"sub": str(user_id), "email": f"user{user_id}@example.com", "role": role}
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf_token,
    }


def test_user_b_creating_project_with_user_a_account_forbidden():
    client = TestClient(app)
    fabric_mock = MagicMock()

    with patch("semabridge.api.controllers.projects_controller._validate_fabric_models_on_save", fabric_mock):
        response = client.post(
            "/api/projects",
            headers=_auth_headers(client, user_id=2, role="user"),
            json={
                "name": "Project B",
                "source": {
                    "type": "fabric",
                    "workspace_id": "ws-123",
                    "identity_id": "acct_user_a",
                    "models": ["ModelA"],
                },
            },
        )

    assert response.status_code == 403
    assert "Forbidden: connector identities belong to another user" in response.text
    # Fabric client / validation is NEVER called
    fabric_mock.assert_not_called()


def test_user_b_saving_config_with_user_a_account_forbidden():
    # Setup project owned by User B
    with db_manager.get_session() as session:
        proj = Project(
            project_id="proj_b",
            name="Project B",
            owner_user_id="2",
            workspace_id="ws-123",
        )
        session.add(proj)
        session.commit()

    client = TestClient(app)
    fabric_mock = MagicMock()

    with patch("semabridge.api.controllers.projects_controller._validate_fabric_models_on_save", fabric_mock):
        response = client.put(
            "/api/projects/proj_b/config",
            headers=_auth_headers(client, user_id=2, role="user"),
            json={
                "config_yaml": (
                    "source:\n"
                    "  type: fabric\n"
                    "  workspace_id: ws-123\n"
                    "  identity_id: acct_user_a\n"
                    "  models:\n"
                    "    - ModelA\n"
                ),
            },
        )

    assert response.status_code == 403
    assert "Forbidden: connector identities belong to another user" in response.text
    fabric_mock.assert_not_called()


def test_user_b_patching_project_with_user_a_account_forbidden():
    with db_manager.get_session() as session:
        proj = Project(
            project_id="proj_b_patch",
            name="Project B Patch",
            owner_user_id="2",
            workspace_id="ws-123",
        )
        session.add(proj)
        session.commit()

    client = TestClient(app)
    response = client.patch(
        "/api/projects/proj_b_patch",
        headers=_auth_headers(client, user_id=2, role="user"),
        json={
            "source": {
                "type": "fabric",
                "identity_id": "acct_user_a",
            },
        },
    )
    assert response.status_code == 403
    assert "Forbidden: connector identities belong to another user" in response.text


def test_user_b_using_user_a_account_in_target_forbidden():
    client = TestClient(app)
    response = client.post(
        "/api/projects",
        headers=_auth_headers(client, user_id=2, role="user"),
        json={
            "name": "Project Target Check",
            "source": {"type": "pbix"},
            "targets": [
                {"type": "snowflake", "account_id": "acct_user_a"}
            ],
        },
    )
    assert response.status_code == 403
    assert "Forbidden: connector identities belong to another user" in response.text


def test_owner_and_admin_succeed():
    client = TestClient(app)
    fabric_mock = MagicMock()

    with patch("semabridge.api.controllers.projects_controller._validate_fabric_models_on_save", fabric_mock):
        # 1. Owner (User A) creates project with own account
        res_a = client.post(
            "/api/projects",
            headers=_auth_headers(client, user_id=1, role="user"),
            json={
                "name": "Project A",
                "source": {
                    "type": "fabric",
                    "workspace_id": "ws-123",
                    "identity_id": "acct_user_a",
                    "models": ["ModelA"],
                },
            },
        )
        assert res_a.status_code == 200
        assert fabric_mock.called

    fabric_mock.reset_mock()

    with patch("semabridge.api.controllers.projects_controller._validate_fabric_models_on_save", fabric_mock):
        # 2. Admin creates project with User A's account
        res_admin = client.post(
            "/api/projects",
            headers=_auth_headers(client, user_id=3, role="admin"),
            json={
                "name": "Project Admin",
                "source": {
                    "type": "fabric",
                    "workspace_id": "ws-123",
                    "identity_id": "acct_user_a",
                    "models": ["ModelA"],
                },
            },
        )
        assert res_admin.status_code == 200
        assert fabric_mock.called

    # 3. Owner and Admin can patch project with owner's account
    with db_manager.get_session() as session:
        proj = Project(
            project_id="proj_a_patch",
            name="Project A Patch",
            owner_user_id="1",
            workspace_id="ws-123",
        )
        session.add(proj)
        session.commit()

    res_patch_owner = client.patch(
        "/api/projects/proj_a_patch",
        headers=_auth_headers(client, user_id=1, role="user"),
        json={"source": {"identity_id": "acct_user_a"}},
    )
    assert res_patch_owner.status_code == 200

    res_patch_admin = client.patch(
        "/api/projects/proj_a_patch",
        headers=_auth_headers(client, user_id=3, role="admin"),
        json={"source": {"identity_id": "acct_user_a"}},
    )
    assert res_patch_admin.status_code == 200

