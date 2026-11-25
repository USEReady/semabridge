"""Tests verifying removed /sync routes return 410 Gone with replacement endpoints."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.api.sync_router import router


def test_removed_sync_routes_return_410_gone_with_replacement():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. POST /sync/jobs
    resp = client.post("/sync/jobs", json={})
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/sync" in body["replacement"]
    assert "/api/sync" in body["detail"]

    # 2. GET /sync/jobs
    resp = client.get("/sync/jobs")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/sync" in body["replacement"]

    # 3. GET /sync/jobs/{job_id}
    resp = client.get("/sync/jobs/job-123")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs" in body["replacement"]

    # 4. POST /sync/jobs/{job_id}/cancel
    resp = client.post("/sync/jobs/job-123/cancel")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs" in body["replacement"]

    # 5. POST /sync/jobs/{job_id}/resume
    resp = client.post("/sync/jobs/job-123/resume")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs" in body["replacement"]

    # 6. GET /sync/jobs/{job_id}/conflicts
    resp = client.get("/sync/jobs/job-123/conflicts")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs" in body["replacement"]

    # 7. GET /sync/conflicts
    resp = client.get("/sync/conflicts")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs/resolve" in body["replacement"]

    # 8. GET /sync/conflicts/{conflict_id}
    resp = client.get("/sync/conflicts/c-123")
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs/resolve" in body["replacement"]

    # 9. POST /sync/conflicts/{conflict_id}/resolve
    resp = client.post("/sync/conflicts/c-123/resolve", json={})
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs/resolve" in body["replacement"]

    # 10. PATCH /sync/jobs/{job_id}/conflicts/{conflict_id}
    resp = client.patch("/sync/jobs/job-123/conflicts/c-123", json={})
    assert resp.status_code == 410
    body = resp.json()
    assert "/api/runs/resolve" in body["replacement"]

