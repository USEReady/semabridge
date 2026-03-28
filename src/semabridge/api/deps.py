
from __future__ import annotations
from fastapi import Request, HTTPException, Header, Depends

# Dependency to extract Fabric Workspace Context from header
def get_fabric_context(request: Request):
    context_header = request.headers.get("X-Fabric-Context")
    if not context_header:
        raise HTTPException(status_code=401, detail="No Fabric context provided")
    return context_header

"""
Shared FastAPI Dependency Injection utilities.

All FastAPI routers should import database and repository dependencies from
this module rather than instantiating sessions or repositories at module level.
This ensures that every request receives a fresh, correctly scoped session and
that database rotations (URL changes at runtime) are picked up transparently.

Available dependencies
----------------------

``get_db``
    Yields an open ``Session`` auto-closed after the request.  Use with
    ``Depends(get_db)`` in any route that needs direct ORM access::

        from semabridge.api.deps import get_db

        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            return db.execute(select(Item)).scalars().all()

``get_model_repository``
    Returns a ``ModelRepository`` instance wired to the current engine.
    Use when you need the higher-level repository API::

        from semabridge.api.deps import get_model_repository

        @router.get("/versions")
        def list_versions(repo: ModelRepository = Depends(get_model_repository)):
            return repo.list_versions()
"""


from typing import TYPE_CHECKING, Generator

from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from semabridge.repository.model_repository import ModelRepository

logger = get_logger(__name__)


def get_db() -> Generator["Session", None, None]:  # type: ignore[misc]
    """FastAPI dependency — yield a scoped SQLAlchemy ``Session``.

    The session is obtained from the module-level :data:`DatabaseManager`
    singleton which re-checks the database URL on every call.  If the URL
    has changed since the last request (i.e. credentials were rotated), the
    old connection pool is disposed and a new engine is created before the
    session is opened.

    The session is always closed in the ``finally`` block so connections are
    returned to the pool even when an exception propagates.

    Usage::

        from fastapi import Depends
        from sqlalchemy.orm import Session
        from semabridge.api.deps import get_db

        @router.get("/ping")
        def ping(db: Session = Depends(get_db)):
            db.execute(text("SELECT 1"))
            return {"status": "ok"}

    Yields:
        An open, request-scoped ``Session``.
    """
    from semabridge.repository.orm.session_factory import db_manager

    with db_manager.get_session() as session:
        yield session


def get_model_repository() -> "ModelRepository":
    """FastAPI dependency -- return a ``ModelRepository`` for the current engine.

    ``ModelRepository.__init__`` reads the engine from the module-level
    ``DatabaseManager`` singleton, so it always uses the latest (possibly
    rotated) engine.

    Usage::

        from fastapi import Depends
        from semabridge.api.deps import get_model_repository
        from semabridge.repository.model_repository import ModelRepository

        @router.get("/versions")
        def list_versions(repo: ModelRepository = Depends(get_model_repository)):
            return repo.list_versions()

    Returns:
        A ``ModelRepository`` bound to the active engine.
    """
    from semabridge.repository.model_repository import ModelRepository

    return ModelRepository()
