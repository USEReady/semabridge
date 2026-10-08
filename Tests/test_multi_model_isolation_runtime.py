"""Multi-model project isolation: runtime behavior.

(e) post-deploy reconciliation runs per model, (f) concurrent runs never see
each other's env credentials, plus per-model result shape and the
process-executor retry keeping sync_mode/force.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from types import SimpleNamespace

import pytest

from semabridge.api.services import run_service
from semabridge.api.services import sync_execution_service as ses
from semabridge.auth import user_credentials


# ---------------------------------------------------------------------------
# (e) reconciliation per model
# ---------------------------------------------------------------------------


def _model_result(model: str, snapshot_id: str, view_name: str) -> dict:
    summary = {
        "status": "SUCCESS",
        "target_type": "snowflake",
        "sml_snapshot_id": snapshot_id,
        "run_id": f"run-{model}",
        "dropped_entities": [],
    }
    return {
        "model": model,
        "status": "success",
        "summary": summary,
        "console": {"lines": ["[SUCCESS] Step 9: Deploy"], "text": ""},
        "run_id": f"run-{model}",
        "dropped_entities": summary["dropped_entities"],
        "missing_dims": {},
        "pbix_path": None,
        "view_name": view_name,
        "target_artifact_path": None,
    }


def _patch_run_pipeline(monkeypatch, results):
    async def _fake_sync_models(payload):
        return {
            "status": "success",
            "models_synced": len(results),
            "total_models": len(results),
            "results": results,
            "summary": results[-1]["summary"],
        }

    import semabridge.api.services.core_domain_service as cds
    import semabridge.api.services.snapshot_service as snap
    import semabridge.api.services.run_report_service as rrs

    monkeypatch.setattr(cds, "sync_models", _fake_sync_models)
    monkeypatch.setattr(snap, "_compat_capture_snapshots_for_run", lambda **kw: None)
    monkeypatch.setattr(rrs, "write_run_report", lambda *a, **k: None)
    monkeypatch.setattr(rrs, "write_per_model_run_reports", lambda *a, **k: None)
    monkeypatch.setattr(run_service, "_compat_save_store", lambda: None)
    monkeypatch.setattr(run_service, "_run_retention_background", lambda *a, **k: None)


def test_multi_model_run_reconciles_each_model_against_its_own_view(monkeypatch):
    results = [_model_result("A", "snap-a", "REGION_A_SEMANTIC"), _model_result("B", "snap-b", "REGION_B_SEMANTIC")]
    _patch_run_pipeline(monkeypatch, results)

    fetched_views = []
    metrics_by_snapshot = {"snap-a": ["TotalA"], "snap-b": ["TotalB", "Lost"]}
    ddl_by_view = {
        "REGION_A_SEMANTIC": "METRICS (\n  FACT.TOTALA AS SUM(FACT.AMOUNT)\n)",
        "REGION_B_SEMANTIC": "METRICS (\n  FACT.TOTALB AS SUM(FACT.AMOUNT)\n)",
    }

    class _Repo:
        def get_snapshot(self, snapshot_id):
            return SimpleNamespace(sml_blob={"metrics": [{"unique_name": n} for n in metrics_by_snapshot[snapshot_id]]})

    def _fake_fetch(project_id, view_name):
        fetched_views.append(view_name)
        return ddl_by_view[view_name]

    monkeypatch.setattr("semabridge.repository.model_repository.ModelRepository", _Repo)
    monkeypatch.setattr("semabridge.core.reconciliation._fetch_live_deployed_ddl", _fake_fetch)

    def _must_not_be_called(run_id):
        raise AssertionError("run-level reconcile_run must not be used for a multi-model run")

    monkeypatch.setattr("semabridge.core.reconciliation.reconcile_run", _must_not_be_called)

    run = {"id": "run-1", "project_id": "proj-1"}
    out = asyncio.run(run_service._perform_project_run(run, "source: {type: pbix}\n", time.time()))

    assert sorted(fetched_views) == ["REGION_A_SEMANTIC", "REGION_B_SEMANTIC"]
    rec_a = out["results"][0]["reconciliation"]
    rec_b = out["results"][1]["reconciliation"]
    for rec in (rec_a, rec_b):
        assert set(rec) == {"is_clean", "unaccounted", "deployed_live_count", "deployed_dead_count", "ddl_error"}
    assert rec_a["is_clean"] is True
    assert rec_b["is_clean"] is False and "LOST" in {k.upper() for k in rec_b["unaccounted"]}
    # Unaccounted metric lands on model B only, exactly once.
    b_drops = [d for d in out["results"][1]["dropped_entities"] if d["stage"] == "reconciliation"]
    assert len(b_drops) == 1
    assert not [d for d in out["results"][0]["dropped_entities"] if d["stage"] == "reconciliation"]
    # Backward-compatible top-level summary still carries reconciliation.
    assert out["summary"]["reconciliation"] == rec_b


def test_single_model_run_keeps_run_level_reconciliation(monkeypatch):
    results = [_model_result("A", "snap-a", "REGION_A_SEMANTIC")]
    _patch_run_pipeline(monkeypatch, results)
    calls = []

    def _fake_reconcile_run(run_id):
        calls.append(run_id)
        return SimpleNamespace(
            is_clean=lambda: True, unaccounted={}, deployed_live_metrics={"X"},
            deployed_dead_metrics=set(), ddl_error=None, snapshot_id="snap-a",
        )

    monkeypatch.setattr("semabridge.core.reconciliation.reconcile_run", _fake_reconcile_run)
    run = {"id": "run-1", "project_id": "proj-1"}
    out = asyncio.run(run_service._perform_project_run(run, "source: {type: pbix}\n", time.time()))

    assert calls == ["run-1"]
    assert out["summary"]["reconciliation"]["deployed_live_count"] == 1
    assert out["results"][0]["reconciliation"] == out["summary"]["reconciliation"]


# ---------------------------------------------------------------------------
# (f) concurrent runs never see each other's env credentials
# ---------------------------------------------------------------------------


def test_concurrent_scoped_user_env_never_leaks_between_users(monkeypatch):
    keys = {1: "key-user-1", 2: "key-user-2"}
    monkeypatch.setattr(
        user_credentials, "get_user_credentials",
        lambda user_id, service, db=None: {"OPENAI_API_KEY": keys[user_id]},
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    errors: list[str] = []
    inside = {"count": 0, "max": 0}
    lock = threading.Lock()
    start = threading.Barrier(2)

    def _worker(user_id):
        start.wait()
        with user_credentials.scoped_user_env(user_id, "api_secrets"):
            with lock:
                inside["count"] += 1
                inside["max"] = max(inside["max"], inside["count"])
            for _ in range(20):
                seen = os.environ.get("OPENAI_API_KEY")
                if seen != keys[user_id]:
                    errors.append(f"user {user_id} saw {seen}")
                time.sleep(0.002)
            with lock:
                inside["count"] -= 1

    threads = [threading.Thread(target=_worker, args=(uid,)) for uid in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    assert inside["max"] == 1  # different credentials never overlap
    assert "OPENAI_API_KEY" not in os.environ  # restored afterwards


def test_same_user_concurrent_scopes_run_in_parallel(monkeypatch):
    monkeypatch.setattr(
        user_credentials, "get_user_credentials",
        lambda user_id, service, db=None: {"OPENAI_API_KEY": "shared"},
    )
    overlap = threading.Event()
    entered = threading.Barrier(2, timeout=5)

    def _worker():
        with user_credentials.scoped_user_env(7, "api_secrets"):
            entered.wait()  # both must be inside at the same time
            overlap.set()

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert overlap.is_set()


# ---------------------------------------------------------------------------
# Per-model result shape, unique labels, retry plumbing
# ---------------------------------------------------------------------------


class _FakeSummary:
    def __init__(self, data):
        self._data = data

    def model_dump(self, mode="json"):
        return dict(self._data)


def test_run_single_job_result_carries_per_model_fields(monkeypatch):
    captured = {}

    class _FakeEngine:
        def __init__(self, db_manager=None):
            self._context = None

        def execute(self, **kwargs):
            captured.update(kwargs)
            self._context = SimpleNamespace(
                deployed_view_name="REGION_A_SEMANTIC",
                target_artifact_path="output/reverse/p/REGION_A/semantic_view.sql",
                sml_model=None,
            )
            return _FakeSummary({
                "status": "SUCCESS",
                "run_id": "r-1",
                "steps_completed": [{"step_number": 9, "step_name": "Deploy", "status": "success"}],
                "errors": [],
                "target_artifact_path": "output/reverse/p/REGION_A/semantic_view.sql",
            })

    monkeypatch.setattr(ses, "ExecutionEngine", _FakeEngine)
    monkeypatch.setattr(ses, "ModelRepository", lambda: None)
    job = {
        "dataset_id": None, "pbix_path": "/x/Region A.pbix", "model_label": "Region A",
        "physical_namespace": "REGION_A", "batch_model_count": 2, "resolved_display_name": "REGION_A",
    }
    result = ses._run_single_job(
        job, engine_source_type="pbix", target_type="snowflake", config={},
        config_path="", deploy_enabled=False, resolved_workspace_id="local",
    )
    assert captured["physical_namespace"] == "REGION_A"
    assert captured["batch_model_count"] == 2
    assert result["view_name"] == "REGION_A_SEMANTIC"
    assert result["target_artifact_path"].endswith("REGION_A/semantic_view.sql")
    assert result["console"]["lines"] and isinstance(result["duration_ms"], int)
    for key in ("model", "status", "summary", "console", "run_id", "dropped_entities", "missing_dims", "pbix_path"):
        assert key in result


def test_engine_exception_result_has_error_in_console(monkeypatch):
    class _BoomEngine:
        def __init__(self, db_manager=None):
            pass

        def execute(self, **kwargs):
            raise RuntimeError("boom: warehouse unreachable")

    monkeypatch.setattr(ses, "ExecutionEngine", _BoomEngine)
    monkeypatch.setattr(ses, "ModelRepository", lambda: None)
    result = ses._run_single_job(
        {"dataset_id": "d", "pbix_path": None, "model_label": "M"},
        engine_source_type="fabric", target_type="snowflake", config={},
        config_path="", deploy_enabled=True, resolved_workspace_id="ws",
    )
    assert result["status"] == "failed"
    assert any("warehouse unreachable" in line for line in result["console"]["lines"])
    assert result["dropped_entities"] == [] and result["view_name"] is None


def test_process_executor_retry_keeps_sync_mode_and_force(monkeypatch):
    calls = []

    def _fake_run_parallel_jobs(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("process pool broken")
        return [{"model": "m", "status": "success", "summary": {}, "console": {"lines": []}, "run_id": "r"}]

    monkeypatch.setattr(ses, "_load_config", lambda payload, normalizer: ("semabridge.yaml", {}))
    monkeypatch.setattr(
        ses, "_build_sync_jobs",
        lambda config: ([{"dataset_id": None, "pbix_path": "a.pbix", "model_label": "m"}], "pbix", "snowflake", {}, {"deploy": True}),
    )
    monkeypatch.setattr(ses, "get_settings", lambda: SimpleNamespace(fabric=SimpleNamespace(workspace_id="")))
    monkeypatch.setattr(ses, "_resolve_executor_kind", lambda payload, is_fabric_bound: "process")
    monkeypatch.setattr(ses, "_resolve_effective_parallelism", lambda **kwargs: 2)
    monkeypatch.setattr(ses, "_run_parallel_jobs", _fake_run_parallel_jobs)

    ses.execute_sync_request({"sync_mode": "upsert", "force": True}, lambda c, *a: c)

    assert len(calls) == 2
    assert calls[1]["executor_kind"] == "thread"
    assert calls[1]["sync_mode"] == "upsert"
    assert calls[1]["force"] is True
