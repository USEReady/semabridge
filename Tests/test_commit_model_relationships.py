import uuid
from semabridge.repository.model_repository import ModelRepository
from semabridge.repository.orm.models import RelationshipRow
from sqlalchemy import select


def test_commit_model_persists_relationships():
    repo = ModelRepository(url_override="sqlite:///:memory:")
    project_id = f"test-project-{uuid.uuid4()}"

    sml_json = {
        "relationships": [
            {
                "from_dataset": "dbo.Customers",
                "to_dataset": "dbo.Orders",
                "from_columns": ["CustomerID"],
                "to_columns": ["CustomerID"],
                "cardinality": "OneToMany",
                "cross_filter": "BothDirections",
                "is_active": True,
            }
        ]
    }

    committed, snapshot_id = repo.commit_model(project_id, sml_json, tag="v1")
    assert committed is True
    assert snapshot_id

    # Verify relationship rows were persisted for this snapshot
    with repo._session() as session:
        rows = session.execute(
            select(RelationshipRow).where(RelationshipRow.snapshot_id == snapshot_id)
        ).scalars().all()

    assert len(rows) == 1
    rel = rows[0]
    assert rel.from_table == "dbo.Customers"
    assert rel.to_table == "dbo.Orders"
    assert rel.from_column == "CustomerID"
    assert rel.to_column == "CustomerID"
    assert rel.cardinality == "OneToMany"
    assert rel.cross_filter_behavior == "BothDirections"
    assert rel.is_active is True
