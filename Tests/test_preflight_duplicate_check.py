"""Tests for early pre-Step-4 'Model already synced' duplicate check endpoint & service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from semabridge.api.services import project_shared
from semabridge.core import run_coordination as rc
from semabridge.repository.orm.dry_run_job_models import DryRunJob, DryRunJobFile
from semabridge.repository.orm.session_factory import db_manager
from semabridge.repository.orm.snapshot_models import SnapshotRow


@pytest.fixture
def isolated_projects(monkeypatch):
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Project, Run, RunLease  # noqa: F401

    Base.metadata.create_all(db_manager.get_engine())
    with db_manager.get_session() as s:
        for pid in ("proj-ai-sample", "proj-fab-sales"):
            if s.get(Project, pid) is None:
                s.add(Project(project_id=pid, name=pid))
        s.commit()
    orig_projects = dict(project_shared._compat_projects)
    orig_configs = dict(project_shared._compat_project_configs)
    monkeypatch.setattr(project_shared, "_compat_ensure_loaded", lambda: None)
    try:
        yield
    finally:
        with db_manager.get_session() as s:
            s.query(SnapshotRow).filter(SnapshotRow.project_id.in_(["proj-ai-sample", "proj-fab-sales"])).delete()
            s.query(Run).filter(Run.project_id.in_(["proj-ai-sample", "proj-fab-sales"])).delete()
            s.query(RunLease).filter(RunLease.project_id.in_(["proj-ai-sample", "proj-fab-sales"])).delete()
            for job in s.query(DryRunJob).filter(DryRunJob.project_id.in_(["proj-ai-sample", "proj-fab-sales"])).all():
                s.delete(job)
            s.commit()
        project_shared._compat_projects.clear()
        project_shared._compat_projects.update(orig_projects)
        project_shared._compat_project_configs.clear()
        project_shared._compat_project_configs.update(orig_configs)


def test_preflight_duplicate_check_detects_pbix_already_synced_or_in_progress_before_step4(
    tmp_path, isolated_projects
):
    """POST /api/projects/preview/duplicate-check catches an already-synced/in-progress PBIX before Step 4."""
    from semabridge.api.main import app
    from semabridge.api.services.sync_execution_service import file_content_sha256

    pbix = tmp_path / "Employee Hiring and History 1.pbix"
    pbix.write_bytes(b"fake-pbix-binary-payload-for-hash")
    fhash = file_content_sha256(str(pbix))
    assert fhash

    other_pid = "proj-ai-sample"
    # "name" is the source model's name; the dialog shows the project's own name.
    project_shared._compat_projects[other_pid] = {
        "id": other_pid, "name": "ARTIFICIAL_INTELLIGENCE_SAMPLE_1_1", "display_name": "dbt5",
    }

    job_id = f"dryrun-{uuid.uuid4().hex[:12]}"
    with db_manager.get_session() as s:
        s.add(
            DryRunJob(
                job_id=job_id,
                project_id=other_pid,
                status="success",
                created_at=datetime.now(timezone.utc),
                source_config_json=json.dumps({"type": "pbix", "pbix_path": str(pbix)}),
                target_config_json=json.dumps({"type": "databricks", "catalog": "workspace", "schema": "default"}),
            )
        )
        s.add(
            DryRunJobFile(
                file_id=f"{job_id}-f0",
                job_id=job_id,
                file_index=0,
                pbix_path=str(pbix),
                source_ref=str(pbix),
                display_name="Employee Hiring and History 1",
                status="success",
                content_hash=fhash,
                preview_project_id="preview-other",
            )
        )
        s.commit()

    rc.acquire_lease(rc.project_lease_key(other_pid), run_id="run-live-1", lease_type="project", project_id=other_pid)
    try:
        client = TestClient(app)
        res = client.post(
            "/api/projects/preview/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "databricks", "catalog": "workspace", "schema": "default"},
                "selected_sources": [str(pbix)],
                "project_name": "New HR Project",
            },
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["has_duplicates"] is True
        assert len(data["duplicates"]) == 1
        dup = data["duplicates"][0]
        assert dup["other_project_id"] == other_pid
        assert dup["other_project_name"] == "dbt5"
        assert dup["target_label"] == "Databricks"
        assert "same_file_in_progress" in dup["reasons"]

        # Different target schema -> NOT a duplicate
        res_other_schema = client.post(
            "/api/projects/preview/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "databricks", "catalog": "workspace", "schema": "staging"},
                "selected_sources": [str(pbix)],
            },
        )
        assert res_other_schema.status_code == 200
        assert res_other_schema.json()["has_duplicates"] is False

        # Editing the SAME project -> NOT a duplicate of itself
        res_self = client.post(
            f"/api/projects/{other_pid}/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "databricks", "catalog": "workspace", "schema": "default"},
                "selected_sources": [str(pbix)],
            },
        )
        assert res_self.status_code == 200
        assert res_self.json()["has_duplicates"] is False
    finally:
        rc.release_leases(run_id="run-live-1")


def test_preflight_duplicate_check_detects_fabric_model_already_synced_or_dry_run_before_step4(
    isolated_projects,
):
    """Non-file sources (Fabric) also detect duplicate deployments & pending dry-runs before Step 4."""
    from semabridge.api.main import app
    from semabridge.utils.identifiers import databricks_view_name

    other_pid = "proj-fab-sales"
    project_shared._compat_projects[other_pid] = {"id": other_pid, "name": "Enterprise Fabric Sales"}

    snap_id = f"snap-{uuid.uuid4().hex[:12]}"
    expected_view = databricks_view_name("Sales Exec").upper()
    with db_manager.get_session() as s:
        s.add(
            SnapshotRow(
                snapshot_id=snap_id,
                project_id=other_pid,
                run_id="run-fab-1",
                timestamp=datetime.now(timezone.utc),
                deployed_at=datetime.now(timezone.utc),
                target_database="WORKSPACE",
                target_schema="DEFAULT",
                view_name=expected_view,
                sml_blob=json.dumps({"unique_name": "Sales Exec", "label": "Sales Exec", "datasets": [], "metrics": []}),
            )
        )
        s.commit()

    client = TestClient(app)
    res = client.post(
        "/api/projects/preview/duplicate-check",
        json={
            "source_config": {"type": "fabric", "workspace_id": "11111111-1111-1111-1111-111111111111"},
            "target_config": {"type": "databricks", "catalog": "workspace", "schema": "default"},
            "selected_sources": ["Sales Exec"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["has_duplicates"] is True
    assert any(d["other_project_id"] == other_pid for d in body["duplicates"])



def test_cross_platform_targets_snowflake_vs_databricks_do_not_flag_each_other(
    tmp_path, isolated_projects
):
    """Syncing the same model to Snowflake vs Databricks is allowed (even with identical db/schema names);
    only syncing to the SAME target platform + location is flagged, with target_type & target_label returned.
    """
    from semabridge.api.main import app
    from semabridge.api.services.sync_execution_service import file_content_sha256
    from semabridge.repository.orm.models import Project

    pbix = tmp_path / "CrossPlatformSales.pbix"
    pbix.write_bytes(b"cross-platform-pbix-bytes")
    fhash = file_content_sha256(str(pbix))

    sf_pid = "proj-sf-analytics"
    dbx_pid = "proj-dbx-analytics"
    with db_manager.get_session() as s:
        for pid in (sf_pid, dbx_pid):
            if s.get(Project, pid) is None:
                s.add(Project(project_id=pid, name=pid))
        s.commit()

    try:
        # Project 1 deployed CrossPlatformSales.pbix to SNOWFLAKE (ANALYTICS.GOLD)
        project_shared._compat_projects[sf_pid] = {
            "id": sf_pid,
            "name": "Snowflake Sales Prod",
            "target_type": "snowflake",
            "target": {"type": "snowflake", "database": "ANALYTICS", "schema": "GOLD"},
        }
        sf_job = f"dryrun-{uuid.uuid4().hex[:12]}"
        with db_manager.get_session() as s:
            s.add(
                SnapshotRow(
                    snapshot_id=f"snap-{uuid.uuid4().hex[:10]}",
                    project_id=sf_pid,
                    run_id="run-sf-1",
                    timestamp=datetime.now(timezone.utc),
                    deployed_at=datetime.now(timezone.utc),
                    target_database="ANALYTICS",
                    target_schema="GOLD",
                    view_name="CROSSPLATFORMSALES_SEMANTIC",
                    sml_blob=json.dumps({"unique_name": "CrossPlatformSales", "label": "CrossPlatformSales", "datasets": [], "metrics": []}),
                )
            )
            # Also a Snowflake dry-run with blank db/schema must NEVER bleed into Databricks
            s.add(
                DryRunJob(
                    job_id=sf_job,
                    project_id=sf_pid,
                    status="success",
                    created_at=datetime.now(timezone.utc),
                    source_config_json=json.dumps({"type": "pbix", "pbix_path": str(pbix)}),
                    target_config_json=json.dumps({"type": "snowflake"}),
                )
            )
            s.add(
                DryRunJobFile(
                    file_id=f"{sf_job}-f0",
                    job_id=sf_job,
                    file_index=0,
                    pbix_path=str(pbix),
                    source_ref=str(pbix),
                    display_name="CrossPlatformSales",
                    status="success",
                    content_hash=fhash,
                    preview_project_id="preview-sf",
                )
            )
            s.commit()

        client = TestClient(app)

        # 1. Selecting DATABRICKS (even with catalog='analytics', schema='gold') -> OK (NOT a duplicate!)
        res_dbx_vs_sf = client.post(
            "/api/projects/preview/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "databricks", "catalog": "analytics", "schema": "gold"},
                "selected_sources": [str(pbix)],
            },
        )
        assert res_dbx_vs_sf.status_code == 200, res_dbx_vs_sf.text
        assert res_dbx_vs_sf.json()["has_duplicates"] is False, res_dbx_vs_sf.json()

        # 2. Selecting SNOWFLAKE (ANALYTICS.GOLD) -> CONCERN (same platform + same db.schema!)
        res_sf_vs_sf = client.post(
            "/api/projects/preview/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "snowflake", "database": "ANALYTICS", "schema": "GOLD"},
                "selected_sources": [str(pbix)],
            },
        )
        assert res_sf_vs_sf.status_code == 200
        sf_dup_body = res_sf_vs_sf.json()
        assert sf_dup_body["has_duplicates"] is True
        assert sf_dup_body["duplicates"][0]["target_type"] == "snowflake"
        assert sf_dup_body["duplicates"][0]["target_label"] == "Snowflake"
        assert "Snowflake" in sf_dup_body["message"]

        # 3. Vice versa: Project 2 deployed to DATABRICKS (WORKSPACE.DEFAULT)
        project_shared._compat_projects[dbx_pid] = {
            "id": dbx_pid,
            "name": "Databricks Sales Lakehouse",
            "target_type": "databricks",
            "target": {"type": "databricks", "catalog": "workspace", "schema": "default"},
        }
        with db_manager.get_session() as s:
            s.add(
                SnapshotRow(
                    snapshot_id=f"snap-{uuid.uuid4().hex[:10]}",
                    project_id=dbx_pid,
                    run_id="run-dbx-1",
                    timestamp=datetime.now(timezone.utc),
                    deployed_at=datetime.now(timezone.utc),
                    target_database="WORKSPACE",
                    target_schema="DEFAULT",
                    view_name="CROSSPLATFORMSALES",
                    sml_blob=json.dumps({"unique_name": "CrossPlatformSales", "label": "CrossPlatformSales", "datasets": [], "metrics": []}),
                )
            )
            s.commit()

        # Selecting SNOWFLAKE (WORKSPACE.DEFAULT) -> OK (different target platform!)
        res_sf_vs_dbx = client.post(
            "/api/projects/preview/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "snowflake", "database": "WORKSPACE", "schema": "DEFAULT"},
                "selected_sources": [str(pbix)],
            },
        )
        assert res_sf_vs_dbx.status_code == 200
        sf_vs_dbx = res_sf_vs_dbx.json()["duplicates"]
        # The Databricks project is never flagged (other platform) ...
        assert not [d for d in sf_vs_dbx if d["other_project_id"] == dbx_pid], sf_vs_dbx
        # ... but the Snowflake project's copy in ANALYTICS.GOLD is: same platform,
        # another schema -> shown and confirmed, never an overwrite.
        assert [(d["other_project_id"], d["target"], d["elsewhere"], d["overwrites"]) for d in sf_vs_dbx] == [
            (sf_pid, "ANALYTICS.GOLD", True, False)
        ]

        # Selecting DATABRICKS (workspace.default) -> CONCERN (same platform + same catalog.schema!)
        res_dbx_vs_dbx = client.post(
            "/api/projects/preview/duplicate-check",
            json={
                "source_config": {"type": "pbix", "pbix_path": str(pbix)},
                "target_config": {"type": "databricks", "catalog": "workspace", "schema": "default"},
                "selected_sources": [str(pbix)],
            },
        )
        assert res_dbx_vs_dbx.status_code == 200
        dbx_dup_body = res_dbx_vs_dbx.json()
        assert dbx_dup_body["has_duplicates"] is True
        assert dbx_dup_body["duplicates"][0]["target_type"] == "databricks"
        assert dbx_dup_body["duplicates"][0]["target_label"] == "Databricks"
        assert "Databricks" in dbx_dup_body["message"]
    finally:
        with db_manager.get_session() as s:
            s.query(SnapshotRow).filter(SnapshotRow.project_id.in_([sf_pid, dbx_pid])).delete()
            for job in s.query(DryRunJob).filter(DryRunJob.project_id.in_([sf_pid, dbx_pid])).all():
                s.delete(job)
            s.commit()
