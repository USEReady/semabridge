"""REST API Router for Cross-Platform Deployment & Multi-Target Parity."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from semabridge.sync.cross_platform import (
    check_cross_platform_parity,
    preview_cross_platform_definitions,
)

router = APIRouter(prefix="/api/cross-platform", tags=["cross_platform"])


class CrossPlatformPreviewRequest(BaseModel):
    project_id: Optional[str] = None
    domain: Optional[str] = None
    targets: List[str] = Field(default_factory=lambda: ["snowflake", "databricks", "fabric"])
    version_tag: str = "v1.0.0"


class CrossPlatformParityRequest(BaseModel):
    project_id: Optional[str] = None
    sql_snowflake: str
    sql_databricks: Optional[str] = None
    snowflake_account_id: Optional[str] = None
    databricks_account_id: Optional[str] = None


@router.post("/preview", response_model=Dict[str, Any])
def preview_targets(req: CrossPlatformPreviewRequest) -> Dict[str, Any]:
    return preview_cross_platform_definitions(
        project_id=req.project_id,
        domain=req.domain,
        targets=req.targets,
        version_tag=req.version_tag,
    )


@router.post("/parity-check", response_model=Dict[str, Any])
def parity_check(req: CrossPlatformParityRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    return check_cross_platform_parity(
        sql_snowflake=req.sql_snowflake,
        sql_databricks=req.sql_databricks,
        project_id=req.project_id,
        owner_id=str(uid) if uid else None,
        snowflake_account_id=req.snowflake_account_id,
        databricks_account_id=req.databricks_account_id,
    )

