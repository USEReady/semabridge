"""Sync gate is tied to file CONTENT and deploy PLAN, not to file names."""
from __future__ import annotations

import json
import logging
import os
import sys
import types
from datetime import datetime, timedelta, timezone

os.environ["SEMABRIDGE_DATABASE_URL"] = "sqlite:///file::memory:?cache=shared&uri=true"

import pytest

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    _extras = types.ModuleType("psycopg2.extras")
    _extras.register_uuid = lambda *args, **kwargs: None
    _extras.register_default_json = lambda *args, **kwargs: None
    _extras.register_default_jsonb = lambda *args, **kwargs: None
    _extras.register_hstore = lambda *args, **kwargs: None
    _extras.HstoreAdapter = types.SimpleNamespace(get_oids=lambda *args, **kwargs: (None, None))
    sys.modules["psycopg2.extras"] = _extras

from semabridge.api.services import sync_execution_service as ses
from semabridge.api.services.dry_run_job_service import (
    _ensure_schema,
    _record_file_outcome,
    validate_multi_model_dry_run_readiness,
)
from semabridge.domain.exceptions import ValidationError
from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
from semabridge.repository.orm.session_factory import db_manager
from semabridge.utils.project_models import assign_model_view_names

PID = "proj-plan-gate"
HEX_A, HEX_B = "a" * 32, "b" * 32
OK_RESULT = {"entity_mappings": [{"id": "r1", "source_path": "datasets.T.columns.C", "source_name": "C",
                                  "target_name": "C", "entity_kind": "column", "mapping_scope": "column::m::datasets.T"}]}


@pytest.fixture(autouse=True)
def _reset_db():
    _ensure_schema()
    with db_manager.get_session() as session:
        session.query(DryRunJobFile).delete()
        session.query(DryRunJob).delete()
    yield
    with db_manager.get_session() as session:
        session.query(DryRunJobFile).delete()
        session.query(DryRunJob).delete()


@pytest.fixture()
def project(tmp_path):
    a = tmp_path / f"{HEX_A}_Sales.pbix"
    b = tmp_path / f"{HEX_A}_Finance.pbix"
    a.write_bytes(b"sales-v1")
    b.write_bytes(b"finance-v1")
    source = {"type": "pbix", "mode": "multiple", "models": [str(a), str(b)]}
    source["model_views"] = assign_model_view_names(source)
    return {
        "source": source,
        "targets": [{"type": "snowflake", "database": "DB", "schema": "SCH"}],
        "mappings_overrides": [],
    }


def _plan_hashes(cfg):
    jobs, _st, tt, _s, _t = ses._build_sync_jobs(cfg)
    return {j["source_ref"]: (ses.file_content_sha256(j["pbix_path"]), ses.model_config_fingerprint(cfg, j, tt)) for j in jobs}


_counter = {"n": 0}


def _add_job(files, *, project_id=PID, age_minutes=0):
    _counter["n"] += 1
    now = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    job_id = f"dryrun-gate-{_counter['n']}"
    with db_manager.get_session() as session:
        session.add(DryRunJob(job_id=job_id, project_id=project_id, status="success", created_at=now,
                              source_config_json="{}", target_config_json="{}", source_type="pbix"))
        for i, f in enumerate(files):
            session.add(DryRunJobFile(
                file_id=f"{job_id}-f{i}", job_id=job_id, file_index=i, pbix_path=f["ref"], source_ref=f["ref"],
                source_type="pbix", display_name=f.get("label", f"M{i}"), label=f.get("label", f"M{i}"),
                status=f.get("status", "success"), error_message=f.get("error"),
                result_json=json.dumps(f.get("result", OK_RESULT)), queued_at=now, started_at=now, completed_at=now,
                content_hash=f.get("content_hash"), config_hash=f.get("config_hash"),
            ))


def _files_for(cfg, **overrides):
    out = []
    for ref, (content, config_hash) in _plan_hashes(cfg).items():
        out.append({"ref": ref, "content_hash": content, "config_hash": config_hash, **overrides.get(ref, {})})
    return out


def test_gate_passes_for_matching_content_and_plan(project):
    _add_job(_files_for(project))
    validate_multi_model_dry_run_readiness(PID, project["source"], config=project)


def test_reupload_copy_of_same_bytes_still_passes(project, tmp_path):
    files = _files_for(project)
    # The dry run ran on a different upload copy (other prefix, same bytes).
    for f in files:
        copy = tmp_path / ("dry_" + os.path.basename(f["ref"]).replace(HEX_A, HEX_B))
        copy.write_bytes(open(f["ref"], "rb").read())
        f["ref"] = str(copy)
    _add_job(files)
    validate_multi_model_dry_run_readiness(PID, project["source"], config=project)


def test_changed_file_blocks_with_rerun_message(project):
    _add_job(_files_for(project))
    sales = project["source"]["models"][0]
    with open(sales, "wb") as fh:
        fh.write(b"sales-v2")
    with pytest.raises(ValidationError, match="changed since its dry run — re-run the dry run"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=project)


def test_changed_plan_blocks(project):
    _add_job(_files_for(project))
    moved = {**project, "targets": [{"type": "snowflake", "database": "DB", "schema": "OTHER"}]}
    with pytest.raises(ValidationError, match="changed since its dry run"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=moved)


def test_newest_dry_run_of_the_same_content_decides(project):
    sales = project["source"]["models"][0]
    _add_job(_files_for(project), age_minutes=10)
    _add_job([f for f in _files_for(project, **{sales: {"status": "failed", "error": "boom"}}) if f["ref"] == sales])
    with pytest.raises(ValidationError, match=r"dry run for model .* failed \(boom\)"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=project)


def test_ontology_blocking_conflicts_block(project):
    sales = project["source"]["models"][0]
    _add_job(_files_for(project, **{sales: {"result": {**OK_RESULT, "blocking_conflicts": [{"term": "x"}]}}}))
    with pytest.raises(ValidationError, match="1 ontology blocking conflict"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=project)


def test_override_resolves_blocker_but_duplicate_names_block(project):
    sales = project["source"]["models"][0]
    rows = [
        {"id": "a", "source_path": "datasets.T.columns.A", "source_name": "A", "target_name": "A",
         "entity_kind": "column", "mapping_scope": "s", "status": "collision", "collision_detected": True},
        {"id": "b", "source_path": "datasets.T.columns.B", "source_name": "B", "target_name": "B",
         "entity_kind": "column", "mapping_scope": "s"},
    ]
    _add_job(_files_for(project, **{sales: {"result": {"entity_mappings": rows}}}))
    with pytest.raises(ValidationError, match="unresolved dry-run blocker"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=project)
    fixed = {**project, "mappings_overrides": [{"source_path": "datasets.T.columns.A", "target_name": "A_NEW", "model": sales}]}
    validate_multi_model_dry_run_readiness(PID, project["source"], config=fixed)
    clash = {**project, "mappings_overrides": [{"source_path": "datasets.T.columns.A", "target_name": "b", "model": sales}]}
    with pytest.raises(ValidationError, match="duplicate target name"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=clash)


def test_legacy_dry_run_without_hash_is_allowed_with_warning(project, caplog):
    files = [{"ref": f["ref"], "content_hash": None, "config_hash": None} for f in _files_for(project)]
    _add_job(files)
    with caplog.at_level(logging.WARNING):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=project)
    assert "legacy dry run" in caplog.text


def test_other_projects_dry_runs_are_never_used(project):
    _add_job(_files_for(project), project_id="preview-someone-else")
    with pytest.raises(ValidationError, match="no dry-run job found"):
        validate_multi_model_dry_run_readiness(PID, project["source"], config=project)


def test_record_file_outcome_stores_hashes_only_on_success(project):
    _add_job([{"ref": project["source"]["models"][0], "status": "running"}])
    with db_manager.get_session() as session:
        f = session.query(DryRunJobFile).first()
        file_id, job_id, started = f.file_id, f.job_id, f.started_at
    h = "c" * 64
    _record_file_outcome(job_id, file_id, started_at=started.replace(tzinfo=timezone.utc), expected_preview_id=None,
                         result={"success": True, "content_hash": h, "config_hash": h, "overrides_hash": h}, error=None)
    with db_manager.get_session() as session:
        f = session.get(DryRunJobFile, file_id)
        assert (f.status, f.content_hash, f.config_hash) == ("success", h, h)
    with db_manager.get_session() as session:
        session.get(DryRunJobFile, file_id).status = "running"
    _record_file_outcome(job_id, file_id, started_at=started.replace(tzinfo=timezone.utc), expected_preview_id=None,
                         result={"success": False, "error": "Step 7 failed", "content_hash": h}, error=None)
    with db_manager.get_session() as session:
        f = session.get(DryRunJobFile, file_id)
        assert (f.status, f.content_hash, f.error_message) == ("failed", None, "Step 7 failed")
