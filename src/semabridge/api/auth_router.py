"""
Authentication & user-credential REST endpoints.

Routes
------
- ``POST /auth/register``          — create a new user account
- ``POST /auth/login``             — authenticate and receive a JWT
- ``GET  /auth/me``                — return the authenticated user profile
- ``POST /auth/credentials/{svc}`` — save a credential for the current user
- ``GET  /auth/credentials``       — list credential keys for the current user
- ``DELETE /auth/credentials/{svc}/{key}`` — remove a specific credential

All mutating endpoints require a valid JWT (``Authorization: Bearer …``),
except ``register`` and ``login`` which are public.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from semabridge.auth.deps import get_current_user
from semabridge.api.deps import get_db
from semabridge.auth.passwords import hash_password, verify_password
from semabridge.auth.schemas import (
    CredentialListItem,
    CredentialSaveRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from semabridge.auth.tokens import create_access_token
from semabridge.repository.orm.models import User, UserCredential
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Public endpoints ─────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> UserResponse:
    """Create a new user account.

    The first user registered is automatically promoted to ``admin``.
    """
    # Check for duplicate username / email
    existing = db.execute(
        select(User).where(
            (User.username == body.username) | (User.email == body.email)
        )
    ).scalar_one_or_none()

    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already registered",
        )

    # First user → admin
    user_count = db.execute(select(User.id)).scalars().all()
    role = "admin" if len(user_count) == 0 else "viewer"

    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    logger.info("User registered: %s (role=%s)", user.username, user.role)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate with username + password and receive a JWT."""
    user = db.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated",
        )

    token = create_access_token(data={"sub": user.id, "role": user.role})
    logger.info("User logged in: %s", user.username)
    return TokenResponse(access_token=token)


# ── Protected endpoints ──────────────────────────────────────────────────

@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse:
    """Return the authenticated user's profile."""
    return UserResponse.model_validate(user)


# ── Per-user credential management ───────────────────────────────────────

@router.post("/credentials/{service}", status_code=status.HTTP_201_CREATED)
def save_credential(
    service: str,
    body: CredentialSaveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, str]:
    """Save or update a credential key for the current user."""
    service = service.lower()

    existing = db.execute(
        select(UserCredential).where(
            UserCredential.user_id == user.id,
            UserCredential.service == service,
            UserCredential.key == body.key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.value = body.value
    else:
        cred = UserCredential(
            user_id=user.id,
            service=service,
            key=body.key,
            value=body.value,
        )
        db.add(cred)

    db.commit()
    logger.info("Credential saved: user=%s service=%s key=%s", user.username, service, body.key)
    return {"status": "saved", "service": service, "key": body.key}


@router.get("/credentials", response_model=List[CredentialListItem])
def list_credentials(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[CredentialListItem]:
    """List all credential keys for the current user (values are masked)."""
    rows = db.execute(
        select(UserCredential).where(UserCredential.user_id == user.id)
    ).scalars().all()

    return [
        CredentialListItem(service=r.service, key=r.key, has_value=bool(r.value))
        for r in rows
    ]


@router.delete("/credentials/{service}/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    service: str,
    key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a specific credential for the current user."""
    service = service.lower()
    db.execute(
        delete(UserCredential).where(
            UserCredential.user_id == user.id,
            UserCredential.service == service,
            UserCredential.key == key,
        )
    )
    db.commit()
    logger.info("Credential deleted: user=%s service=%s key=%s", user.username, service, key)


@router.get("/credentials/{service}", response_model=List[CredentialListItem])
def list_service_credentials(
    service: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[CredentialListItem]:
    """List credential keys for a specific service for the current user."""
    service = service.lower()
    rows = db.execute(
        select(UserCredential).where(
            UserCredential.user_id == user.id,
            UserCredential.service == service,
        )
    ).scalars().all()

    return [
        CredentialListItem(service=r.service, key=r.key, has_value=bool(r.value))
        for r in rows
    ]
