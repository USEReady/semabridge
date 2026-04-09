from fastapi import APIRouter
from services.sync_service import (
    sync_models,
    semantic_sync,
    semantic_refresh
)

router = APIRouter()

@router.post("/sync")
async def sync(payload: dict):
    return await sync_models(payload)

@router.post("/semantic/sync")
async def semantic_sync_api(payload: dict):
    return await semantic_sync(payload)

@router.post("/semantic/refresh")
async def semantic_refresh_api(payload: dict):
    return await semantic_refresh(payload)