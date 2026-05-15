from semabridge.repository.model_repository import ModelRepository
from semabridge.repository.orm.models import CommandLog, Project, Run, SnapshotRow
from semabridge.repository.schema_compat import PROJECT_ID_LENGTH


LONG_SEMANTIC_PROJECT_ID = "COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC"


def test_project_id_columns_allow_semantic_model_names():
    assert len(LONG_SEMANTIC_PROJECT_ID) > 36
    assert Project.__table__.c.project_id.type.length == PROJECT_ID_LENGTH
    assert Run.__table__.c.project_id.type.length == PROJECT_ID_LENGTH
    assert SnapshotRow.__table__.c.project_id.type.length == PROJECT_ID_LENGTH
    assert CommandLog.__table__.c.project_id.type.length == PROJECT_ID_LENGTH


def test_model_repository_persists_long_semantic_project_id():
    repo = ModelRepository("sqlite:///:memory:")

    repo.ensure_project(
        project_id=LONG_SEMANTIC_PROJECT_ID,
        name=LONG_SEMANTIC_PROJECT_ID,
        workspace_id="",
        adapter="snowflake",
    )

    run_id = "98d57518-dc95-425b-90bb-54f4aa1340c0"
    repo.record_run_start(
        run_id=run_id,
        project_id=LONG_SEMANTIC_PROJECT_ID,
        source_type="snowflake",
        target_type="fabric",
    )

    committed, snapshot_id = repo.commit_model(
        project_id=LONG_SEMANTIC_PROJECT_ID,
        sml_json={"model": {"name": LONG_SEMANTIC_PROJECT_ID}},
        run_id=run_id,
    )

    assert committed is True
    assert snapshot_id
    assert repo.get_head(LONG_SEMANTIC_PROJECT_ID).project_id == LONG_SEMANTIC_PROJECT_ID
