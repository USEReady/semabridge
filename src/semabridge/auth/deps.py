"""
FastAPI authentication dependencies.

Provides ``get_current_user`` — a reusable ``Depends(...)`` that extracts
and validates the JWT bearer token from the ``Authorization`` header,
then returns the corresponding ORM ``User`` row.

Usage in a route::

    from semabridge.auth.deps import get_current_user
    from semabridge.repository.orm.models import User

    @router.get("/protected")
    def protected(user: User = Depends(get_current_user)):
        return {"hello": user.username}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from semabridge.auth.tokens import decode_access_token
from semabridge.repository.orm.models import User
from semabridge.api.deps import get_db
from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Backward-compat alias: code that imports ``_get_db`` from this module
# continues to work after the rename to ``get_db``.
_get_db = get_db

# OAuth2 scheme — tells Swagger UI to send ``Authorization: Bearer <token>``
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Decode JWT and return the authenticated :class:`User`.

    Raises:
        HTTPException 401: If the token is missing, expired, or the
            user no longer exists / is deactivated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_access_token(token)
        raw_sub = payload.get("sub")
        if raw_sub is None:
            raise credentials_exception
        user_id = int(raw_sub)
    except (JWTError, ValueError, TypeError):
        raise credentials_exception

    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()

    if user is None or not user.is_active:
        raise credentials_exception

    return user


async def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    """Ensure the current user has the ``admin`` role.

    Raises:
        HTTPException 403: If the user is not an admin.
    """
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
