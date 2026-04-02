from __future__ import annotations

import time
from types import SimpleNamespace

from semabridge.api.services import sync_execution_service as service


def test_build_console_details_includes_steps_and_errors():
    summary_data = {
        "steps_completed": [
            {
                "step_number": 4,
                "step_name": "Extract from Source",
                "status": "success",
                "message": "Extraction complete",
            },
            {
                "step_number": 9,
                "step_name": "Deploy to Target",
                "status": "failed",
                "message": "Deployment failed",
            },
        ],
        "errors": [
            {
                "step_number": 9,
                "step_name": "Deploy to Target",
                "message": "warehouse timeout",
            }
        ],
    }

    console = service._build_console_details(summary_data)

    assert console["lines"][0] == "[SUCCESS] Step 4: Extract from Source - Extraction complete"
    assert console["lines"][1] == "[FAILED] Step 9: Deploy to Target - Deployment failed"
    assert console["lines"][2] == "[ERROR] Step 9: Deploy to Target - warehouse timeout"
    assert "warehouse timeout" in console["text"]


def test_execute_sync_request_caps_fabric_parallelism_and_preserves_job_order(monkeypatch):
    config = {
        "source": {"type": "fabric", "models": ["model_a", "model_b", "model_c", "model_d"]},
        "target": {"type": "snowflake", "deploy": True},
        "version_tag": "v1",
    }

    monkeypatch.setattr(service, "_write_content_if_provided", lambda payload, normalize: None)
    monkeypatch.setattr(service, "reload_settings", lambda: None)
    monkeypatch.setattr(service, "_load_config", lambda normalize: ("semabridge.yaml", config))
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(fabric=SimpleNamespace(workspace_id="ws1")))

    sleep_map = {"model_a": 0.03, "model_b": 0.01, "model_c": 0.02, "model_d": 0.0}

    def fake_run_single_job(job, **kwargs):
        time.sleep(sleep_map[job["model_label"]])
        return {
            "model": job["model_label"],
            "status": "success",
            "summary": {"run_id": f"run-{job['model_label']}", "status": "SUCCESS"},
            "console": {"lines": [job["model_label"]], "text": job["model_label"]},
            "run_id": f"run-{job['model_label']}",
        }

    monkeypatch.setattr(service, "_run_single_job", fake_run_single_job)

    result = service.execute_sync_request(
        {"max_parallel_models": 10},
        lambda content: content,
    )

    assert result["status"] == "success"
    assert result["models_synced"] == 4
    assert result["batch"]["requested_parallelism"] == 10
    assert result["batch"]["effective_parallelism"] == 3
    assert result["batch"]["fabric_limited"] is True
    assert [item["model"] for item in result["results"]] == ["model_a", "model_b", "model_c", "model_d"]
