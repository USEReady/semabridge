"""
Module: deps
Purpose: Provide FastAPI dependency providers for request-scoped services.
Responsibilities:
- Expose dependency functions for database sessions and repositories.
- Centralize dependency wiring for API routes.
"""

from __future__ import annotations
from typing import Generator
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


from typing import TYPE_CHECKING

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


def get_current_user(
    request: Request,
    db: "Session" = Depends(get_db),
) -> "User":
    """FastAPI dependency: resolve the authenticated User from the JWT.

    The ``AuthMiddleware`` (``middleware.py:97``) has already validated the
    token and set ``request.state.user_id``.  This dependency fetches the
    full User row and validates it is active.

    Usage::

        from semabridge.api.deps import get_current_user
        from semabridge.repository.orm.models import User

        @router.get("/me")
        def whoami(user: User = Depends(get_current_user)):
            return {"id": user.id, "username": user.username}

    Args:
        request: The incoming FastAPI request (injected automatically).
        db: A scoped SQLAlchemy session (injected via ``get_db``).

    Returns:
        The authenticated :class:`User` row.

    Raises:
        HTTPException 401: If ``user_id`` is missing from request state
            (auth not enabled, or token missing).
        HTTPException 401: If user row not found or deactivated.
    """
    from semabridge.repository.orm.models import User as UserModel

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )
    user = db.get(UserModel, int(user_id))
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail="User not found or inactive",
        )
    return user


def get_current_user_optional(
    request: Request,
    db: "Session | None" = None,
) -> "User | None":
    """Resolve the authenticated user without raising on failure.

    Unlike :func:`get_current_user`, this function returns ``None`` when
    authentication is disabled or no valid token is present.  Use this
    wherever the caller must gracefully handle unauthenticated contexts
    (e.g. dev mode, CLI, or optional-auth endpoints).

    Args:
        request: The incoming FastAPI request.
        db: Optional SQLAlchemy session.  If ``None``, one is opened from
            the session factory for the duration of this call.

    Returns:
        The authenticated :class:`User` row, or ``None`` if not authenticated.
    """
    from semabridge.repository.orm.models import User as UserModel

    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return None

    def _fetch(session: "Session") -> "User | None":
        user = session.get(UserModel, int(user_id))
        if not user or not user.is_active:
            return None
        return user

    if db is not None:
        return _fetch(db)

    try:
        from semabridge.repository.orm.session_factory import db_manager
        with db_manager.get_session() as session:
            return _fetch(session)
    except Exception:
        return None


def get_scoped_db(request: Request) -> Generator["Session", None, None]:

    """FastAPI dependency — yield a tenant-scoped SQLAlchemy session.

    Like :func:`get_db`, but also sets the PostgreSQL session variable
    ``app.current_user_id`` so that Row Level Security (RLS) policies
    are enforced at the database level.

    This is **Layer 2** defense in depth — even if a developer forgets
    ``.where(Account.owner_id == user.id)`` in a query, the database
    refuses to return another user's rows.

    The ``SET LOCAL`` is scoped to the current transaction and is
    automatically rolled back when the session closes — no manual
    cleanup required.

    Usage::

        from semabridge.api.deps import get_scoped_db

        @router.get("/accounts")
        def list_accounts(db: Session = Depends(get_scoped_db)):
            return db.execute(select(Account)).scalars().all()
            # RLS ensures only current user's accounts are returned.

    Args:
        request: The incoming FastAPI request.

    Yields:
        A tenant-scoped ``Session``.
    """
    from sqlalchemy import text
    from semabridge.repository.orm.session_factory import db_manager

    user_id = getattr(request.state, "user_id", None)
    with db_manager.get_session() as session:
        if user_id:
            session.execute(
                text("SET LOCAL app.current_user_id = :uid"),
                {"uid": int(user_id)},
            )
        yield session
