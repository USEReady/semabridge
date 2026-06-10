"""
FastAPI dependency injection factories.

Replace direct imports from project_shared with these Depends() providers.
Controllers receive dependencies via function parameters:

    @router.get("/projects")
    async def list_projects(engine: ExecutionEngine = Depends(get_engine)):
        ...

All factories are request-scoped (new instance per request) unless decorated
with @lru_cache (settings only).
"""
from __future__ import annotations
from functools import lru_cache
from fastapi import Depends, Request

# -- Settings (process-wide singleton via lru_cache) --------------------------


@lru_cache(maxsize=1)
def get_settings_cached():
    from semabridge.core.settings import get_settings
    return get_settings()


def get_settings_dep():
    return get_settings_cached()


# -- Core services ------------------------------------------------------------


def get_db_manager(settings=Depends(get_settings_dep)):
    from semabridge.repository.model_repository import ModelRepository
    return ModelRepository()


def get_engine(db_manager=Depends(get_db_manager)):
    from semabridge.core.execution_engine import ExecutionEngine
    return ExecutionEngine(db_manager=db_manager)


def get_scheduler():
    from semabridge.api.services.scheduler_service import SchedulerService
    return SchedulerService()


# -- Current user (convenience re-export of existing auth dep) ----------------


def get_current_user(request: Request):
    """Re-export of the existing auth dependency for convenience."""
    from semabridge.api.deps import get_current_user as _get_current_user
    return _get_current_user(request)
