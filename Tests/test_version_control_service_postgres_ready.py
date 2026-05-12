from pathlib import Path

from semabridge.api.services.version_control_service import VersionControlService
from semabridge.repository.model_repository import ModelRepository


def test_list_versions_uses_repository_model_discovery(tmp_path: Path):
    repo = ModelRepository(url_override="sqlite:///:memory:")
    repo.insert_model_version(
        model_id="db_model_a",
        workspace_id="ws1",
        snapshot={"name": "a"},
        author="test",
    )
    repo.insert_model_version(
        model_id="db_model_b",
        workspace_id="ws1",
        snapshot={"name": "b"},
        author="test",
    )

    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "local_only.yaml").write_text("name: local_only\n", encoding="utf-8")

    svc = VersionControlService(
        db_manager=repo,
        models_path_resolver=lambda: models_dir,
    )

    versions = svc.list_versions(workspace_id="ws1", limit=50)
    model_ids = {v["model_id"] for v in versions}

    assert "db_model_a" in model_ids
    assert "db_model_b" in model_ids
    assert all(v.get("source") == "model_versions" for v in versions)
    assert all(v.get("can_compare") is True for v in versions)
    assert all(v.get("can_rollback") is True for v in versions)
