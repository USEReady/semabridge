"""REST API Router for Microsoft Fabric Direct Cloud Deployment."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException

from semabridge.connectors.fabric_cloud_deployer import (
    FabricCloudDeployer,
    FabricDeploymentConfig,
    FabricDeploymentReceipt,
)
from semabridge.ontology.osi_export import ontology_osi_model

logger = logging.getLogger("semabridge.api.fabric_cloud_router")

router = APIRouter(prefix="/api/fabric", tags=["fabric_cloud"])


@router.post("/deploy", response_model=FabricDeploymentReceipt)
def deploy_to_fabric_cloud(config: FabricDeploymentConfig, project_id: Optional[str] = None) -> FabricDeploymentReceipt:
    """Deploy the ontology (one project's with ``?project_id=``) to a Microsoft Fabric workspace."""
    if not config.workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")

    osi_model = ontology_osi_model(config.dataset_name, project_id)

    deployer = FabricCloudDeployer(config)
    receipt = deployer.deploy_model(osi_model)
    return receipt


@router.post("/refresh/{dataset_id}", response_model=Dict[str, Any])
def refresh_fabric_dataset(workspace_id: str, dataset_id: str) -> Dict[str, Any]:
    """Trigger a refresh on a deployed Microsoft Fabric dataset."""
    config = FabricDeploymentConfig(workspace_id=workspace_id, dataset_name="dataset")
    deployer = FabricCloudDeployer(config)
    return deployer.trigger_dataset_refresh(dataset_id)
