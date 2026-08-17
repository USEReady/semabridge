from __future__ import annotations

from pathlib import Path
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


def test_build_sync_jobs_accepts_pbix_path():
    source_cfg = {"type": "pbix", "pbix_path": "C:/Sales.pbix"}
    jobs, source_type, target_type, resolved_source_cfg, resolved_target_cfg = ses._build_sync_jobs(
        {"source": source_cfg, "targets": [{"type": "snowflake"}]}
    )
    assert source_type == "pbix"
    assert target_type == "snowflake"
    assert len(jobs) == 1
    assert jobs[0]["pbix_path"] == "C:/Sales.pbix"
    assert jobs[0]["model_label"] == "Sales"


def test_build_sync_jobs_raises_validation_error_on_missing_pbix_path():
    from semabridge.domain.exceptions import ValidationError
    source_cfg = {"type": "pbix"}
    with pytest.raises(ValidationError, match="PBIX source requires source.pbix_path or source.models."):
        ses._build_sync_jobs(
            {"source": source_cfg, "targets": [{"type": "snowflake"}]}
        )


def test_build_sync_jobs_resolves_multi_pbix_full_paths_without_glob(tmp_path):
    """Multi-PBIX source.models entries are full absolute paths; _build_sync_jobs
    must resolve them via its existing literal-path exact-match branch (lines
    ~224-234) with zero code changes — this is the integration point the
    multi-PBIX feature relies on, so it must be proven, not assumed.
    """
    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Marketing Analysis.pbix"
    file_a.write_bytes(b"fake-pbix-a")
    file_b.write_bytes(b"fake-pbix-b")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b)]}
    jobs, source_type, target_type, resolved_source_cfg, resolved_target_cfg = ses._build_sync_jobs(
        {"source": source_cfg, "targets": [{"type": "snowflake"}]}
    )

    assert source_type == "pbix"
    assert len(jobs) == 2
    assert jobs[0]["pbix_path"] == str(file_a.resolve())
    assert jobs[1]["pbix_path"] == str(file_b.resolve())
    # Each job carries its own distinct path — proves the jobs are independently
    # addressable, which is what per-file parallel dry-run/deploy (Part B/C)
    # needs from this layer.
    assert jobs[0]["pbix_path"] != jobs[1]["pbix_path"]


def test_build_sync_jobs_multi_pbix_never_invokes_glob_fallback(tmp_path, monkeypatch):
    """Literal full-path resolution must not depend on the glob fallback at all.

    Asserts Path.glob is never invoked when a model entry is already a full,
    existing path — proving the multi-PBIX feature avoids the non-deterministic
    "first match wins" glob heuristic entirely, by construction.
    """
    file_a = tmp_path / "Report One.pbix"
    file_a.write_bytes(b"fake-pbix")

    def _tracking_glob(self, pattern):
        raise AssertionError(
            f"Path.glob() must not be called for a literal full-path model entry (pattern={pattern!r})"
        )

    monkeypatch.setattr(Path, "glob", _tracking_glob)

    source_cfg = {"type": "pbix", "models": [str(file_a)]}
    jobs, *_ = ses._build_sync_jobs({"source": source_cfg, "targets": [{"type": "snowflake"}]})
    assert jobs[0]["pbix_path"] == str(file_a.resolve())


def test_build_sync_jobs_rejects_more_than_max_batch_models(tmp_path):
    """Confirms the pre-existing MAX_BATCH_MODELS cap (10) still applies once
    models are full paths, not just stems — the multi-PBIX UI's 1-10 file cap
    (Part C) is enforced here too, not only by pbix_source_validation.
    """
    from semabridge.domain.exceptions import ValidationError

    files = []
    for i in range(ses.MAX_BATCH_MODELS + 1):
        f = tmp_path / f"Report {i}.pbix"
        f.write_bytes(b"x")
        files.append(str(f))

    source_cfg = {"type": "pbix", "models": files}
    with pytest.raises(ValidationError, match="Batch sync currently supports up to"):
        ses._build_sync_jobs({"source": source_cfg, "targets": [{"type": "snowflake"}]})


def test_build_config_yaml_from_request_propagates_pbix_path():
    import yaml
    from semabridge.api.controllers.mappings_controller import _build_config_yaml_from_request
    
    source_config = {
        "type": "pbix",
        "pbix_path": "C:/Reports/Sales.pbix",
        "pbix_folder": "C:/Reports"
    }
    target_config = {
        "type": "snowflake",
        "database": "DB",
        "schema": "SCH"
    }
    
    config_yaml = _build_config_yaml_from_request(
        source_config=source_config,
        target_config=target_config,
        selected_sources=[]
    )
    
    config = yaml.safe_load(config_yaml)
    assert config["source"]["type"] == "pbix"
    assert config["source"]["pbix_path"] == "C:/Reports/Sales.pbix"
    assert config["source"]["pbix_folder"] == "C:/Reports"

