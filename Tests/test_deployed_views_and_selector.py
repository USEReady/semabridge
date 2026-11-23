"""
Tests for Deployed Views and Multi-View Selector.

Covers:
  1. list_project_deployments: Project with two deployments lists newest first with is_latest=True on newest.
  2. validate_project_view: Accepts deployed views or views in project schema; raises ValueError on unknown view.
  3. GET /api/projects/{project_id}/deployed-views endpoint returns deployments list with is_latest on newest.
  4. Endpoints default to the latest view and accept an explicit view parameter:
     - /api/drift/check & /api/drift/events
     - /api/reconciliation/project-metrics
     - /api/ai/nl-query
     - /api/bi-truth/generate & /api/bi-truth/run
  5. Unknown view -> 400 with descriptive error message across endpoints.
"""

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SEMABRIDGE_ALLOW_LOCAL_DB", "true")
os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///file::memory:?cache=shared&uri=true")
os.environ.setdefault("SEMABRIDGE_DB_BACKEND", "orm")

from semabridge.repository.model_repository import ModelRepository
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.project_models import Project
from semabridge.repository.orm.snapshot_models import SnapshotRow
from semabridge.repository.orm.run_models import Run


@pytest.fixture(autouse=True)
def init_test_db():
    repo = ModelRepository()
    engine = repo._engine
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def sample_project_with_two_deployments():
    repo = ModelRepository()
    pid = f"proj-multi-view-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    t_older = now - timedelta(days=18)
    t_newer = now - timedelta(hours=11)

    with repo._session() as session:
        proj = Project(
            project_id=pid,
            name="HR Multi-View Project",
            database="SEMABRIDGE",
            schema="HUMAN_RESOURCES",
            adapter="snowflake",
        )
        session.add(proj)

        # Older deployment (18 days ago)
        run1 = Run(run_id=f"run-{uuid.uuid4().hex[:6]}", project_id=pid, started_at=t_older, run_type="SYNC", status="success", target_type="snowflake")
        session.add(run1)
        snap1 = SnapshotRow(
            snapshot_id=f"snap-{uuid.uuid4().hex[:6]}",
            project_id=pid,
            timestamp=t_older,
            deployed_at=t_older,
            status="success",
            target_database="SEMABRIDGE",
            target_schema="HUMAN_RESOURCES",
            view_name="HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC",
            run_id=run1.run_id,
        )
        session.add(snap1)

        # Newer deployment (11 hours ago)
        run2 = Run(run_id=f"run-{uuid.uuid4().hex[:6]}", project_id=pid, started_at=t_newer, run_type="SYNC", status="success", target_type="snowflake")
        session.add(run2)
        snap2 = SnapshotRow(
            snapshot_id=f"snap-{uuid.uuid4().hex[:6]}",
            project_id=pid,
            timestamp=t_newer,
            deployed_at=t_newer,
            status="success",
            target_database="SEMABRIDGE",
            target_schema="HUMAN_RESOURCES",
            view_name="HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC",
            run_id=run2.run_id,
        )
        session.add(snap2)
        session.commit()

    return pid


def test_list_project_deployments_order_and_is_latest(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    repo = ModelRepository()

    deployments = repo.list_project_deployments(pid, include_unmanaged=False)
    assert len(deployments) == 2

    # Newest must be first
    assert deployments[0]["view"] == "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC"
    assert deployments[0]["is_latest"] is True
    assert deployments[0]["managed"] is True
    assert deployments[0]["database"] == "SEMABRIDGE"
    assert deployments[0]["schema"] == "HUMAN_RESOURCES"
    assert deployments[0]["platform"] == "snowflake"

    # Older deploy must be second with is_latest=False
    assert deployments[1]["view"] == "HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC"
    assert deployments[1]["is_latest"] is False
    assert deployments[1]["managed"] is True


def test_validate_project_view_accepts_valid_and_rejects_unknown(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    repo = ModelRepository()

    # Deployed view 1
    val1 = repo.validate_project_view(pid, "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC")
    assert "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC" in val1

    # Deployed view 2 (older)
    val2 = repo.validate_project_view(pid, "HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC")
    assert "HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC" in val2

    # View matching project's schema
    val3 = repo.validate_project_view(pid, '"SEMABRIDGE"."HUMAN_RESOURCES"."OTHER_CUSTOM_VIEW"')
    assert "OTHER_CUSTOM_VIEW" in val3

    # Unknown view not in project or schema
    with pytest.raises(ValueError) as exc_info:
        repo.validate_project_view(pid, '"FOREIGN_DB"."FOREIGN_SCHEMA"."UNKNOWN_VIEW"')
    assert "neither deployed by project" in str(exc_info.value)


def test_api_deployed_views_endpoint(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    from semabridge.api.main import app

    client = TestClient(app)
    with patch("semabridge.api.controllers.projects_controller._assert_project_access"):
        res = client.get(f"/api/projects/{pid}/deployed-views?include_unmanaged=false")
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 2
        assert data[0]["is_latest"] is True
        assert data[0]["view"] == "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC"
        assert data[1]["is_latest"] is False
        assert data[1]["view"] == "HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC"


def test_drift_endpoints_default_and_view_parameter(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    from semabridge.api.main import app

    client = TestClient(app)

    # 1. Default when omitted: uses latest successful deploy
    res_default = client.get(f"/api/drift/events?project_id={pid}")
    assert res_default.status_code == 200
    d_data = res_default.json()
    assert "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC" in str(d_data.get("view"))

    # 2. Passing older view explicitly: checks and returns that view
    res_older = client.get(f"/api/drift/events?project_id={pid}&view=HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC")
    assert res_older.status_code == 200
    o_data = res_older.json()
    assert "HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC" in str(o_data.get("view"))

    # 3. Unknown view: returns 400
    res_bad = client.get(f"/api/drift/events?project_id={pid}&view=UNKNOWN_FOREIGN_VIEW")
    assert res_bad.status_code == 400
    assert "neither deployed by project" in res_bad.json().get("detail", "")

    # 4. POST /api/drift/check rejects unknown view with 400
    res_bad_post = client.post("/api/drift/check", json={"project_id": pid, "view": "UNKNOWN_VIEW"})
    assert res_bad_post.status_code == 400


def test_reconciliation_endpoints_default_and_view_parameter(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    from semabridge.api.main import app

    client = TestClient(app)

    # 1. Default when omitted
    res_default = client.get(f"/api/reconciliation/project-metrics?project_id={pid}")
    assert res_default.status_code == 200
    d_data = res_default.json()
    assert "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC" in str(d_data.get("view"))

    # 2. Older view
    res_older = client.get(f"/api/reconciliation/project-metrics?project_id={pid}&view=HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC")
    assert res_older.status_code == 200
    o_data = res_older.json()
    assert "HUMAN_RESOURCES_SAMPLE_PBIX_1_SEMANTIC" in str(o_data.get("view"))

    # 3. Unknown view -> 400
    res_bad = client.get(f"/api/reconciliation/project-metrics?project_id={pid}&view=UNKNOWN_VIEW")
    assert res_bad.status_code == 400
    assert "neither deployed by project" in res_bad.json().get("detail", "")


def test_ai_nl_query_default_and_view_parameter(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    from semabridge.api.main import app

    client = TestClient(app)
    with patch("semabridge.api.routers.ai_router._require_project_access"):
        # Unknown view -> 400
        res_bad = client.post(
            "/api/ai/nl-query",
            json={"prompt": "What is total employees?", "project_id": pid, "view": "INVALID_UNKNOWN_VIEW"},
        )
        assert res_bad.status_code == 400
        assert "neither deployed by project" in res_bad.json().get("detail", "")

        # Valid view passed
        res_valid = client.post(
            "/api/ai/nl-query",
            json={
                "prompt": "What is total employees?",
                "project_id": pid,
                "view": "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC",
                "allow_offline_draft": True,
            },
        )
        assert res_valid.status_code == 200
        plan = res_valid.json()
        assert "HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC" in str(plan.get("view") or plan.get("answered_by_view"))


def test_bi_truth_endpoints_default_and_view_parameter(sample_project_with_two_deployments):
    pid = sample_project_with_two_deployments
    from semabridge.api.main import app

    client = TestClient(app)

    # 1. Unknown view in generate -> 400
    res_bad_gen = client.post(
        "/api/bi-truth/generate",
        json={"project_id": pid, "view": "UNKNOWN_VIEW"},
    )
    assert res_bad_gen.status_code == 400
    assert "neither deployed by project" in res_bad_gen.json().get("detail", "")

    # 2. Unknown view in run -> 400
    res_bad_run = client.post(
        "/api/bi-truth/run",
        json={"project_id": pid, "view": "UNKNOWN_VIEW"},
    )
    assert res_bad_run.status_code == 400
    assert "neither deployed by project" in res_bad_run.json().get("detail", "")
