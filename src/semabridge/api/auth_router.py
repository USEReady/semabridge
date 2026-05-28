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

from datetime import datetime, timezone
from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from typing import Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from semabridge.auth.deps import get_current_user
from semabridge.api.deps import get_db
from semabridge.auth.passwords import hash_password, verify_password, verify_password_async
from semabridge.auth.schemas import (
    CredentialListItem,
    CredentialSaveRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    TokenUserInfo,
    UserResponse,
)
from semabridge.auth.tokens import (
    create_access_token,
    create_token_pair,
    generate_refresh_token,
    get_refresh_token_expiry,
    hash_refresh_token,
)
from semabridge.repository.orm.models import RefreshToken, User, UserCredential
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "semabridge_refresh_token"

_RATE_LIMIT_LOCK = Lock()
_RATE_LIMIT_BUCKETS: dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def _enforce_rate_limit(bucket_name: str, key: str, limit: int, window_seconds: int) -> None:
    now = monotonic()
    bucket_key = (bucket_name, key)

    with _RATE_LIMIT_LOCK:
        # Periodic cleanup of all buckets when size grows to prevent memory leak
        if len(_RATE_LIMIT_BUCKETS) > 500:
            for k, deq in list(_RATE_LIMIT_BUCKETS.items()):
                # Clean up elements older than 900s (max window used is 900s)
                cutoff_all = now - 900
                while deq and deq[0] < cutoff_all:
                    deq.popleft()
                if not deq:
                    _RATE_LIMIT_BUCKETS.pop(k, None)

        attempts = _RATE_LIMIT_BUCKETS[bucket_key]
        cutoff = now - window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()

        if len(attempts) >= limit:
            retry_after = max(1, int(window_seconds - (now - attempts[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, please try again later",
                headers={"Retry-After": str(retry_after)},
            )

        attempts.append(now)


def _register_attempt(bucket_name: str, key: str) -> None:
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_BUCKETS[(bucket_name, key)].append(monotonic())


# ── Auto-login (development) ────────────────────────────────────────────

@router.post("/auto-login", response_model=TokenResponse)
def auto_login(
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Auto-create a dev user and issue a JWT — no credentials needed.

    Used during development to bypass the login page. In production,
    disable by removing this route or checking an env flag.

    Creates a default user ``dev`` on first call; reuses it on subsequent calls.
    """
    import os
    import uuid

    # Find or create default dev user
    dev_user = db.execute(
        select(User).where(User.username == "dev")
    ).scalar_one_or_none()

    if dev_user is None:
        from semabridge.auth.passwords import hash_password as _hash
        dev_user = User(
            username="dev",
            email=f"dev-{uuid.uuid4().hex[:8]}@semabridge.local",
            password_hash=_hash(uuid.uuid4().hex),  # random pw, never used
            role="admin",
        )
        db.add(dev_user)
        db.commit()
        db.refresh(dev_user)
        logger.info("Auto-created dev user (id=%s)", dev_user.id)

    # Issue tokens
    access_token, raw_refresh = create_token_pair(dev_user.id, dev_user.username, dev_user.role)

    rt = RefreshToken(
        user_id=dev_user.id,
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=get_refresh_token_expiry(),
    )
    db.add(rt)
    db.commit()

    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw_refresh,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/auth",
    )

    logger.info("Auto-login issued for dev user (id=%s)", dev_user.id)
    return TokenResponse(
        access_token=access_token,
        user=TokenUserInfo.model_validate(dev_user),
    )


# ── Public endpoints ─────────────────────────────────────────────────────


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> UserResponse:
    """Create a new user account.

    The first user registered is automatically promoted to ``admin``.
    """
    ip = _client_ip(request)
    username_key = body.username.strip().lower()
    email_key = body.email.strip().lower()

    _enforce_rate_limit("auth-register-ip", ip, limit=5, window_seconds=900)
    _enforce_rate_limit("auth-register-user", username_key, limit=3, window_seconds=900)
    _enforce_rate_limit("auth-register-email", email_key, limit=3, window_seconds=900)

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

    # Successful register: reset limits
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_BUCKETS.pop(("auth-register-ip", ip), None)
        _RATE_LIMIT_BUCKETS.pop(("auth-register-user", username_key), None)
        _RATE_LIMIT_BUCKETS.pop(("auth-register-email", email_key), None)

    logger.info("User registered: %s (role=%s)", user.username, user.role)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Authenticate with username + password and receive a JWT.

    Returns an access token in the response body (with inline user info)
    and sets a refresh token as an HttpOnly cookie for session renewal.

    This handler is synchronous (``def``) because FastAPI runs sync
    handlers in a threadpool, which naturally offloads the CPU-bound
    bcrypt verification without cross-thread session issues.
    """
    ip = _client_ip(request)
    username_key = body.username.strip().lower()

    _enforce_rate_limit("auth-login-ip", ip, limit=10, window_seconds=300)
    _enforce_rate_limit("auth-login-user", username_key, limit=5, window_seconds=300)

    user = db.execute(
        select(User).where(User.username == body.username)
    ).scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        _register_attempt("auth-login-ip-fail", ip)
        _register_attempt("auth-login-user-fail", username_key)
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

    # Successful login: reset both generic limits and failed attempts to reset behavior correctly
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_BUCKETS.pop(("auth-login-ip", ip), None)
        _RATE_LIMIT_BUCKETS.pop(("auth-login-user", username_key), None)
        _RATE_LIMIT_BUCKETS.pop(("auth-login-ip-fail", ip), None)
        _RATE_LIMIT_BUCKETS.pop(("auth-login-user-fail", username_key), None)

    # Create access + refresh token pair
    access_token, raw_refresh = create_token_pair(user.id, user.username, user.role)

    # Persist refresh token hash in DB
    rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=get_refresh_token_expiry(),
    )
    db.add(rt)
    db.commit()

    # Set refresh token as HttpOnly cookie
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw_refresh,
        httponly=True,
        secure=False,  # Set True in production with HTTPS
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/auth",
    )

    logger.info("User logged in: %s", user.username)
    return TokenResponse(
        access_token=access_token,
        user=TokenUserInfo.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    db: Session = Depends(get_db),
    semabridge_refresh_token: Optional[str] = Cookie(None),
) -> TokenResponse:
    """Exchange a refresh token for a new access + refresh pair.

    The old refresh token is consumed (one-time use) and a new
    pair is issued. This prevents replay attacks.
    """
    if not semabridge_refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No refresh token provided",
        )

    token_hash = hash_refresh_token(semabridge_refresh_token)
    stored = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.is_revoked == False,  # noqa: E712
        )
    ).scalar_one_or_none()

    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or already-used refresh token",
        )

    if stored.expires_at < datetime.now(timezone.utc):
        stored.is_revoked = True
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired",
        )

    # Revoke old token (one-time use)
    stored.is_revoked = True

    # Load user
    user = db.execute(
        select(User).where(User.id == stored.user_id)
    ).scalar_one_or_none()

    if not user or not user.is_active:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )

    # Issue new pair
    access_token, raw_refresh = create_token_pair(user.id, user.username, user.role)

    new_rt = RefreshToken(
        user_id=user.id,
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=get_refresh_token_expiry(),
    )
    db.add(new_rt)
    db.commit()

    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw_refresh,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/auth",
    )

    logger.info("Token refreshed for user: %s", user.username)
    return TokenResponse(access_token=access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    semabridge_refresh_token: Optional[str] = Cookie(None),
) -> None:
    """Revoke the current refresh token and clear the cookie."""
    if semabridge_refresh_token:
        token_hash = hash_refresh_token(semabridge_refresh_token)
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(is_revoked=True)
        )
        db.commit()

    response.delete_cookie(key=_REFRESH_COOKIE, path="/auth")
    logger.info("User logged out")


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
