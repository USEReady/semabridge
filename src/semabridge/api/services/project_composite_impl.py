from semabridge.api.services.project_shared import *
from semabridge.domain.exceptions import InternalError

async def register_composite_report(payload: Dict[str, Any]):
    """Register a report and its composite model dependencies.

    Request body:
        {
            "report_name": "Sales Dashboard",
            "source_path": "C:/path/to/report.pbix",
            "workspace_id": "local",
            "connections": [...from PBIX extraction...]
        }
    """
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        report_id = resolver.register_report(
            report_name=payload.get("report_name", ""),
            source_path=payload.get("source_path", ""),
            workspace_id=payload.get("workspace_id", "local"),
            connections=payload.get("connections", []),
        )
        return {"status": "registered", "report_id": report_id}
    except Exception as e:
        logger.exception(f"Composite registration failed: {e}")
        raise InternalError(str(e))


async def get_impact_analysis(model_guid: str):
    """Impact analysis: which reports depend on this semantic model?

    Args:
        model_guid: The upstream semantic model GUID to check.

    Returns:
        List of reports that reference this model.
    """
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        impacted = resolver.get_impacted_reports(model_guid)
        return {"model_guid": model_guid, "impacted_reports": impacted}
    except Exception as e:
        raise InternalError(str(e))


async def get_all_composite_links():
    """List all report Ã¢â€ â€™ model dependency links."""
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        links = resolver.list_all_links()
        return {"links": links, "total": len(links)}
    except Exception as e:
        raise InternalError(str(e))