"""API router for Power BI Desktop sources and running instance discovery."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, status

from semabridge.sources.base import SourceError, SourceNotFoundError
from semabridge.sources.pbi_desktop.discovery import discover_running_instances
from semabridge.sources.pbi_desktop.preselection import validate_preselected_desktop_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources/pbi-desktop", tags=["pbi-desktop"])


@router.get("/instances")
def list_desktop_instances() -> Dict[str, Any]:
    """List running Power BI Desktop Analysis Services instances."""
    try:
        instances = discover_running_instances(verify_active=True)
        serialized = [inst.to_dict() for inst in instances]
        return {
            "instances": serialized,
            "total": len(serialized),
        }
    except Exception as exc:
        logger.error(f"Failed to discover running Power BI Desktop instances: {exc}", exc_info=True)
        return {
            "instances": [],
            "total": 0,
            "error": str(exc),
        }


@router.get("/validate-preselection")
def validate_preselection(
    server: str = Query(..., description="Desktop Analysis Services server (localhost:<port>)"),
    database: str = Query(..., description="Analysis Services database GUID/name"),
) -> Dict[str, Any]:
    """Validate a pre-selected Power BI Desktop instance from launcher params."""
    try:
        matched = validate_preselected_desktop_source(server, database)
        return {
            "valid": True,
            "instance": matched.to_dict(),
        }
    except SourceNotFoundError as exc:
        logger.info(f"Pre-selected Desktop instance not found: {exc}")
        return {
            "valid": False,
            "error": "That file isn't open anymore",
            "message": str(exc),
            "show_picker": True,
        }
    except SourceError as exc:
        logger.warning(f"Rejected pre-selected Desktop server: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.error(f"Unexpected error validating preselection: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
