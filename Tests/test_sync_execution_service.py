from __future__ import annotations

from types import SimpleNamespace

import pytest

from semabridge.api.services import sync_execution_service as ses


def _make_routing_summary() -> dict[str, int]:
    return {
        "source_table_count": 3,
        "fact_table_count": 1,
        "dimension_table_count": 2,
        "bridge_table_count": 0,
        "review_required_count": 1,
        "generated_artifact_count": 1,
    }


def test_execute_sync_request_exposes_routing_summary(monkeypatch) -> None:
    routing_summary = _make_routing_summary()

    monkeypatch.setattr(ses, "reload_settings", lambda: None)
    monkeypatch.setattr(ses, "_load_config", lambda payload, normalizer: ("semabridge.yaml", {}))
    monkeypatch.setattr(
        ses,
        "_build_sync_jobs",
        lambda config: (
            [{"dataset_id": "model-1", "pbix_path": None, "model_label": "model-1"}],
            "fabric",
            "databricks",
            {"workspace_id": "ws-1"},
            {"deploy": True},
        ),
    )
    monkeypatch.setattr(
        ses,
        "get_settings",
        lambda: SimpleNamespace(fabric=SimpleNamespace(workspace_id="ws-from-settings")),
    )
    monkeypatch.setattr(ses, "_resolve_requested_parallelism", lambda payload: 1)
    monkeypatch.setattr(ses, "_resolve_executor_kind", lambda payload, is_fabric_bound: "thread")
    monkeypatch.setattr(
        ses,
        "_resolve_effective_parallelism",
        lambda **kwargs: 1,
    )
    monkeypatch.setattr(
        ses,
        "_run_parallel_jobs",
        lambda **kwargs: [
            {
                "model": "model-1",
                "status": "success",
                "summary": {"status": "SUCCESS", "routing_summary": routing_summary},
                "routing_summary": routing_summary,
                "console": {"lines": []},
                "run_id": "run-1",
            }
        ],
    )

    result = ses.execute_sync_request({}, lambda content, *args: content)

    assert result["status"] == "success"
    assert result["routing_summary"] == routing_summary
    assert result["summary"]["routing_summary"] == routing_summary
    assert result["results"][0]["routing_summary"] == routing_summary


def test_execute_sync_request_returns_none_routing_summary_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(ses, "reload_settings", lambda: None)
    monkeypatch.setattr(ses, "_load_config", lambda payload, normalizer: ("semabridge.yaml", {}))
    monkeypatch.setattr(
        ses,
        "_build_sync_jobs",
        lambda config: (
            [{"dataset_id": "model-1", "pbix_path": None, "model_label": "model-1"}],
            "fabric",
            "databricks",
            {"workspace_id": "ws-1"},
            {"deploy": True},
        ),
    )
    monkeypatch.setattr(
        ses,
        "get_settings",
        lambda: SimpleNamespace(fabric=SimpleNamespace(workspace_id="ws-from-settings")),
    )
    monkeypatch.setattr(ses, "_resolve_requested_parallelism", lambda payload: 1)
    monkeypatch.setattr(ses, "_resolve_executor_kind", lambda payload, is_fabric_bound: "thread")
    monkeypatch.setattr(
        ses,
        "_resolve_effective_parallelism",
        lambda **kwargs: 1,
    )
    monkeypatch.setattr(
        ses,
        "_run_parallel_jobs",
        lambda **kwargs: [
            {
                "model": "model-1",
                "status": "success",
                "summary": {"status": "SUCCESS"},
                "routing_summary": None,
                "console": {"lines": []},
                "run_id": "run-1",
            }
        ],
    )

    result = ses.execute_sync_request({}, lambda content, *args: content)

    assert result["status"] == "success"
    assert result["routing_summary"] is None


def test_execute_sync_request_forwards_requested_project_id(monkeypatch) -> None:
    monkeypatch.setattr(ses, "reload_settings", lambda: None)
    monkeypatch.setattr(ses, "_load_config", lambda payload, normalizer: ("semabridge.yaml", {}))
    monkeypatch.setattr(
        ses,
        "_build_sync_jobs",
        lambda config: (
            [{"dataset_id": "Competitive Marketing Analysis", "pbix_path": None, "model_label": "Competitive Marketing Analysis"}],
            "fabric",
            "snowflake",
            {"workspace_id": "ws-1"},
            {"deploy": True},
        ),
    )
    monkeypatch.setattr(
        ses,
        "get_settings",
        lambda: SimpleNamespace(fabric=SimpleNamespace(workspace_id="ws-from-settings")),
    )
    monkeypatch.setattr(ses, "_resolve_requested_parallelism", lambda payload: 1)
    monkeypatch.setattr(ses, "_resolve_executor_kind", lambda payload, is_fabric_bound: "thread")
    monkeypatch.setattr(ses, "_resolve_effective_parallelism", lambda **kwargs: 1)

    captured: dict[str, object] = {}

    def _fake_run_parallel_jobs(**kwargs):
        captured.update(kwargs)
        return [
            {
                "model": "Competitive Marketing Analysis",
                "status": "success",
                "summary": {"status": "SUCCESS"},
                "routing_summary": None,
                "console": {"lines": []},
                "run_id": "run-1",
            }
        ]

    monkeypatch.setattr(ses, "_run_parallel_jobs", _fake_run_parallel_jobs)

    ses.execute_sync_request({"project_id": "proj-comp-marketing"}, lambda content, *args: content)

    assert captured.get("requested_project_id") == "proj-comp-marketing"


@pytest.mark.parametrize(
    "source_cfg, expected_models",
    [
        ({"type": "fabric", "model": "LegacyModel"}, ["LegacyModel"]),
        ({"type": "fabric", "models": ["ModelA", "ModelB"]}, ["ModelA", "ModelB"]),
    ],
)
def test_build_sync_jobs_accepts_fabric_model_fallbacks(source_cfg, expected_models):
    jobs, source_type, target_type, resolved_source_cfg, resolved_target_cfg = ses._build_sync_jobs(
        {"source": source_cfg, "targets": [{"type": "snowflake"}]}
    )

    assert source_type == "fabric"
    assert target_type == "snowflake"
    assert resolved_source_cfg == source_cfg
    assert resolved_target_cfg == {"type": "snowflake"}
    assert [job["dataset_id"] for job in jobs] == expected_models
