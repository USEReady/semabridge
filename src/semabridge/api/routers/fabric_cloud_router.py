"""REST API Router for Microsoft Fabric Direct Cloud Deployment."""

from __future__ import annotations

import logging
from typing import Any, Dict
from fastapi import APIRouter, HTTPException

from semabridge.connectors.fabric_cloud_deployer import (
    FabricCloudDeployer,
    FabricDeploymentConfig,
    FabricDeploymentReceipt,
)
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.fabric_cloud_router")

router = APIRouter(prefix="/api/fabric", tags=["fabric_cloud"])


@router.post("/deploy", response_model=FabricDeploymentReceipt)
def deploy_to_fabric_cloud(config: FabricDeploymentConfig) -> FabricDeploymentReceipt:
    """Deploy the current enterprise semantic model directly to a Microsoft Fabric workspace."""
    if not config.workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required.")

    fed = ontology_store.get_federated_ontology()
    osi_model = FederatedOntologyLoader.to_osi_model(fed, model_name=config.dataset_name)

    deployer = FabricCloudDeployer(config)
    receipt = deployer.deploy_model(osi_model)
    return receipt


@router.post("/refresh/{dataset_id}", response_model=Dict[str, Any])
def refresh_fabric_dataset(workspace_id: str, dataset_id: str) -> Dict[str, Any]:
    """Trigger a refresh on a deployed Microsoft Fabric dataset."""
    config = FabricDeploymentConfig(workspace_id=workspace_id, dataset_name="dataset")
    deployer = FabricCloudDeployer(config)
    return deployer.trigger_dataset_refresh(dataset_id)
