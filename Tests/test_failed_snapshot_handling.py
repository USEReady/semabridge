import uuid

from semabridge.repository.model_repository import ModelRepository

def test_failed_snapshot_written_and_head_ignores_failed(model_repository, sample_sml_model):
    repo = model_repository

    project_id = f"proj_failed_{uuid.uuid4().hex[:8]}"
    repo.ensure_project(project_id, "Test Project", "ws1")

    sml = sample_sml_model.model_dump(mode="json")

    # Commit a successful snapshot
    committed, success_id = repo.commit_model(project_id, sml, tag="v1")
    assert committed is True

    # Simulate a failed sync attempt (no sml_blob produced)
    committed_failed, failed_id = repo.commit_model(
        project_id,
        None,
        status="failed",
        error_message="Connection timed out",
        run_id="run_failed_1",
    )
    assert committed_failed is True
    assert failed_id != success_id

    # get_head should return the last successful snapshot (not the failed one)
    head = repo.get_head(project_id)
    assert head is not None
    assert head.snapshot_id == success_id

    # get_snapshot should return the failed row by id and preserve error_message
    fetched_failed = repo.get_snapshot(failed_id)
    assert fetched_failed is not None
    assert fetched_failed.status == "failed"
    assert "Connection timed out" in (fetched_failed.error_message or "")

    # list_snapshots with include_failed=True should include the failed snapshot
    all_snaps = repo.list_snapshots(project_id, limit=10, include_failed=True)
    ids = {s.snapshot_id for s in all_snaps}
    assert failed_id in ids and success_id in ids


def test_failed_snapshots_clear_payload(tmp_path, sample_sml_model):
    repo = ModelRepository(url_override=f"sqlite:///{tmp_path / 'failed_snapshot_payload.db'}")

    project_id = f"proj_failed_payload_{uuid.uuid4().hex[:8]}"
    repo.ensure_project(project_id, "Test Project", "ws1")

    sml = sample_sml_model.model_dump(mode="json")

    committed, snapshot_id = repo.commit_model(project_id, sml, tag="v1")
    assert committed is True

    updated = repo.update_snapshot_status(snapshot_id, "failed", "Deployment failed")
    assert updated is True

    failed_snapshot = repo.get_snapshot(snapshot_id)
    assert failed_snapshot is not None
    assert failed_snapshot.status == "failed"
    assert failed_snapshot.sml_blob == {}

    committed_failed, failed_id = repo.commit_model(
        project_id,
        sml,
        status="failed",
        error_message="Connection timed out",
        run_id="run_failed_2",
    )
    assert committed_failed is True

    direct_failed_snapshot = repo.get_snapshot(failed_id)
    assert direct_failed_snapshot is not None
    assert direct_failed_snapshot.status == "failed"
    assert direct_failed_snapshot.sml_blob == {}
