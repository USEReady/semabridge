"""
Export Router.

Provides endpoints for exporting SemaBridge semantic models to external formats.

Endpoints:
    POST /export/projects/{project_id}/pbix-via-fabric
        — Publish the project's OSI model to a Fabric workspace as a semantic model.
          The user then downloads the .pbix from the Fabric workspace UI.
          This is the only viable .pbix export path because the .pbix binary format
          is proprietary and not writeable outside the Fabric API.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("semabridge.api.export")

router = APIRouter(prefix="/export", tags=["export"])


# =============================================================================
# Request / Response schemas
# =============================================================================


class PbixViaFabricRequest(BaseModel):
    """Request to export a project to .pbix via Fabric REST API proxy."""

    workspace_id: str = Field(
        ...,
        description="Target Fabric workspace ID where the model will be published. "
        "The user can then download the .pbix from the Fabric UI.",
    )
    model_name: Optional[str] = Field(
        default=None,
        description="Override the semantic model name in Fabric. "
        "Defaults to the project name.",
    )
    overwrite_existing: bool = Field(
        default=True,
        description="If True, overwrites an existing model with the same name in the workspace.",
    )
    snapshot_id: Optional[str] = Field(
        default=None,
        description="Snapshot ID to export. Defaults to the latest snapshot.",
    )


class PbixViaFabricResponse(BaseModel):
    """Response from the pbix-via-fabric export endpoint."""

    status: str
    fabric_workspace_id: str
    model_name: str
    fabric_workspace_url: str
    message: str


# =============================================================================
# Export Endpoints
# =============================================================================


@router.post(
    "/projects/{project_id}/pbix-via-fabric",
    response_model=PbixViaFabricResponse,
    summary="Export project as .pbix via Fabric REST API",
    description=(
        "Publishes the project's latest semantic model to a Fabric workspace "
        "as a TMSL semantic model. The user can then download the .pbix directly "
        "from the Fabric workspace UI.\n\n"
        "Note: Direct .pbix file generation is not possible because the .pbix binary "
        "format is proprietary. This endpoint uses the Fabric REST API as a proxy."
    ),
)
async def export_pbix_via_fabric(
    project_id: str,
    request: PbixViaFabricRequest,
) -> Dict[str, Any]:
    """Publish the project OSI model to Fabric and return a workspace URL."""
    try:
        # 1. Load the OSI model from the project snapshot
        osi_model = await _load_osi_model(project_id, request.snapshot_id)
        if osi_model is None:
            raise HTTPException(
                status_code=404,
                detail=f"No snapshot found for project '{project_id}'. "
                "Run the project at least once before exporting.",
            )

        model_name = request.model_name or osi_model.label or osi_model.unique_name

        # 2. Generate TMSL from OSI
        from semabridge.connectors.tmsl_generator import TMSLGenerator
        tmsl_gen = TMSLGenerator()
        tmsl_payload = tmsl_gen.generate(osi_model)

        # 3. Publish to Fabric workspace
        from semabridge.connectors.fabric_publisher import FabricPublisher
        from semabridge.core.settings import get_settings
        settings = get_settings()
        publisher = FabricPublisher(settings.fabric)

        publish_result = publisher.publish(
            tmsl_payload=tmsl_payload,
            workspace_id=request.workspace_id,
            model_name=model_name,
            overwrite=request.overwrite_existing,
        )

        # Build the Fabric workspace URL
        fabric_base = "https://app.fabric.microsoft.com/groups"
        workspace_url = f"{fabric_base}/{request.workspace_id}"

        logger.info(
            f"Project '{project_id}' exported to Fabric workspace '{request.workspace_id}' "
            f"as '{model_name}'"
        )

        return PbixViaFabricResponse(
            status="published",
            fabric_workspace_id=request.workspace_id,
            model_name=model_name,
            fabric_workspace_url=workspace_url,
            message=(
                f"Model '{model_name}' published to Fabric workspace. "
                "Open the workspace URL to download the .pbix file."
            ),
        ).model_dump()

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Export failed for project '{project_id}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Internal helpers
# =============================================================================


async def _load_osi_model(project_id: str, snapshot_id: Optional[str] = None):
    """Load the OSI model from the project's latest (or specified) snapshot.

    Returns None if no snapshot exists.
    """
    try:
        from semabridge.api.dependencies import get_db_session
        from semabridge.repository.db import ModelRepository

        repo = ModelRepository()
        # Try to get the snapshot from the version history
        snapshot = repo.get_snapshot(project_id, snapshot_id=snapshot_id)
        if snapshot is None:
            return None

        # Deserialise the OSI model from the snapshot
        from semabridge.intermediate.models import OSIModel
        import json

        raw = snapshot.get("osi_model") or snapshot.get("model_data") or snapshot
        if isinstance(raw, str):
            raw = json.loads(raw)
        if isinstance(raw, dict):
            return OSIModel(**raw)
        return None
    except Exception as e:
        logger.warning(f"Could not load OSI model for project '{project_id}': {e}")
        return None
