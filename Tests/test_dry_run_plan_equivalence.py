"""The dry run must plan each model exactly like the sync deploys it.

Both paths compute a model's view name / physical prefix with
sync_execution_service._build_sync_jobs: the sync on the project's config,
the dry run on mappings_controller._dry_run_config (the project's own config
with only the dry-run file substituted), scoped to that one model.
"""
from __future__ import annotations

import yaml
import pytest

from semabridge.api.controllers import mappings_controller as mc
from semabridge.api.services import sync_execution_service as ses
from semabridge.utils.project_models import assign_model_view_names

HEX_A = "0" * 32
HEX_B = "f" * 32


def _write(path, data=b"pbix-bytes"):
    path.write_bytes(data)
    return str(path)


@pytest.fixture()
def multi_project(tmp_path):
    files = [
        _write(tmp_path / f"{HEX_A}_Sales.pbix", b"sales"),
        _write(tmp_path / f"{HEX_A}_Sales_EU.pbix", b"sales-eu"),
        _write(tmp_path / f"{HEX_A}_Finance.pbix", b"finance"),
    ]
    source = {"type": "pbix", "mode": "multiple", "models": list(files)}
    source["model_views"] = assign_model_view_names(source)
    cfg = {
        "project_name": "p",
        "source": source,
        "targets": [{"type": "snowflake", "database": "DB", "schema": "SCH"}],
        "mappings_overrides": [{"source_path": "datasets.T.columns.C", "target_name": "X", "model": files[0]}],
        "options": {"auto_relationships": True},
    }
    return cfg, files


def _scoped_job(cfg, scope):
    jobs, _st, tt, _s, _t = ses._build_sync_jobs(cfg)
    hit = [j for j in jobs if any(ses.job_matches_source_ref(j, r) for r in scope)]
    assert len(hit) == 1
    return hit[0], tt


def test_dry_run_plan_equals_sync_plan_for_every_model(multi_project):
    cfg, files = multi_project
    sync_jobs, _st, tt, _s, _t = ses._build_sync_jobs(cfg)
    assert len({j["physical_namespace"] for j in sync_jobs}) == 3
    for sync_job in sync_jobs:
        dcfg, scope = mc._dry_run_config(
            project_cfg=cfg, source_config={"type": "pbix"}, target_config=cfg["targets"][0],
            selected_sources=[sync_job["source_ref"]], keep_saved_target=True,
        )
        assert dcfg["source"]["mode"] == "multiple"
        assert dcfg["mappings_overrides"] == cfg["mappings_overrides"]
        job, dtt = _scoped_job(dcfg, scope)
        assert ses.model_plan_for_job(job, dtt) == ses.model_plan_for_job(sync_job, tt)
        assert ses.model_config_fingerprint(dcfg, job, dtt) == ses.model_config_fingerprint(cfg, sync_job, tt)


def test_reupload_copy_gets_the_configured_models_plan(multi_project, tmp_path):
    cfg, files = multi_project
    copy = _write(tmp_path / f"{HEX_B}_Sales_EU.pbix", b"sales-eu")
    dcfg, scope = mc._dry_run_config(
        project_cfg=cfg, source_config={"type": "pbix"}, target_config={}, selected_sources=[copy],
        keep_saved_target=True,
    )
    assert copy in dcfg["source"]["models"] and files[1] not in dcfg["source"]["models"]
    job, tt = _scoped_job(dcfg, scope)
    sync_job = [j for j in ses._build_sync_jobs(cfg)[0] if j["source_ref"] == files[1]][0]
    assert job["resolved_display_name"] == sync_job["resolved_display_name"] == "SALES_EU"
    assert job["physical_namespace"] == sync_job["physical_namespace"]


def test_new_model_is_appended_and_named_like_project_save(multi_project, tmp_path):
    cfg, files = multi_project
    new = _write(tmp_path / f"{HEX_B}_Marketing.pbix", b"mkt")
    dcfg, scope = mc._dry_run_config(
        project_cfg=cfg, source_config={"type": "pbix"}, target_config={}, selected_sources=[new],
        keep_saved_target=True,
    )
    assert dcfg["source"]["models"][-1] == new
    job, _tt = _scoped_job(dcfg, scope)
    assert job["resolved_display_name"] == "MARKETING"
    # Existing models keep their names.
    assert {k: v for k, v in dcfg["source"]["model_views"].items() if k != new} == cfg["source"]["model_views"]


def test_wizard_multi_model_dry_run_uses_multi_mode_naming(tmp_path):
    files = [_write(tmp_path / f"{HEX_A}_Sales.pbix"), _write(tmp_path / f"{HEX_B}_Sales.pbix", b"other")]
    dcfg, scope = mc._dry_run_config(
        project_cfg={}, source_config={"type": "pbix"}, target_config={"type": "snowflake"},
        selected_sources=[files[1]], all_sources=files,
    )
    assert dcfg["source"]["mode"] == "multiple"
    # The names project creation will save (assign_model_view_names, same order).
    saved = assign_model_view_names({"type": "pbix", "mode": "multiple", "models": files})
    assert dcfg["source"]["model_views"] == saved
    job, _tt = _scoped_job(dcfg, scope)
    assert job["resolved_display_name"] == saved[files[1]] == "SALES_2"
    assert job["physical_namespace"]


def test_single_file_wizard_dry_run_stays_single_mode():
    dcfg, scope = mc._dry_run_config(
        project_cfg={}, source_config={"type": "pbix"}, target_config={"type": "snowflake"},
        selected_sources=["/x/Sales.pbix"],
    )
    assert "mode" not in dcfg["source"] and scope == []


def test_single_mode_project_keeps_its_saved_config(tmp_path):
    path = _write(tmp_path / f"{HEX_A}_Sales.pbix")
    cfg = {"source": {"type": "pbix", "pbix_path": path}, "targets": [{"type": "snowflake", "database": "D", "schema": "S"}]}
    upload = _write(tmp_path / f"{HEX_B}_Sales.pbix")
    dcfg, scope = mc._dry_run_config(
        project_cfg=cfg, source_config={"type": "pbix"}, target_config={}, selected_sources=[upload],
        keep_saved_target=True,
    )
    job, _tt = _scoped_job(dcfg, scope)
    assert job["multi_mode"] is False and job["physical_namespace"] is None
    assert dcfg["targets"] == cfg["targets"]


def test_execute_sync_request_runs_only_the_scoped_model_with_full_list_naming(multi_project, monkeypatch):
    cfg, files = multi_project
    captured = {}

    def _fake_run_parallel_jobs(**kwargs):
        captured["jobs"] = kwargs["sync_jobs"]
        return [{"model": j["model_label"], "status": "success", "summary": {}} for j in kwargs["sync_jobs"]]

    monkeypatch.setattr(ses, "_run_parallel_jobs", _fake_run_parallel_jobs)
    full = {j["source_ref"]: j for j in ses._build_sync_jobs(cfg)[0]}
    out = ses.execute_sync_request(
        {"content": yaml.safe_dump(cfg), "dry_run": True, "scope_source_refs": [files[2]]},
        lambda text: text,
    )
    assert [j["source_ref"] for j in captured["jobs"]] == [files[2]]
    job = captured["jobs"][0]
    assert job["physical_namespace"] == full[files[2]]["physical_namespace"]
    assert job["batch_model_count"] == 3
    plan = out["results"][0]["model_plan"]
    assert plan["view_name"] == ses.model_plan_for_job(full[files[2]], "snowflake")["view_name"]
    assert plan["config_hash"] == ses.model_config_fingerprint(cfg, full[files[2]], "snowflake")


def test_scope_is_ignored_for_real_syncs(multi_project, monkeypatch):
    cfg, files = multi_project
    captured = {}
    monkeypatch.setattr(
        ses, "_run_parallel_jobs",
        lambda **kw: captured.setdefault("jobs", kw["sync_jobs"]) and [],
    )
    ses.execute_sync_request({"content": yaml.safe_dump(cfg), "scope_source_refs": [files[0]]}, lambda t: t)
    assert len(captured["jobs"]) == 3


def test_dry_run_does_not_take_the_target_view_lock(multi_project, monkeypatch):
    cfg, files = multi_project
    job = ses._build_sync_jobs(cfg)[0][0]
    view = ses.get_target_deployment_name(job["resolved_display_name"], "snowflake")
    key = ses._target_lock_key(cfg, view)

    class _Summary:
        def model_dump(self, mode="json"):
            return {"status": "SUCCESS"}

    class _Engine:
        def __init__(self, **kw):
            pass

        def execute(self, **kw):
            return _Summary()

    monkeypatch.setattr(ses, "ExecutionEngine", _Engine)
    monkeypatch.setattr(ses, "ModelRepository", lambda: None)
    kwargs = dict(engine_source_type="pbix", target_type="snowflake", config=cfg, config_path="",
                  resolved_workspace_id="w")
    # Another run holds the DB-backed lease on this view (cross-process).
    from semabridge.core import run_coordination as rc

    target_cfg = ses._resolve_target_config(cfg) or {}
    lease_key = rc.target_lease_key(target_cfg.get("database"), target_cfg.get("schema"), view)
    held = rc.acquire_lease(lease_key, run_id="other-run", holder="other-run:model")
    assert held and key
    try:
        dry = ses._run_single_job_impl(job, deploy_enabled=False, **kwargs)
        real = ses._run_single_job_impl(job, deploy_enabled=True, **kwargs)
    finally:
        rc.release_leases(holder="other-run:model")
    assert dry["status"] == "success"
    assert real["status"] == "conflict"
