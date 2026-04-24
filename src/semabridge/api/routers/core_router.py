from fastapi import APIRouter

from semabridge.api.controllers.config_controller import router as config_router
from semabridge.api.controllers.discovery_controller import router as discovery_router
from semabridge.api.controllers.health_controller import router as health_router
from semabridge.api.controllers.history_controller import router as history_router
from semabridge.api.controllers.model_controller import router as model_router
from semabridge.api.routers.comparator_router import router as comparator_router
from semabridge.api.controllers.semantic_controller import router as semantic_router

router = APIRouter()

for child_router in [
    health_router,
    discovery_router,
    semantic_router,
    model_router,
    config_router,
    history_router,
    comparator_router,
]:
    router.include_router(child_router)
