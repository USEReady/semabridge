"""Tests verifying Upsert baseline target lookup and dry-run exclusion rules.

1. Dry runs are never a baseline:
   - get_last_deployed_snapshot excludes DRY_RUN everywhere (including legacy fallback).
   - Dry-run snapshots must never get deployed_at set.
2. Baseline by target, not by project:
   - The Upsert baseline is the latest successful deployment to the same target semantic view
     (database + schema + view name), from ANY SemaBridge project.
   - If no deployment of that view exists in history: stops with:
     "No previous deployment of <view> found. Run a Copy sync first."
3. Scenarios a, b, c, d verification.
"""

from datetime import datetime, timezone
import json
from types import SimpleNamespace
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.snapshot_models import SnapshotRow
from semabridge.repository.orm.run_models import Run
from semabridge.repository.orm.project_models import Project
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.engine.conversion.base import _step6a_apply_upsert_delta
from semabridge.core.engine.exceptions import ConversionError
from semabridge.core.upsert_delta import compute_delta
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)


@pytest.fixture
def repo_with_db(tmp_path):
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    repo = ModelRepository(url_override=db_url)
    return repo, repo._SessionLocal



def _build_model(unique_name="SalesModel", metrics=None):
    sales_fact = SMLDataset(
        unique_name="SalesFact",
        columns=[
            SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
            SMLColumn(unique_name="Units", data_type=DataType.INTEGER),
        ],
    )
    m_list = metrics or [
        SMLMetric(unique_name="Total Revenue", dataset="SalesFact", expression="SUM(SalesFact[Revenue])"),
    ]
    return SMLModel(
        unique_name=unique_name,
        label=unique_name,
        datasets=[sales_fact],
        metrics=m_list,
    )


def test_dry_run_snapshots_never_get_deployed_at(repo_with_db):
    """Requirement 1: mark_snapshot_deployed refuses to set deployed_at on DRY_RUN snapshots."""
    repo, Session = repo_with_db
    now = datetime.now(timezone.utc)

    with Session() as session:
        session.add(Project(project_id="proj-1", name="Project 1"))
        session.add(Run(run_id="run-dry", project_id="proj-1", run_type="DRY_RUN", status="success", started_at=now))
        session.add(
            SnapshotRow(
                snapshot_id="snap-dry",
                project_id="proj-1",
                run_id="run-dry",
                timestamp=now,
                status="success",
                deployed_at=None,
            )
        )
        session.commit()

    # Attempt to mark dry-run snapshot as deployed
    marked = repo.mark_snapshot_deployed("snap-dry", target_database="DB", target_schema="PUBLIC", view_name="VIEW_A")
    assert marked is False

    with Session() as session:
        row = session.get(SnapshotRow, "snap-dry")
        assert row.deployed_at is None


def test_get_last_deployed_snapshot_excludes_dry_run_everywhere(repo_with_db):
    """Requirement 1: get_last_deployed_snapshot excludes DRY_RUN in both deployed and fallback passes."""
    repo, Session = repo_with_db
    now = datetime.now(timezone.utc)
    model = _build_model("ModelA")
    blob = model.model_dump(mode="json")

    with Session() as session:
        session.add(Project(project_id="proj-1", name="Project 1", database="DB", schema="PUBLIC"))
        session.add(Run(run_id="run-dry", project_id="proj-1", run_type="DRY_RUN", status="success", target_type="snowflake", started_at=now))
        session.add(
            SnapshotRow(
                snapshot_id="snap-dry",
                project_id="proj-1",
                run_id="run-dry",
                timestamp=now,
                status="success",
                deployed_at=None,
                sml_blob=json.dumps(blob),
                view_name="MODELA_SEMANTIC",
                target_database="DB",
                target_schema="PUBLIC",
            )
        )
        session.commit()

    # Even though snap-dry is in DB with status=success and matches the view, it must NEVER be returned as baseline
    baseline = repo.get_last_deployed_snapshot(
        project_id="proj-1",
        model_names=["ModelA"],
        target_database="DB",
        target_schema="PUBLIC",
        view_name="MODELA_SEMANTIC",
    )
    assert baseline is None


def test_baseline_by_target_not_by_project_cross_project(repo_with_db):
    """Requirement 2: Latest successful deployment to the same target semantic view is found across ANY project."""
    repo, Session = repo_with_db
    now = datetime.now(timezone.utc)
    model = _build_model("SalesModel")
    blob = model.model_dump(mode="json")

    with Session() as session:
        # Project A deploys to DB.PUBLIC.SALESMODEL_SEMANTIC
        session.add(Project(project_id="proj-A", name="Project A", database="DB", schema="PUBLIC"))
        session.add(Run(run_id="run-A", project_id="proj-A", run_type="SYNC", status="success", target_type="snowflake", started_at=now))
        session.add(
            SnapshotRow(
                snapshot_id="snap-A-deployed",
                project_id="proj-A",
                run_id="run-A",
                timestamp=now,
                status="success",
                deployed_at=now,
                sml_blob=json.dumps(blob),
                view_name="SALESMODEL_SEMANTIC",
                target_database="DB",
                target_schema="PUBLIC",
            )
        )

        # Project B (different project) targeting the same view
        session.add(Project(project_id="proj-B", name="Project B", database="DB", schema="PUBLIC"))
        session.commit()

    # Looking up from Project B should find Project A's deployed snapshot as the baseline!
    baseline = repo.get_last_deployed_snapshot(
        project_id="proj-B",
        model_names=["SalesModel"],
        target_database="DB",
        target_schema="PUBLIC",
        view_name="SALESMODEL_SEMANTIC",
    )
    assert baseline is not None
    assert baseline.snapshot_id == "snap-A-deployed"
    assert baseline.project_id == "proj-A"


def test_no_previous_deployment_stops_with_clear_message(repo_with_db):
    """Requirement 2: If no deployment exists in history, stop with clear message and do not deploy."""
    repo, Session = repo_with_db
    model = _build_model("UnseededModel")

    context = SimpleNamespace(
        project_id="proj-new",
        source_type="pbix",
        sync_mode="upsert",
        sml_model=model,
        config_payload={},
        config=SimpleNamespace(target=SimpleNamespace(type="snowflake", database="DB", schema_name="PUBLIC")),
        upsert_delta=None,
    )

    harness = SimpleNamespace(
        db_manager=repo,
        _record_step=lambda *args, **kwargs: None,
        _upsert_delta_preview_model=lambda ctx, ovr: ctx.sml_model,
    )

    with pytest.raises(ConversionError) as exc_info:
        _step6a_apply_upsert_delta(harness, context)

    assert "No previous deployment of UNSEEDEDMODEL_SEMANTIC found. Run a Copy sync first." in str(exc_info.value)


def test_scenario_a_b_c_d_lifecycle(repo_with_db):
    """Verify scenarios a, b, c, d end-to-end:
    a. Same project: Copy sync -> add measure -> Upsert dry run shows '1 added' -> Upsert sync shows '1 added'
    b. Different projects, same target view: Copy in proj A -> Upsert in proj B with modified PBIX -> '1 added'
    c. Upsert with no change -> 'No changes'
    d. Restore: Copy sync with original PBIX
    """
    repo, Session = repo_with_db
    now = datetime.now(timezone.utc)

    # 1. Base model
    base_model = _build_model("SalesFactModel")
    base_blob = base_model.model_dump(mode="json")

    # Step a1: Copy sync in Project 1
    with Session() as session:
        session.add(Project(project_id="proj-1", name="Project 1", database="DB", schema="PUBLIC"))
        session.add(Run(run_id="run-copy-1", project_id="proj-1", run_type="SYNC", status="success", target_type="snowflake", started_at=now))
        session.add(
            SnapshotRow(
                snapshot_id="snap-copy-1",
                project_id="proj-1",
                run_id="run-copy-1",
                timestamp=now,
                status="success",
                deployed_at=now,
                sml_blob=json.dumps(base_blob),
                view_name="SALESFACTMODEL_SEMANTIC",
                target_database="DB",
                target_schema="PUBLIC",
            )
        )
        session.commit()

    # Step a2: Add "Revenue per Unit = DIVIDE(SUM(SalesFact[Revenue]), SUM(SalesFact[Units]))"
    modified_metrics = [
        SMLMetric(unique_name="Total Revenue", dataset="SalesFact", expression="SUM(SalesFact[Revenue])"),
        SMLMetric(
            unique_name="Revenue per Unit",
            dataset="SalesFact",
            expression="DIVIDE(SUM(SalesFact[Revenue]), SUM(SalesFact[Units]))",
        ),
    ]
    modified_model = _build_model("SalesFactModel", metrics=modified_metrics)

    # Step a3: Dry run in Project 1 -> must NOT overwrite baseline, must show '1 added'
    baseline_for_dry_run = repo.get_last_deployed_snapshot(
        project_id="proj-1",
        model_names=["SalesFactModel"],
        target_database="DB",
        target_schema="PUBLIC",
        view_name="SALESFACTMODEL_SEMANTIC",
    )
    assert baseline_for_dry_run.snapshot_id == "snap-copy-1"
    delta_dry = compute_delta(modified_model, base_blob)
    assert len(delta_dry["kinds"]["metrics"]["added"]) == 1
    assert delta_dry["kinds"]["metrics"]["added"][0]["name"] == "Revenue per Unit"

    # Step a4: Upsert sync in Project 1
    # Deploys snapshot snap-upsert-1
    modified_blob = modified_model.model_dump(mode="json")
    with Session() as session:
        session.add(Run(run_id="run-upsert-1", project_id="proj-1", run_type="SYNC", status="success", target_type="snowflake", started_at=now))
        session.add(
            SnapshotRow(
                snapshot_id="snap-upsert-1",
                project_id="proj-1",
                run_id="run-upsert-1",
                timestamp=datetime.now(timezone.utc),
                status="success",
                deployed_at=datetime.now(timezone.utc),
                sml_blob=json.dumps(modified_blob),
                view_name="SALESFACTMODEL_SEMANTIC",
                target_database="DB",
                target_schema="PUBLIC",
            )
        )
        session.commit()

    # Scenario c: Upsert with no change
    latest_baseline = repo.get_last_deployed_snapshot(
        project_id="proj-1",
        model_names=["SalesFactModel"],
        target_database="DB",
        target_schema="PUBLIC",
        view_name="SALESFACTMODEL_SEMANTIC",
    )
    assert latest_baseline.snapshot_id == "snap-upsert-1"
    delta_no_change = compute_delta(modified_model, modified_blob)
    assert delta_no_change["has_changes"] is False
    assert delta_no_change["summary_text"].startswith("No changes")

    # Scenario b: Different projects, same target view
    # Project B targets the same view DB.PUBLIC.SALESFACTMODEL_SEMANTIC
    with Session() as session:
        session.add(Project(project_id="proj-2", name="Project 2", database="DB", schema="PUBLIC"))
        session.commit()

    # Upsert in Project B finds the baseline from Project 1 via target view
    baseline_b = repo.get_last_deployed_snapshot(
        project_id="proj-2",
        model_names=["SalesFactModel"],
        target_database="DB",
        target_schema="PUBLIC",
        view_name="SALESFACTMODEL_SEMANTIC",
    )
    assert baseline_b is not None
    assert baseline_b.snapshot_id == "snap-upsert-1"  # found latest deployment across projects
    assert baseline_b.project_id == "proj-1"

    # Scenario d: Restore: Copy sync with the original PBIX
    # Run Copy sync with original base_model on Project 1
    with Session() as session:
        session.add(Run(run_id="run-restore", project_id="proj-1", run_type="SYNC", status="success", target_type="snowflake", started_at=now))
        session.add(
            SnapshotRow(
                snapshot_id="snap-restored",
                project_id="proj-1",
                run_id="run-restore",
                timestamp=datetime.now(timezone.utc),
                status="success",
                deployed_at=datetime.now(timezone.utc),
                sml_blob=json.dumps(base_blob),
                view_name="SALESFACTMODEL_SEMANTIC",
                target_database="DB",
                target_schema="PUBLIC",
            )
        )
        session.commit()

    restored_baseline = repo.get_last_deployed_snapshot(
        project_id="proj-1",
        model_names=["SalesFactModel"],
        target_database="DB",
        target_schema="PUBLIC",
        view_name="SALESFACTMODEL_SEMANTIC",
    )
    assert restored_baseline.snapshot_id == "snap-restored"
    restored_payload = (
        json.loads(restored_baseline.sml_blob)
        if isinstance(restored_baseline.sml_blob, str)
        else restored_baseline.sml_blob
    )
    assert restored_payload == base_blob

