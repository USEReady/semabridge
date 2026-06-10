"""Semantic / sync API controller.

Routes:
    POST /api/semantic/sync      — semantic-aware sync
    POST /api/semantic/refresh    — refresh semantic metadata
    POST /api/sync               — full sync pipeline
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel


class TriggerSyncRequest(BaseModel):
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    sync_mode: Optional[str] = None

    class Config:
        extra = "allow"

from semabridge.api.services.core_domain_service import semantic_refresh, semantic_sync, sync_models

router = APIRouter()
router.post('/api/semantic/sync', response_model=None)(semantic_sync)
router.post('/api/semantic/refresh', response_model=None)(semantic_refresh)


@router.post("/api/sync")
async def trigger_sync(request: Request, payload: TriggerSyncRequest) -> Any:
    """Trigger a full sync with user-scoped credential isolation.

    When ``AUTH_ENABLED=true``, inject the authenticated ``user_id``
    from the JWT into the sync payload so downstream project-scoped
    sync flows can enforce direct project ownership.
    """
    payload_dict = payload.model_dump(exclude_none=False)
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            payload_dict["user_id"] = user_id

    return await sync_models(payload_dict)
