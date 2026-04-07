from fastapi import APIRouter

from semabridge.api.controllers.composite_controller import router as composite_router
from semabridge.api.controllers.folders_controller import router as folders_router
from semabridge.api.controllers.graph_controller import router as graph_router
from semabridge.api.controllers.jobs_controller import router as jobs_router
from semabridge.api.controllers.mappings_controller import router as mappings_router
from semabridge.api.controllers.pbix_controller import router as pbix_router
from semabridge.api.controllers.project_runs_controller import router as project_runs_router
from semabridge.api.controllers.projects_controller import router as projects_router
from semabridge.api.controllers.versioning_controller import router as versioning_router

router = APIRouter()

for child_router in [
    projects_router,
    graph_router,
    project_runs_router,
    folders_router,
    jobs_router,
    mappings_router,
    versioning_router,
    pbix_router,
    composite_router,
]:
    router.include_router(child_router)
