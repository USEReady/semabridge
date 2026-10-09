"""Tests for multi-model dry-run readiness server-side gate and synonym/override flow.
Neutral names used throughout (no domain-specific naming).
"""
from __future__ import annotations

import json
import os
import sys
import types
from datetime import datetime, timezone

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
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.api.services.dry_run_job_service import (
    create_dry_run_job,
    validate_multi_model_dry_run_readiness,
)
from semabridge.domain.exceptions import ValidationError
from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
from semabridge.repository.orm.session_factory import db_manager


@pytest.fixture(autouse=True)
def _reset_db():
    from semabridge.api.services.dry_run_job_service import _ensure_schema
    _ensure_schema()
    with db_manager.get_session() as session:
        session.query(DryRunJobFile).delete()
        session.query(DryRunJob).delete()
    yield
    with db_manager.get_session() as session:
        session.query(DryRunJobFile).delete()
        session.query(DryRunJob).delete()


def _populate_job(
    project_id: str,
    files_data: list[dict],
    status: str = "success",
):
    now = datetime.now(timezone.utc)
    job_id = f"dryrun-{project_id}"
    with db_manager.get_session() as session:
        job = DryRunJob(
            job_id=job_id,
            project_id=project_id,
            status=status,
            created_at=now,
            source_config_json=json.dumps({"mode": "multiple"}),
            target_config_json=json.dumps({}),
            source_type="pbix",
        )
        session.add(job)
        for idx, fd in enumerate(files_data):
            f = DryRunJobFile(
                file_id=f"{job_id}-f{idx}",
                job_id=job_id,
                file_index=idx,
                pbix_path=fd.get("pbix_path", ""),
                source_ref=fd.get("source_ref", fd.get("pbix_path", "")),
                source_type="pbix",
                display_name=fd.get("label", f"Model{idx}"),
                label=fd.get("label", f"Model{idx}"),
                status=fd.get("status", "success"),
                error_message=fd.get("error_message"),
                result_json=json.dumps(fd.get("result", {})) if fd.get("result") is not None else None,
                queued_at=now,
                started_at=now,
                completed_at=now,
            )
            session.add(f)


def test_gate_rejects_when_no_dry_run_exists():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    with pytest.raises(ValidationError, match="no dry-run job found"):
        validate_multi_model_dry_run_readiness("proj_gate_1", source_cfg)


def test_gate_rejects_when_dry_run_in_progress():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    _populate_job(
        "proj_gate_2",
        [
            {"source_ref": "models/ModelA.pbix", "status": "running"},
            {"source_ref": "models/ModelB.pbix", "status": "pending"},
        ],
        status="running",
    )
    with pytest.raises(ValidationError, match="currently in progress"):
        validate_multi_model_dry_run_readiness("proj_gate_2", source_cfg)


def test_gate_rejects_when_a_model_was_not_dry_run():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    _populate_job(
        "proj_gate_3",
        [
            {"source_ref": "models/ModelA.pbix", "status": "success", "result": {"entity_mappings": []}},
        ],
        status="success",
    )
    with pytest.raises(ValidationError, match="model 'models/ModelB.pbix' has not been dry-run"):
        validate_multi_model_dry_run_readiness("proj_gate_3", source_cfg)


def test_gate_rejects_when_a_model_dry_run_failed():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    _populate_job(
        "proj_gate_4",
        [
            {"source_ref": "models/ModelA.pbix", "status": "success", "result": {"entity_mappings": []}},
            {"source_ref": "models/ModelB.pbix", "status": "failed", "error_message": "Invalid schema syntax"},
        ],
        status="partial",
    )
    with pytest.raises(ValidationError, match="dry run for model '.*' failed"):
        validate_multi_model_dry_run_readiness("proj_gate_4", source_cfg)


def test_gate_rejects_when_unresolved_blockers_exist():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix"],
    }
    _populate_job(
        "proj_gate_5",
        [
            {
                "source_ref": "models/ModelA.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {
                            "id": "row_1",
                            "source_name": "FieldOne",
                            "target_name": "FIELD_ONE",
                            "status": "collision",
                            "collision_detected": True,
                        }
                    ]
                },
            }
        ],
        status="success",
    )
    with pytest.raises(ValidationError, match="1 unresolved dry-run blocker"):
        validate_multi_model_dry_run_readiness("proj_gate_5", source_cfg)


def test_gate_passes_when_all_models_succeed_without_blockers():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    _populate_job(
        "proj_gate_6",
        [
            {
                "source_ref": "models/ModelA.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {"id": "r1", "source_name": "Field1", "target_name": "F1", "status": "valid"}
                    ]
                },
            },
            {
                "source_ref": "models/ModelB.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {"id": "r2", "source_name": "Field2", "target_name": "F2", "status": "valid"}
                    ]
                },
            },
        ],
        status="success",
    )
    # Should not raise
    validate_multi_model_dry_run_readiness("proj_gate_6", source_cfg)


def test_gate_passes_when_job_is_linked_from_preview():
    from semabridge.api.services.dry_run_job_service import link_dry_run_job_to_project

    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    _populate_job(
        "preview",
        [
            {
                "source_ref": "models/ModelA.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {"id": "r1", "source_name": "Field1", "target_name": "F1", "status": "valid"}
                    ]
                },
            },
            {
                "source_ref": "models/ModelB.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {"id": "r2", "source_name": "Field2", "target_name": "F2", "status": "valid"}
                    ]
                },
            },
        ],
        status="success",
    )

    linked = link_dry_run_job_to_project(
        "proj_linked_1",
        dry_run_job_id="dryrun-preview",
    )
    assert linked >= 1
    # Gate should now pass for the real project id
    validate_multi_model_dry_run_readiness("proj_linked_1", source_cfg)


def test_gate_discovers_unlinked_preview_job_by_matching_models():
    source_cfg = {
        "mode": "multiple",
        "models": ["models/ModelA.pbix", "models/ModelB.pbix"],
    }
    _populate_job(
        "preview-random-id",
        [
            {
                "source_ref": "models/ModelA.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {"id": "r1", "source_name": "Field1", "target_name": "F1", "status": "valid"}
                    ]
                },
            },
            {
                "source_ref": "models/ModelB.pbix",
                "status": "success",
                "result": {
                    "entity_mappings": [
                        {"id": "r2", "source_name": "Field2", "target_name": "F2", "status": "valid"}
                    ]
                },
            },
        ],
        status="success",
    )

    # Gate should discover the preview job with matching models and link it
    validate_multi_model_dry_run_readiness("proj-multi-sync-test", source_cfg)

