from __future__ import annotations

import os
import sys
import types

import pytest
from fastapi import HTTPException


os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_project_runs_dry_run.sqlite")

if "psycopg2" not in sys.modules:
    psycopg2_stub = types.ModuleType("psycopg2")
    psycopg2_stub.__version__ = "2.9.9"
    psycopg2_stub.apilevel = "2.0"
    psycopg2_stub.threadsafety = 2
    psycopg2_stub.paramstyle = "pyformat"
    psycopg2_stub.Error = Exception
    psycopg2_stub.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = psycopg2_stub
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services import project_runs_impl as pri
from semabridge.api.services import core_domain_service


@pytest.mark.asyncio
async def test_auto_map_dry_run_prefers_project_backed_mappings(monkeypatch):
    build_calls = []
    compat_calls = []

    def fake_build_entity_mappings(**kwargs):
        build_calls.append(kwargs)
        return {
            "project_id": kwargs["project_id"],
            "session_key": kwargs.get("session_key"),
            "model_name": kwargs["model"].get("unique_name"),
            "source_fields": [],
            "target_fields": [],
            "mappings": [],
            "collisions": [],
        }

    def fake_compat_build_project_entity_mappings(*args, **kwargs):
        compat_calls.append((args, kwargs))
        return {
            "project_id": args[0],
            "session_key": "compat-session",
            "model_name": "compat-model",
            "source_fields": [],
            "target_fields": [],
            "mappings": [],
            "collisions": [],
        }

    monkeypatch.setattr(pri, "build_entity_mappings", fake_build_entity_mappings)
    monkeypatch.setattr(pri, "_compat_build_project_entity_mappings", fake_compat_build_project_entity_mappings)
    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "legacy", "source": "pbix"}})

    payload = {
        "project_id": "project-1",
        "dry_run": True,
        "config_yaml": "project_name: map\nsource:\n  type: fabric\n  models:\n    - Device\ntarget:\n  type: snowflake\n",
        "selected_model_names": ["Device"],
        "target_connector": "snowflake",
    }

    result = await pri.auto_map_compat(payload)

    assert len(build_calls) == 0
    assert len(compat_calls) == 1
    assert compat_calls[0][0][0] == "project-1"
    assert compat_calls[0][1].get("selected_model_names") == ["Device"]
    assert result["project_id"] == "project-1"
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_run_project_now_blocks_when_dry_run_has_blockers(monkeypatch):
    class DummyBackgroundTasks:
        def __init__(self):
            self.calls = []

        def add_task(self, func, *args, **kwargs):
            self.calls.append((func, args, kwargs))

    async def fake_auto_map(payload):
        assert payload.get("dry_run") is True
        return {
            "entity_mappings": [
                {
                    "id": "m1",
                    "source_path": "datasets.orders.columns.customer_id",
                    "source_name": "customer_id",
                    "target_name": "",
                    "status": "unmapped",
                    "validation_status": "invalid",
                    "validation_code": "INVALID_TARGET_NAME",
                    "validation_message": "Target is required",
                }
            ]
        }

    def fail_create_run(*args, **kwargs):
        raise AssertionError("_create_project_run must not be called when blockers exist")

    monkeypatch.setattr(pri, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(pri, "auto_map_compat", fake_auto_map)
    monkeypatch.setattr(pri, "_create_project_run", fail_create_run)

    with pytest.raises(HTTPException) as exc_info:
        await pri.run_project_now_compat("project-1", DummyBackgroundTasks(), payload={"dry_run": False})

    assert exc_info.value.status_code == 409
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail.get("status") == "blocked"
    assert detail.get("mode") == "DRY_RUN"
    assert int(detail.get("blocking_issue_count") or 0) == 1


@pytest.mark.asyncio
async def test_run_project_now_starts_run_when_dry_run_has_no_blockers(monkeypatch):
    class DummyBackgroundTasks:
        def __init__(self):
            self.calls = []

        def add_task(self, func, *args, **kwargs):
            self.calls.append((func, args, kwargs))

    async def fake_auto_map(payload):
        assert payload.get("dry_run") is True
        return {
            "entity_mappings": [
                {
                    "id": "m1",
                    "source_path": "datasets.orders.columns.customer_id",
                    "source_name": "customer_id",
                    "target_name": "CUSTOMER_ID",
                    "status": "auto",
                    "validation_status": "valid",
                    "validation_code": "OK",
                    "validation_message": "",
                }
            ]
        }

    def fake_create_run(project_id, actor, **kwargs):
        return (
            {"id": "run-1", "run_id": "run-1"},
            "project_cfg",
            "2026-04-24T00:00:00Z",
        )

    monkeypatch.setattr(pri, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(pri, "auto_map_compat", fake_auto_map)
    monkeypatch.setattr(pri, "_create_project_run", fake_create_run)
    monkeypatch.setattr(pri, "_run_project_background", lambda *args, **kwargs: None)
    monkeypatch.setattr(pri, "_compat_load_modular_project", lambda project_id: None)
    monkeypatch.setattr(pri, "_compat_load_repo_yaml_text", lambda: "")
    monkeypatch.setattr(pri, "_compat_apply_manual_mapping_overrides_to_cfg", lambda cfg, project_id: cfg)
    monkeypatch.setattr(pri, "_compat_project_configs", {})
    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "p1"}})

    background_tasks = DummyBackgroundTasks()
    result = await pri.run_project_now_compat("project-1", background_tasks, payload={"dry_run": False})

    assert result["status"] == "running"
    assert result["run_id"] == "run-1"
    assert len(background_tasks.calls) == 1


@pytest.mark.asyncio
async def test_auto_map_dry_run_raises_when_presync_fails(monkeypatch):
    async def failing_sync_models(payload):
        raise RuntimeError("sync unavailable")

    monkeypatch.setattr(core_domain_service, "sync_models", failing_sync_models)
    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "p1"}})

    with pytest.raises(HTTPException) as exc_info:
        await pri.auto_map_compat({
            "project_id": "project-1",
            "dry_run": True,
        })

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_auto_map_skip_sync_flag_is_ignored_for_project_pipeline(monkeypatch):
    async def failing_sync_models(payload):
        raise RuntimeError("presync is always required")

    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "p1"}})
    from semabridge.api.services import core_domain_service
    monkeypatch.setattr(core_domain_service, "sync_models", failing_sync_models)

    with pytest.raises(HTTPException) as exc_info:
        await pri.auto_map_compat({
            "project_id": "project-1",
            "dry_run": True,
            "skip_sync": True,
        })

    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_auto_map_forwards_user_id_to_sync_models(monkeypatch):
    captured = {}

    async def fake_sync_models(payload):
        captured.update(payload)
        return {"status": "success", "summary": {}, "results": []}

    monkeypatch.setattr(pri, "_compat_projects", {"project-1": {"name": "p1"}})
    monkeypatch.setattr(pri, "_compat_build_project_entity_mappings", lambda *args, **kwargs: {
        "project_id": "project-1",
        "session_key": "s",
        "model_name": "m",
        "source_fields": [],
        "target_fields": [],
        "mappings": [],
        "collisions": [],
    })

    from semabridge.api.services import core_domain_service
    monkeypatch.setattr(core_domain_service, "sync_models", fake_sync_models)

    result = await pri.auto_map_compat({
        "project_id": "project-1",
        "dry_run": True,
        "user_id": 123,
    })

    assert result["status"] == "ok"
    assert captured.get("user_id") == 123


@pytest.mark.asyncio
async def test_perform_project_run_forwards_user_id_to_sync_models(monkeypatch):
    captured = {}

    async def fake_sync_models(payload):
        captured.update(payload)
        return {"status": "success", "summary": {}, "results": []}

    monkeypatch.setattr(pri, "_compat_save_store", lambda: None)
    monkeypatch.setattr(pri, "_build_run_logs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pri, "_build_stage_states", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(pri, "_compat_capture_snapshots_for_run", lambda **_kwargs: None)

    from semabridge.api.services import core_domain_service
    monkeypatch.setattr(core_domain_service, "sync_models", fake_sync_models)

    run = {
        "id": "run-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "summary": {},
        "user_id": 456,
    }
    await pri._perform_project_run(run, "project_name: p1\n", 0.0)

    assert captured.get("user_id") == 456


def test_scope_model_for_dry_run_keeps_metrics_for_selected_dataset():
    latest_model = {
        "unique_name": "demo",
        "datasets": [
            {"unique_name": "Sales", "columns": []},
            {"unique_name": "Inventory", "columns": []},
        ],
        "metrics": [
            {"unique_name": "SalesAmount", "expression": "SUM('Sales'[Amount])", "data_type": "decimal"},
            {"unique_name": "InventoryCount", "expression": "SUM('Inventory'[Qty])", "data_type": "decimal"},
        ],
    }

    scoped = pri._compat_scope_model_for_dry_run(latest_model, ["Sales"])
    metric_names = {str(m.get("unique_name")) for m in scoped.get("metrics", []) if isinstance(m, dict)}

    assert len(scoped.get("datasets", [])) == 1
    assert scoped["datasets"][0]["unique_name"] == "Sales"
    assert "SalesAmount" in metric_names
    assert "InventoryCount" not in metric_names


def test_preferred_snapshot_id_from_sync_result_prefers_selected_model():
    sync_result = {
        "summary": {"sml_snapshot_id": "snap-summary"},
        "results": [
            {"model": "Inventory", "summary": {"sml_snapshot_id": "snap-inventory"}},
            {"model": "Sales", "summary": {"sml_snapshot_id": "snap-sales"}},
        ],
    }

    sid = pri._compat_preferred_snapshot_id_from_sync_result(sync_result, ["Sales"])
    assert sid == "snap-sales"


def test_is_blocking_mapping_allows_resolved_name_collision():
    mapping = {
        "status": "auto",
        "target_name": "DAILY_DELIVERY_LD_RATE_1B32",
        "validation_status": "collision",
        "validation_code": "NAME_COLLISION",
        "validation_message": "Name collision resolved with deterministic suffix.",
    }
    assert pri._compat_is_blocking_mapping(mapping) is False


def test_latest_identifier_diagnostics_ignores_stale_older_runs(monkeypatch):
    monkeypatch.setattr(
        pri,
        "_compat_project_runs",
        {
            "project-1": [
                {
                    "id": "run-new",
                    "logs": ["INFO all good"],
                    "summary": {"errors": []},
                    "error": "",
                    "message": "Execution completed successfully.",
                },
                {
                    "id": "run-old",
                    "logs": ["ERROR SQL compilation: invalid identifier 'ORDERS.QUANTITY'"],
                    "summary": {"errors": []},
                    "error": "",
                    "message": "Execution failed.",
                },
            ]
        },
    )

    diagnostics = pri._compat_latest_identifier_diagnostics("project-1")
    assert diagnostics == []


def test_capture_snapshots_strips_runtime_metadata(monkeypatch):
    def has_runtime_keys(value):
        runtime_keys = {"initiated_by", "connector_id", "trigger", "trigger_by"}

        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key or "").strip().lower().replace(" ", "_").replace("-", "_")
                if normalized in runtime_keys:
                    return True
                if has_runtime_keys(child):
                    return True
        elif isinstance(value, list):
            for item in value:
                if has_runtime_keys(item):
                    return True
        return False

    monkeypatch.setattr(pri, "_compat_save_store", lambda: None)
    monkeypatch.setattr(pri, "_compat_project_snapshots", {"project-1": []})
    monkeypatch.setattr(pri, "_compat_snapshot_groups", {"project-1": []})
    monkeypatch.setattr(pri, "_compat_selected_intermediate_format", lambda _project_cfg: "sml")
    monkeypatch.setattr(
        pri,
        "_compat_latest_sml_state",
        lambda _project_id, _preferred_snapshot_id="": {
            "unique_name": "demo",
            "initiated_by": "api",
            "datasets": [
                {
                    "unique_name": "Sales",
                    "connector_id": "source-1",
                    "columns": [
                        {
                            "unique_name": "Revenue",
                            "trigger": "manual",
                        }
                    ],
                }
            ],
            "metrics": [
                {
                    "unique_name": "Total Revenue",
                    "trigger_by": "retry",
                }
            ],
        },
    )
    monkeypatch.setattr(
        pri,
        "_compat_connector_descriptors",
        lambda _project_cfg: {
            "source": {"connector_type": "fabric", "connector_identifier": "source-1"},
            "targets": [
                {"target_id": "target-1", "connector_type": "snowflake", "connector_identifier": "target-1"},
            ],
        },
    )

    run = {"id": "run-1", "run_id": "run-1", "sync_mode": "copy"}
    pri._compat_capture_snapshots_for_run(
        project_id="project-1",
        run=run,
        project_cfg="project_name: demo\n",
        stage="before",
    )

    assert run["before_src_snapshot_id"]
    assert run["before_target_snapshot_ids"]

    snapshots = pri._compat_project_snapshots["project-1"]
    assert snapshots
    assert all(not has_runtime_keys(snapshot.get("state")) for snapshot in snapshots)
