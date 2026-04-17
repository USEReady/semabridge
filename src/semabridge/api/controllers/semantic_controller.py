"""Semantic / sync API controller.

Routes:
    POST /api/semantic/sync      — semantic-aware sync
    POST /api/semantic/refresh    — refresh semantic metadata
    POST /api/sync               — full sync pipeline
"""

from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request

from semabridge.api.services.semantic_service import semantic_refresh, semantic_sync, sync_models

router = APIRouter()
router.post('/api/semantic/sync', response_model=None)(semantic_sync)
router.post('/api/semantic/refresh', response_model=None)(semantic_refresh)


@router.post("/api/sync")
async def trigger_sync(request: Request, payload: Dict[str, Any]) -> Any:
    """Trigger a full sync with user-scoped credential isolation.

    When ``AUTH_ENABLED=true``, injects the authenticated ``user_id``
    from the JWT into the sync payload so that ``core_sync_impl`` can
    validate account ownership before executing.
    """
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            payload["user_id"] = user_id

    return await sync_models(payload)
