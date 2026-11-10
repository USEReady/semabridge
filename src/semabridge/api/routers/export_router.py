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
    """Load the OSI model from the project's latest successful (or a given) snapshot.

    Returns None ONLY when the project has no snapshot (caller -> 404).
    Any other failure raises HTTP 500 with the real reason.
    """
    import json
    from semabridge.intermediate.models import OSIModel
    from semabridge.repository.model_repository import ModelRepository

    repo = ModelRepository()
    # 1. Fetch head snapshot for the project from the persistent SQLite repository
    snap = repo.get_snapshot(snapshot_id) if snapshot_id else repo.get_head(project_id)
    if snap is None or (snapshot_id and snap.project_id != project_id):
        return None
    raw = snap.sml_blob
    if isinstance(raw, (bytes, str)):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise HTTPException(status_code=500, detail=f"Snapshot for '{project_id}' has no model payload.")

    # 2. If the snapshot blob already contains an OSIModel, validate and return directly
    if "datasets" in raw and "relationships" in raw and "unique_name" in raw:
        try:
            return OSIModel.model_validate_json(json.dumps(raw, default=str))
        except Exception:
            pass

    # 3. Otherwise, convert the SML model into an OSI intermediate model
    try:
        from semabridge.converter.sml_to_osi import SMLToOSIConverter
        from semabridge.sml.models import SMLModel
        return SMLToOSIConverter().to_osi(SMLModel(**raw))
    except Exception as e:
        logger.exception("OSI conversion failed for project '%s'", project_id)
        raise HTTPException(status_code=500, detail=f"Could not build OSI model for '{project_id}': {e}")
