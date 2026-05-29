from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from semabridge.api.services.health_service import health_check

router = APIRouter()
router.get('/api/health')(health_check)


@router.get('/api/health/live')
async def liveness():
    """Kubernetes/Docker liveness probe — confirms the process is alive."""
    return {"status": "ok"}


@router.get('/api/health/ready')
async def readiness():
    """Kubernetes/Docker readiness probe — confirms the app can serve traffic."""
    from semabridge.repository.orm.session_factory import db_manager

    checks: dict = {}
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
