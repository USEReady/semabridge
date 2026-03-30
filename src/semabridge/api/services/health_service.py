from sqlalchemy import text
from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager
import logging

logger = logging.getLogger("semabridge.api")

async def health_check():
    db_status = "connected"
    db_dialect = "unknown"

    try:
        _engine = _orm_db_manager.get_engine()
        db_dialect = _engine.dialect.name
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check DB ping failed: %s", exc)
        db_status = "unreachable"

    overall = "ok" if db_status == "connected" else "degraded"

    return {
        "status": overall,
        "service": "semabridge-api",
        "database": db_status,
        "db_dialect": db_dialect,
    }