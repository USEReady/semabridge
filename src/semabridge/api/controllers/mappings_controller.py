import os
from typing import Any, Dict

from fastapi import APIRouter, Request

from semabridge.api.services.mappings_service import (
    auto_map_compat,
    delete_mappings_compat,
    list_mappings_compat,
    update_mapping_compat,
)

router = APIRouter()
router.get('/api/mappings')(list_mappings_compat)


@router.post('/api/mappings/auto')
async def auto_map_with_user_context(request: Request, payload: Dict[str, Any]):
    body = dict(payload or {})
    if os.environ.get("AUTH_ENABLED", "").lower() == "true":
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            body["user_id"] = user_id
    return await auto_map_compat(body)


router.put('/api/mappings/{mapping_id}')(update_mapping_compat)
router.delete('/api/mappings')(delete_mappings_compat)
