"""Unit tests for run_service.get_run_report_data_compat -- the JSON
counterpart to get_run_report_compat, backing the new
/api/projects/{project_id}/runs/{run_id}/report-summary endpoint used by
the frontend's accordion-style run report summary view.
"""
from __future__ import annotations

import asyncio

from semabridge.api.services import run_service


def test_returns_none_when_no_matching_run_record(monkeypatch):
    monkeypatch.setattr(run_service, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(run_service, "_compat_project_runs", {})

    result = asyncio.run(run_service.get_run_report_data_compat("proj-1", "run-does-not-exist"))
    assert result is None


def test_returns_structured_data_for_a_matching_run(monkeypatch):
    monkeypatch.setattr(run_service, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(
        run_service,
        "_compat_project_runs",
        {
            "proj-1": [
                {
                    "run_id": "run-1",
                    "project_id": "proj-1",
                    "project_name": "My Project",
                    "status": "success",
                    "started_at": "2026-08-20T10:00:00Z",
                    "completed_at": "2026-08-20T10:01:00Z",
                    "duration_ms": 60000,
                    "results": [],
                    "summary": {},
                }
            ]
        },
    )
    monkeypatch.setattr(run_service.project_shared, "_compat_load_modular_project", lambda project_id: None)
    monkeypatch.setattr(run_service, "_compat_project_configs", {})
    monkeypatch.setattr(run_service, "_compat_load_repo_yaml_text", lambda: "")
    monkeypatch.setattr(run_service, "_compat_default_project_yaml", lambda project: "source:\n  type: pbix\n")
    monkeypatch.setattr(run_service, "_compat_projects", {"proj-1": {"name": "My Project"}})

    result = asyncio.run(run_service.get_run_report_data_compat("proj-1", "run-1"))

    assert result is not None
    assert result["run_id"] == "run-1"
    assert result["project_name"] == "My Project"
    assert result["crashed"] is True  # no results/summary.dropped_entities -> _gather_run_report_data's crashed path
