from __future__ import annotations

from types import SimpleNamespace

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

    monkeypatch.setattr(ses, "_write_content_if_provided", lambda payload, normalizer: None)
    monkeypatch.setattr(ses, "reload_settings", lambda: None)
    monkeypatch.setattr(ses, "_load_config", lambda normalizer: ("semabridge.yaml", {}))
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

    result = ses.execute_sync_request({}, lambda content: content)

    assert result["status"] == "success"
    assert result["routing_summary"] == routing_summary
    assert result["summary"]["routing_summary"] == routing_summary
    assert result["results"][0]["routing_summary"] == routing_summary


def test_execute_sync_request_returns_none_routing_summary_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(ses, "_write_content_if_provided", lambda payload, normalizer: None)
    monkeypatch.setattr(ses, "reload_settings", lambda: None)
    monkeypatch.setattr(ses, "_load_config", lambda normalizer: ("semabridge.yaml", {}))
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

    result = ses.execute_sync_request({}, lambda content: content)

    assert result["status"] == "success"
    assert result["routing_summary"] is None
