"""REST API Router for Reverse Sync (Data Layer -> BI Upgrade-Only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from semabridge.sync.reverse_sync import (
    apply_reverse_upgrades,
    list_snowflake_semantic_views,
    preview_reverse_upgrades,
)

router = APIRouter(prefix="/api/reverse-sync", tags=["reverse_sync"])


class ReversePreviewRequest(BaseModel):
    project_id: Optional[str] = None
    account_id: Optional[str] = None
    semantic_view: Optional[str] = None
    pbip_path: Optional[str] = None


class ReverseApplyRequest(BaseModel):
    pbip_path: str
    upgrades: List[Dict[str, Any]] = Field(default_factory=list)


@router.get("/semantic-views", response_model=Dict[str, Any])
def get_semantic_views(
    request: Request,
    account_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    return list_snowflake_semantic_views(
        account_id=account_id,
        project_id=project_id,
        owner_id=str(uid) if uid else None,
    )


@router.post("/preview", response_model=Dict[str, Any])
def preview_reverse(req: ReversePreviewRequest, request: Request) -> Dict[str, Any]:
    uid = getattr(request.state, "user_id", None)
    return preview_reverse_upgrades(
        project_id=req.project_id,
        account_id=req.account_id,
        semantic_view=req.semantic_view,
        pbip_path=req.pbip_path,
        owner_id=str(uid) if uid else None,
    )


@router.post("/apply", response_model=Dict[str, Any])
def apply_reverse(req: ReverseApplyRequest) -> Dict[str, Any]:
    try:
        return apply_reverse_upgrades(pbip_path=req.pbip_path, upgrades=req.upgrades)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

