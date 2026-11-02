"""REST API Router for Databricks Unity Catalog Metric Views & Genie Space."""

from __future__ import annotations

import logging
from typing import Any, Dict
from fastapi import APIRouter
from pydantic import BaseModel

from semabridge.connectors.databricks_metric_emitter import DatabricksMetricEmitter
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.databricks_metric_router")

router = APIRouter(prefix="/api/databricks/metrics", tags=["databricks_metrics"])


class DatabricksEmitRequest(BaseModel):
    model_name: str = "ENTERPRISE_MODEL"
    catalog: str = "prod_catalog"
    schema_name: str = "analytics_semantic"


@router.post("/emit-ddl", response_model=Dict[str, str])
def emit_unity_catalog_ddl(req: DatabricksEmitRequest) -> Dict[str, str]:
    """Emit Databricks Unity Catalog Metric View DDL with column comments and governance tags."""
    fed = ontology_store.get_federated_ontology()
    osi_model = FederatedOntologyLoader.to_osi_model(fed, model_name=req.model_name)
    emitter = DatabricksMetricEmitter(catalog=req.catalog, schema=req.schema_name)
    ddl = emitter.emit_unity_catalog_ddl(osi_model)
    return {"ddl": ddl}


@router.post("/emit-genie-space", response_model=Dict[str, Any])
def emit_genie_space(req: DatabricksEmitRequest) -> Dict[str, Any]:
    """Emit Databricks Genie AI Semantic Space specification for conversational analytics."""
    fed = ontology_store.get_federated_ontology()
    osi_model = FederatedOntologyLoader.to_osi_model(fed, model_name=req.model_name)
    emitter = DatabricksMetricEmitter(catalog=req.catalog, schema=req.schema_name)
    spec = emitter.emit_genie_space_spec(osi_model)
    return spec
