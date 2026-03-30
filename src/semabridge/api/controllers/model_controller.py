from fastapi import APIRouter
from services.model_service import get_model, save_model

router = APIRouter()

@router.get("/{model_id}")
async def read_model(model_id: str):
    return await get_model(model_id)

@router.put("/{model_id}")
async def update_model(model_id: str, payload: dict):
    return await save_model(model_id, payload)