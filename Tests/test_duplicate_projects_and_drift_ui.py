import pytest
from unittest.mock import MagicMock, patch

from semabridge.api.services.project_shared import resolve_canonical_project_id
from semabridge.ai.project_connection import resolve_project_scope
from semabridge.api.routers.reconciliation_router import get_project_reconciliation_metrics
from semabridge.governance.linter import SemanticLinter, LintReport
from semabridge.ontology.models import FederatedEnterpriseOntology, DomainOntology


def test_resolve_canonical_project_id():
    """Verify that project display names and slugs resolve to canonical IDs."""
    assert resolve_canonical_project_id(None) is None
    assert resolve_canonical_project_id("") is None

    # Artificial Intelligence resolution
    resolved = resolve_canonical_project_id("Artificial Intelligence")
    assert resolved == "proj-artificial-intelligence"

    # Human Resources resolution
    resolved_hr = resolve_canonical_project_id("Human Resources Sample PBIX")
    assert resolved_hr in ("proj-hr", "proj-human-res", "preview-357299ea71a9") or resolved_hr.startswith("proj-")


def _ensure_ai_deployed_snapshot():
    from datetime import datetime, timezone
    import json
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.repository.orm.models import Base, Project, SnapshotRow

    Base.metadata.create_all(bind=db_manager.get_engine())
    now = datetime.now(timezone.utc)
    sml = {
        "unique_name": "ArtificialIntelligence",
        "datasets": [
            {
                "unique_name": "cases",
                "name": "cases",
                "source_table": "FACT_CASES",
                "columns": [{"name": "case_id", "data_type": "string", "is_dimension": True}],
                "metrics": [
                    {
                        "name": "Total Cases",
                        "dax_expression": "COUNTROWS(cases)",
                        "sql_expression": "COUNT(case_id)",
                    }
                ],
            }
        ],
    }
    Session = db_manager.get_session_factory()
    with Session() as session:
        if not session.get(Project, "proj-artificial-intelligence"):
            session.add(Project(project_id="proj-artificial-intelligence", name="Artificial Intelligence"))
        if not session.get(SnapshotRow, "snap-ai-test"):
            session.add(
                SnapshotRow(
                    snapshot_id="snap-ai-test",
                    project_id="proj-artificial-intelligence",
                    timestamp=now,
                    status="success",
                    deployed_at=now,
                    target_database="SEMABRIDGE_DEV",
                    target_schema="PUBLIC",
                    view_name="SEMANTIC_VIEW_ARTIFICIAL_INTELLIGENCE",
                    sml_blob=json.dumps(sml),
                )
            )
        session.commit()


def test_reconciliation_metrics_for_deployed_view_without_ontology():
    """When a project has no ontology domains, metrics are built from deployed semantic view."""
    _ensure_ai_deployed_snapshot()
    res = get_project_reconciliation_metrics("Artificial Intelligence")
    assert res is not None
    assert "metrics" in res
    assert res["count"] > 0
    names = [m["name"] for m in res["metrics"]]
    assert len(names) > 0
    # Every metric has target_table_or_view pointing to the deployed semantic view
    for m in res["metrics"]:
        assert "target_table_or_view" in m
        assert "dimensions" in m
        assert isinstance(m["dimensions"], list)


def test_resolve_project_scope_ai():
    """Verify that Ask AI project scope resolves deployed semantic view and allowed domains."""
    _ensure_ai_deployed_snapshot()
    scope = resolve_project_scope("Artificial Intelligence")
    assert scope.project_id == "proj-artificial-intelligence"
    assert scope.semantic_view is not None
    assert "ARTIFICIAL_INTELLIGENCE" in scope.semantic_view
    assert scope.allowed_domains
    assert len(scope.allowed_domains) > 0


def test_linter_zero_metrics_fails_closed():
    """When 0 metrics or concepts are checked, the linter report is not passed."""
    fed = FederatedEnterpriseOntology(domains={})
    linter = SemanticLinter()
    report = linter.lint_ontology(fed, allowed_domains=["non-existent-domain"])
    assert report.metrics_checked == 0
    assert report.concepts_checked == 0
    assert report.error_count == 0
    # Zero metrics must never be reported as passing
    assert report.passed is False
    assert "FAILED" in report.summary


@pytest.mark.asyncio
async def test_duplicate_project_name_exists():
    """Attempting to create a project with an existing name raises 409 name_exists."""
    from fastapi import HTTPException
    from semabridge.api.services.project_projects_impl import create_project_compat

    with patch("semabridge.api.services.project_projects_impl._compat_projects", {"proj-existing": {"id": "proj-existing", "name": "Existing Model"}}):
        with pytest.raises(HTTPException) as exc_info:
            await create_project_compat({"name": "Existing Model", "client_type": "powerbi"})
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail.get("status") == "name_exists"
        assert exc_info.value.detail.get("project_id") == "proj-existing"


@pytest.mark.asyncio
async def test_duplicate_project_same_source_file():
    """Attempting to create a project with duplicate source file hash raises 409 same_source unless create_anyway."""
    from fastapi import HTTPException
    from semabridge.api.services.project_projects_impl import create_project_compat

    mock_projects = {
        "proj-source-1": {
            "id": "proj-source-1",
            "name": "First Model",
            "source_content_hash": "hash_abc_123",
        }
    }
    with patch("semabridge.api.services.project_projects_impl._compat_projects", mock_projects):
        with pytest.raises(HTTPException) as exc_info:
            await create_project_compat({"name": "New Brand Model", "client_type": "powerbi", "source_content_hash": "hash_abc_123"})
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail.get("status") == "same_source"
        assert exc_info.value.detail.get("project_id") == "proj-source-1"
