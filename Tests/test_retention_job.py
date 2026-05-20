from datetime import datetime, timedelta
import uuid

from semabridge.repository.model_repository import ModelRepository
from semabridge.repository.orm.models import SnapshotRow, RelationshipRow, Project
from semabridge.tasks.retention import purge_old_relationships
from sqlalchemy import select


def test_purge_old_relationships():
    repo = ModelRepository(url_override="sqlite:///:memory:")
    project_id = f"p-{uuid.uuid4()}"

    # Prepare DB rows manually to control timestamps
    now = datetime.utcnow()
    old_ts = now - timedelta(days=60)

    with repo._session() as session:
        # Ensure project exists
        session.add(Project(project_id=project_id, name="test", workspace_id="w"))
        # Recent snapshot
        recent_sid = str(uuid.uuid4())
        session.add(SnapshotRow(snapshot_id=recent_sid, project_id=project_id, timestamp=now, sml_blob='{}'))
        # Old snapshot
        old_sid = str(uuid.uuid4())
        session.add(SnapshotRow(snapshot_id=old_sid, project_id=project_id, timestamp=old_ts, sml_blob='{}'))
        session.commit()

        # Add relationships for each snapshot
        r1 = RelationshipRow(id=str(uuid.uuid4()), snapshot_id=recent_sid, from_table='A', from_column='id', to_table='B', to_column='id')
        r2 = RelationshipRow(id=str(uuid.uuid4()), snapshot_id=old_sid, from_table='C', from_column='id', to_table='D', to_column='id')
        session.add_all([r1, r2])
        session.commit()

        # Purge with 30-day retention: should delete the old one only
        deleted = purge_old_relationships(session, days=30, project_id=project_id)
        assert deleted >= 1

        # Verify remaining rows: only the recent one should remain
        rows = session.execute(select(RelationshipRow)).scalars().all()
        assert any(r.snapshot_id == recent_sid for r in rows)
        assert all(r.snapshot_id != old_sid for r in rows)
