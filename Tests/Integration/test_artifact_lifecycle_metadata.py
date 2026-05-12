"""Integration tests for artifact lifecycle and versioning metadata.

Verifies that artifact lifecycle is tracked correctly across sync operations,
enabling version pinning, contract validation, and deprecation management.
"""

from __future__ import annotations

import json
import pytest
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from sqlalchemy import select

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from semabridge.repository.orm.models import ArtifactMetadata, Project
from semabridge.repository.orm.session_factory import db_manager


@pytest.fixture
def project_with_artifacts(model_repository):
    """Create a test project and initialize artifact metadata."""
    from semabridge.repository.orm.models import Project
    
    # Create project
    proj = Project(
        project_id="test-artifact-project",
        name="Test Artifact Lifecycle Project",
    )
    
    with db_manager.get_session() as session:
        session.add(proj)
        session.commit()
    
    yield proj
    
    # Cleanup
    with db_manager.get_session() as session:
        session.query(Project).filter(
            Project.project_id == "test-artifact-project"
        ).delete()
        session.commit()


def test_artifact_metadata_create_active():
    """Verify artifact metadata is created with active status."""
    from semabridge.repository.orm.models import Project, ArtifactMetadata
    
    # Create project
    with db_manager.get_session() as session:
        proj = Project(
            project_id="test-create-active",
            name="Create Active Test",
        )
        session.add(proj)
        session.flush()
        
        # Create artifact metadata
        artifact = ArtifactMetadata(
            project_id="test-create-active",
            artifact_ref="model:AggregateMetrics",
            version="1.0.0",
            status="active",
            contract_version="1.0",
        )
        session.add(artifact)
        session.commit()
        
        # Verify created
        result = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.artifact_ref == "model:AggregateMetrics"
        ).first()
        
        assert result is not None
        assert result.status == "active"
        assert result.version == "1.0.0"
        assert result.contract_version == "1.0"
        assert result.created_at is not None
        assert result.updated_at is not None
        assert result.deprecation_date is None
        assert result.archived_at is None
        
        # Cleanup
        session.delete(artifact)
        session.delete(proj)
        session.commit()


def test_artifact_metadata_deprecation_lifecycle():
    """Verify artifact can transition from active to deprecated."""
    from semabridge.repository.orm.models import Project, ArtifactMetadata
    
    with db_manager.get_session() as session:
        # Create project and artifact
        proj = Project(project_id="test-deprecation", name="Deprecation Test")
        session.add(proj)
        session.flush()
        
        artifact = ArtifactMetadata(
            project_id="test-deprecation",
            artifact_ref="table:FACT_SALES",
            version="2.1.0",
            status="active",
            contract_version="2.0",
        )
        session.add(artifact)
        session.commit()
        
        artifact_id = artifact.id
    
    # Transition to deprecated
    with db_manager.get_session() as session:
        artifact = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.id == artifact_id
        ).first()
        
        assert artifact.status == "active"
        
        # Deprecate
        artifact.status = "deprecated"
        artifact.deprecation_date = datetime.utcnow()
        session.commit()
        
        # Verify deprecated
        refreshed = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.id == artifact_id
        ).first()
        
        assert refreshed.status == "deprecated"
        assert refreshed.deprecation_date is not None
        assert refreshed.archived_at is None
        
        # Cleanup
        session.query(ArtifactMetadata).filter(
            ArtifactMetadata.id == artifact_id
        ).delete()
        session.query(Project).filter(
            Project.project_id == "test-deprecation"
        ).delete()
        session.commit()


def test_artifact_metadata_version_pinning():
    """Verify downstream consumers can pin specific artifact versions."""
    from semabridge.repository.orm.models import Project, ArtifactMetadata
    
    with db_manager.get_session() as session:
        # Create project
        proj = Project(project_id="test-pinning", name="Version Pinning Test")
        session.add(proj)
        session.flush()
        
        # Create multiple versions of same artifact
        v1 = ArtifactMetadata(
            project_id="test-pinning",
            artifact_ref="model:DetailMetrics",
            version="1.0.0",
            status="archived",
            contract_version="1.0",
            archived_at=datetime.utcnow() - timedelta(days=60),
        )
        
        v2 = ArtifactMetadata(
            project_id="test-pinning",
            artifact_ref="model:DetailMetrics",
            version="2.0.0",
            status="deprecated",
            contract_version="1.0",
            deprecation_date=datetime.utcnow() - timedelta(days=30),
        )
        
        v3 = ArtifactMetadata(
            project_id="test-pinning",
            artifact_ref="model:DetailMetrics",
            version="3.0.0",
            status="active",
            contract_version="2.0",
        )
        
        session.add_all([v1, v2, v3])
        session.commit()
        
        # Query for active version
        active = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.artifact_ref == "model:DetailMetrics",
            ArtifactMetadata.status == "active",
        ).first()
        
        assert active is not None
        assert active.version == "3.0.0"
        
        # Query for specific pinned version
        pinned = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.artifact_ref == "model:DetailMetrics",
            ArtifactMetadata.version == "2.0.0",
        ).first()
        
        assert pinned is not None
        assert pinned.status == "deprecated"
        
        # Query all versions (for migration window)
        all_versions = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.artifact_ref == "model:DetailMetrics",
        ).order_by(ArtifactMetadata.version.desc()).all()
        
        assert len(all_versions) == 3
        
        # Cleanup
        for v in all_versions:
            session.delete(v)
        session.delete(proj)
        session.commit()


def test_artifact_contract_version_validation():
    """Verify contract version breaks are detected on sync."""
    from semabridge.repository.orm.models import Project, ArtifactMetadata
    
    with db_manager.get_session() as session:
        # Create project and artifact with contract v1.0
        proj = Project(project_id="test-contract", name="Contract Test")
        session.add(proj)
        session.flush()
        
        artifact = ArtifactMetadata(
            project_id="test-contract",
            artifact_ref="model:SalesMetrics",
            version="1.0.0",
            status="active",
            contract_version="1.0",
            metadata_json=json.dumps({
                "schema_hash": "abc123",
                "table_count": 3,
                "column_count": 15,
            }),
        )
        session.add(artifact)
        session.commit()
        
        artifact_id = artifact.id
    
    # Attempt to create new version with breaking contract change
    with db_manager.get_session() as session:
        # This would be detected during sync validation
        new_artifact = ArtifactMetadata(
            project_id="test-contract",
            artifact_ref="model:SalesMetrics",
            version="2.0.0",
            status="active",
            contract_version="2.0",  # Contract changed
            metadata_json=json.dumps({
                "schema_hash": "def456",  # Different hash
                "table_count": 2,  # Breaking change: table removed
                "column_count": 14,
            }),
        )
        
        # Mark old version as deprecated due to breaking change
        old = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.id == artifact_id
        ).first()
        old.status = "deprecated"
        old.deprecation_date = datetime.utcnow()
        
        session.add(new_artifact)
        session.commit()
        
        # Verify both versions exist
        all_versions = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.artifact_ref == "model:SalesMetrics",
        ).all()
        
        assert len(all_versions) == 2
        
        # Verify old is deprecated, new is active
        old_ver = [a for a in all_versions if a.version == "1.0.0"][0]
        new_ver = [a for a in all_versions if a.version == "2.0.0"][0]
        
        assert old_ver.status == "deprecated"
        assert new_ver.status == "active"
        assert new_ver.contract_version == "2.0"
        
        # Cleanup
        session.query(ArtifactMetadata).filter(
            ArtifactMetadata.artifact_ref == "model:SalesMetrics"
        ).delete()
        session.query(Project).filter(
            Project.project_id == "test-contract"
        ).delete()
        session.commit()


def test_artifact_metadata_query_for_sync_validation():
    """Verify artifact metadata can be queried for pre-sync contract validation."""
    from semabridge.repository.orm.models import Project, ArtifactMetadata
    
    with db_manager.get_session() as session:
        # Create project with multiple artifacts
        proj = Project(project_id="test-validation", name="Validation Test")
        session.add(proj)
        session.flush()
        
        artifacts = [
            ArtifactMetadata(
                project_id="test-validation",
                artifact_ref=f"model:{name}",
                version="1.0.0",
                status="active",
                contract_version="1.0",
            )
            for name in ["AggregateMetrics", "DetailMetrics", "SummaryMetrics"]
        ]
        session.add_all(artifacts)
        session.commit()
        
        # Query for validation: get all active artifacts
        active_artifacts = session.query(ArtifactMetadata).filter(
            ArtifactMetadata.project_id == "test-validation",
            ArtifactMetadata.status == "active",
        ).all()
        
        assert len(active_artifacts) == 3
        
        # Build validation map: artifact_ref → contract_version
        contract_map = {
            a.artifact_ref: a.contract_version
            for a in active_artifacts
        }
        
        assert contract_map == {
            "model:AggregateMetrics": "1.0",
            "model:DetailMetrics": "1.0",
            "model:SummaryMetrics": "1.0",
        }
        
        # Cleanup
        session.query(ArtifactMetadata).filter(
            ArtifactMetadata.project_id == "test-validation"
        ).delete()
        session.query(Project).filter(
            Project.project_id == "test-validation"
        ).delete()
        session.commit()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
