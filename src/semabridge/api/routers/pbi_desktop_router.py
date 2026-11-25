"""API router for Power BI Desktop sources and running instance discovery."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter

from semabridge.sources.pbi_desktop.discovery import discover_running_instances

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
