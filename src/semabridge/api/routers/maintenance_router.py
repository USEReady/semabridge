"""Admin maintenance endpoints (persistence housekeeping)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter()


def _require_admin(request: Request) -> None:
    from semabridge.api.services.project_ownership_service import require_admin_request

    require_admin_request(request)


@router.get("/api/admin/maintenance/preview-projects")
async def preview_cleanup_report(request: Request, older_than_days: int = Query(default=7, ge=0)):
    """What a preview-project cleanup WOULD delete (never deletes)."""
    _require_admin(request)
    from semabridge.api.services.preview_cleanup_service import cleanup_preview_projects

    return await asyncio.to_thread(cleanup_preview_projects, older_than_days, apply=False)


@router.post("/api/admin/maintenance/preview-projects/cleanup")
async def preview_cleanup_apply(
    request: Request,
    older_than_days: int = Query(default=7, ge=0),
    confirm: bool = Query(default=False),
):
    """Delete stale preview projects. Without ``confirm=true`` it only reports."""
    _require_admin(request)
    from semabridge.api.services.preview_cleanup_service import cleanup_preview_projects

    return await asyncio.to_thread(cleanup_preview_projects, older_than_days, apply=bool(confirm))


@router.get("/api/admin/maintenance/persistence")
async def persistence_status(request: Request):
    """Alembic revision, last config-file import report, config versions."""
    _require_admin(request)
    import semabridge.api.services.project_shared as ps
    from semabridge.repository.alembic_runner import current_revision
    from semabridge.repository.orm.session_factory import get_engine

    def _collect():
        engine = get_engine()
        with engine.connect() as conn:
            revision = current_revision(conn) if engine.dialect.name == "postgresql" else None
        return {
            "alembic_revision": revision,
            "config_import": dict(ps._compat_config_import_report),
        }

    return await asyncio.to_thread(_collect)


@router.get("/api/admin/flags")
async def feature_flags(request: Request):
    """Registered behaviour-changing env flags: default, effective value
    (secrets masked) and whether it differs from the default. Admin only."""
    _require_admin(request)
    from semabridge.core.feature_flags import flag_report, non_default_flags

    return {
        "flags": flag_report(),
        "non_default": [flag.name for flag in non_default_flags()],
    }
