from fastapi import APIRouter
from services.config_service import (
    get_config,
    generate_config,
    validate_config,
    validate_live,
    get_global_config,
    save_global_config
)

router = APIRouter()

@router.get("")
async def config():
    return await get_config()

@router.post("/generate")
async def generate(payload: dict):
    return await generate_config(payload)

@router.post("/validate")
async def validate(payload: dict):
    return await validate_config(payload)

@router.post("/validate-live")
async def validate_live_config(payload: dict):
    return await validate_live(payload)

@router.get("/global-config")
async def global_config():
    return await get_global_config()

@router.put("/global-config")
async def save_global(payload: dict):
    return await save_global_config(payload)