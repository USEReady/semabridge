from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request

from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
)
from semabridge.api.services.version_control_impl import version_backend
from semabridge.api.services.versioning_service import (
    compare_model_versions,
    delete_model_versions,
    get_version_snapshot,
    list_model_versions,
    rollback_model_version,
)

router = APIRouter()
router.get("/api/model-versions")(list_model_versions)
router.get("/api/model-versions/compare")(compare_model_versions)
router.delete("/api/model-versions")(delete_model_versions)
router.get("/api/model-versions/snapshot")(get_version_snapshot)
router.post("/api/model-versions/rollback")(rollback_model_version)


def _assert_project_access(project_id: str, user_id: str | None) -> None:
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id, log_prefix="VersionControlAuth"):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


@router.get("/api/version-control/stats")
async def get_vc_stats(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await version_backend.get_storage_stats(project_id)


@router.post("/api/version-control/retention")
async def run_retention_policy(
    project_id: str,
    request: Request,
    days_to_keep: int = Query(default=30, ge=1),
    min_versions_to_keep: int = Query(default=5, ge=1),
    strategy: str = Query(default="days"),
    max_snapshots: Optional[int] = Query(default=None, ge=1),
    prune_manual: bool = Query(default=False),
):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await version_backend.apply_retention_policy(
        project_id,
        days_to_keep=days_to_keep,
        min_versions_to_keep=min_versions_to_keep,
        strategy=strategy,
        max_snapshots=max_snapshots,
        prune_manual=prune_manual,
    )


@router.put("/api/version-control/retention-policy")
async def set_retention_policy(
    project_id: str,
    request: Request,
    strategy: str = Query(default="unlimited"),
    max_snapshots: Optional[int] = Query(default=None, ge=1),
    max_age_days: Optional[int] = Query(default=None, ge=1),
    prune_manual: bool = Query(default=False),
):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await version_backend.set_retention_policy(
        project_id,
        strategy=strategy,
        max_snapshots=max_snapshots,
        max_age_days=max_age_days,
        prune_manual=prune_manual,
    )


@router.get("/api/version-control/retention-policy")
async def get_retention_policy(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    _assert_project_access(project_id, user_id)
    return await version_backend.get_retention_policy(project_id)
