"""
Service container for CLI commands and background tasks.

HTTP endpoints should use FastAPI Depends (api/dependencies.py) instead.

Usage:
    from semabridge.core.container import get_container
    container = get_container()
    result = container.engine.run(...)
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import cached_property
from typing import Optional


@dataclass
class ServiceContainer:
    """
    Lightweight lazy service container.

    All properties are cached after first access (cached_property).
    Create a new instance to reset all services.
    """

    @cached_property
    def settings(self):
        from semabridge.core.settings import get_settings
        return get_settings()

    @cached_property
    def db_manager(self):
        from semabridge.repository.model_repository import ModelRepository
        return ModelRepository()

    @cached_property
    def engine(self):
        from semabridge.core.execution_engine import ExecutionEngine
        return ExecutionEngine(db_manager=self.db_manager)

    @cached_property
    def scheduler_service(self):
        from semabridge.api.services.scheduler_service import SchedulerService
        return SchedulerService()

    @cached_property
    def version_control_service(self):
        """Returns None if VersionControlService is not configured."""
        try:
            from semabridge.api.services.version_control_impl import VersionControlService
            return VersionControlService()
        except Exception:
            return None


_container: Optional[ServiceContainer] = None


def get_container() -> ServiceContainer:
    """Get the global service container (created on first call)."""
    global _container
    if _container is None:
        _container = ServiceContainer()
    return _container


def reset_container() -> None:
    """Reset the global container (useful for testing)."""
    global _container
    _container = None
