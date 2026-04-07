from fastapi import APIRouter

from semabridge.api.controllers.connections_controller import router as connections_router
from semabridge.api.controllers.databricks_connection_controller import router as databricks_connection_router
from semabridge.api.controllers.fabric_connection_controller import router as fabric_connection_router

router = APIRouter()

for child_router in [
    connections_router,
    fabric_connection_router,
    databricks_connection_router,
]:
    router.include_router(child_router)
