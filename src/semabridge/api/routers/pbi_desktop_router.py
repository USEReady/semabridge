"""API router for Power BI Desktop sources and running instance discovery."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from semabridge.core.feature_flags import is_pbi_desktop_source_enabled
from semabridge.sources.base import SourceError, SourceNotFoundError
from semabridge.sources.pbi_desktop.discovery import DiscoveredDesktopInstance, discover_running_instances
from semabridge.sources.pbi_desktop.model_reader import read_tmsl_from_engine
from semabridge.sources.pbi_desktop.preselection import validate_preselected_desktop_source

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources/pbi-desktop", tags=["pbi-desktop"])


@router.get("/status")
def desktop_source_status() -> Dict[str, Any]:
    """Check if Power BI Desktop source is enabled via feature flag."""
    return {"enabled": is_pbi_desktop_source_enabled()}


@router.get("/instances")
def list_desktop_instances() -> Dict[str, Any]:
    """List running Power BI Desktop Analysis Services instances."""
    if not is_pbi_desktop_source_enabled():
        return {
            "instances": [],
            "total": 0,
            "enabled": False,
        }
    try:
        instances = discover_running_instances(verify_active=True)
        serialized = [inst.to_dict() for inst in instances]
        return {
            "instances": serialized,
            "total": len(serialized),
            "enabled": True,
        }
    except Exception as exc:
        logger.error(f"Failed to discover running Power BI Desktop instances: {exc}", exc_info=True)
        return {
            "instances": [],
            "total": 0,
            "error": str(exc),
            "enabled": True,
        }


@router.get("/inspect")
def inspect_desktop_model(
    file_path: str = Query(..., description="Full path or file name of the Power BI Desktop file"),
) -> Dict[str, Any]:
    """Inspect a currently open Power BI Desktop model (read-only counts of tables, measures, relationships)."""
    if not is_pbi_desktop_source_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Power BI Desktop source is disabled (feature flag 'pbi_desktop_source' is off).",
        )

    clean_req = str(file_path).strip().strip('"').strip("'")
    req_name = Path(clean_req).name.lower()
    req_stem = Path(clean_req).stem.lower()

    try:
        instances = discover_running_instances(verify_active=True)
    except Exception as exc:
        logger.error(f"Discovery failed during inspect: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Discovery failed: {exc}",
        )

    matched: Optional[DiscoveredDesktopInstance] = None
    for inst in instances:
        inst_name = inst.file_name.lower()
        inst_stem = Path(inst.file_name).stem.lower()
        inst_full = (inst.file_path or "").lower()
        try:
            path_matches = bool(inst_full and Path(clean_req).resolve() == Path(inst.file_path).resolve())
        except Exception:
            path_matches = False

        if clean_req.lower() == inst_name or req_name == inst_name or req_stem == inst_stem or path_matches:
            matched = inst
            break

    if not matched:
        display_name = Path(clean_req).name or clean_req
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Open {display_name} in Power BI Desktop and try again",
        )

    try:
        tmsl = read_tmsl_from_engine(port=matched.port, database_name=matched.database_name)
        model = (tmsl or {}).get("model", {})
        tables = model.get("tables", [])
        user_tables = [
            t for t in tables
            if not str(t.get("name", "")).startswith("LocalDateTable_")
            and not str(t.get("name", "")).startswith("DateTableTemplate_")
        ]
        measures_count = sum(len(t.get("measures", [])) for t in tables)
        relationships_count = len(model.get("relationships", []))

        return {
            "file_path": matched.file_path or clean_req,
            "file_name": matched.file_name,
            "tables_count": len(user_tables) if user_tables else len(tables),
            "measures_count": measures_count,
            "relationships_count": relationships_count,
            "has_unsaved_changes": matched.has_unsaved_changes,
            "is_live_connected": matched.is_live_connected,
        }
    except Exception as exc:
        logger.error(f"Failed to inspect model '{matched.file_name}': {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to inspect model: {exc}",
        )


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
