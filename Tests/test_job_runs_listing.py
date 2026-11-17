"""The Runs page lists only real project runs."""

import asyncio

from semabridge.api.services import schedule_service


def test_runs_page_lists_only_project_runs_of_real_projects(monkeypatch):
    projects = {
        "proj-real": {"id": "proj-real", "status": "active"},
        "preview-abc": {"id": "preview-abc", "status": "preview", "is_transient_preview": True},
    }
    runs_by_project = {
        "proj-real": [
            {"id": "run-1792050599247", "project_id": "proj-real", "started_at": "2026-10-15T07:48:00"},
            # A per-model engine row is never a listed run.
            {"id": "bb51c55d-65e0-4c1e", "project_id": "proj-real", "started_at": "2026-10-15T07:48:12",
             "run_kind": "model"},
            # New project runs carry a uuid id (one identity everywhere).
            {"id": "6f1c2a9e-1111-4c1e-9d55-000000000001", "project_id": "proj-real",
             "started_at": "2026-10-15T08:00:00Z", "run_kind": "project"},
        ],
        "preview-abc": [{"id": "run-1", "project_id": "preview-abc", "started_at": "2026-10-15T07:00:00"}],
        "proj-deleted": [{"id": "run-2", "project_id": "proj-deleted", "started_at": "2026-10-15T06:00:00"}],
    }

    async def fake_runs(project_id):
        return runs_by_project.get(project_id, [])

    monkeypatch.setattr(schedule_service, "_compat_projects", projects)
    monkeypatch.setattr(schedule_service, "_compat_project_runs", runs_by_project)
    monkeypatch.setattr(schedule_service, "_compat_ensure_loaded", lambda: None)
    import semabridge.api.services.run_service as run_service
    monkeypatch.setattr(run_service, "get_project_runs_compat", fake_runs)
    monkeypatch.setattr(run_service, "_sweep_stale_runs", lambda *a, **k: None)

    runs = asyncio.run(schedule_service.list_job_runs_compat())

    assert [r["id"] for r in runs] == ["6f1c2a9e-1111-4c1e-9d55-000000000001", "run-1792050599247"]
