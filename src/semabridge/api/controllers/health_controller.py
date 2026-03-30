from fastapi import APIRouter
from services.health_service import health_check

router = APIRouter()

@router.get("/health")
async def health():
    return await health_check()