from fastapi import APIRouter
from services.discovery_service import (
    discover_fabric_models,
    discover_snowflake,
    discover_repository,
    discover_semantic
)

router = APIRouter()

@router.get("/fabric")
async def fabric():
    return await discover_fabric_models()

@router.get("/snowflake")
async def snowflake():
    return await discover_snowflake()

@router.get("/repository")
async def repository():
    return await discover_repository()

@router.get("/semantic")
async def semantic():
    return await discover_semantic()