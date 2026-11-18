from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db, get_current_user
from semabridge.repository.orm.models import LocalFolder, UserCredential
from semabridge.connectors.schema_manager import clear_schema_cache

logger = logging.getLogger("semabridge.api.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


# ─── Local Folder models ──────────────────────────────────────────────────────

class LocalFolderCreate(BaseModel):
    tag_name: str = Field(min_length=1, max_length=255)
    absolute_path: str = Field(min_length=1)
    is_active: bool = True


class LocalFolderResponse(BaseModel):
    id: str
    tag_name: str
    absolute_path: str
    is_active: bool

    class Config:
        from_attributes = True


def _normalize_path(raw_path: str) -> str:
    return str(Path(raw_path).expanduser()).replace("\\", "/").strip()


def _is_absolute_path(raw_path: str) -> bool:
    normalized = str(raw_path or "").strip().replace("\\", "/")
    return bool(re.match(r"^(?:[A-Za-z]:/|//|/)", normalized))


# Registering a folder widens the server-wide path allow-list
# (``semabridge.auth.file_access``), so these locations are never accepted:
# the whole tree below them is system territory ...
_FORBIDDEN_SUBTREES = (
    "/etc", "/private/etc", "/proc", "/sys", "/dev", "/boot", "/bin", "/sbin",
    "/usr", "/lib", "/lib64", "/System", "/Library", "/root",
    "C:/Windows", "C:/Program Files", "C:/Program Files (x86)", "C:/ProgramData",
)
# ... and these are too broad to register as a whole (subfolders are fine).
_FORBIDDEN_EXACT = (
    "/var", "/private", "/private/var", "/tmp", "/private/tmp", "/opt", "/home",
    "/Users", "/Volumes", "/mnt", "/media", "/srv", "/Applications",
)


def _path_key(path: Path) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def _reject_system_directory(resolved: Path) -> None:
    """Refuse filesystem roots, system directories and the home root itself."""
    key = _path_key(resolved)
    reason: Optional[str] = None
    if resolved == Path(resolved.anchor) or not key or key.endswith(":"):
        reason = "a filesystem root"
    else:
        for forbidden in _FORBIDDEN_SUBTREES:
            fkey = _path_key(Path(forbidden))
            if key == fkey or key.startswith(fkey + "/"):
                reason = "a system directory"
                break
        if reason is None and key in {_path_key(Path(p)) for p in _FORBIDDEN_EXACT}:
            reason = "too broad"
        if reason is None:
            try:
                home = Path.home().resolve()
                # The home directory itself, or any ancestor of it.
                home.relative_to(resolved)
                reason = "your home directory (or a parent of it)"
            except (ValueError, RuntimeError, OSError):
                pass
    if reason:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot register {resolved}: it is {reason}. Choose a specific project folder.",
        )


def _resolve_existing_directory(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise HTTPException(status_code=400, detail="absolute_path is required")

    if not _is_absolute_path(raw_path):
        raise HTTPException(status_code=400, detail="absolute_path must be an absolute path")

    # resolve() follows symlinks, so a link to a system folder is judged by
    # its real target.
    resolved = Path(raw_path).expanduser().resolve()
    if not resolved.exists():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {resolved}")
    _reject_system_directory(resolved)
    return resolved


@router.get("/local-folders", response_model=List[LocalFolderResponse])
def list_local_folders(
    include_inactive: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> List[LocalFolder]:
    stmt = select(LocalFolder).order_by(LocalFolder.tag_name.asc())
    if not include_inactive:
        stmt = stmt.where(LocalFolder.is_active.is_(True))
    return list(db.execute(stmt).scalars().all())


@router.post("/local-folders", response_model=LocalFolderResponse, status_code=status.HTTP_201_CREATED)
def save_local_folder(
    body: LocalFolderCreate,
    request: Request,
    db: Session = Depends(get_db),
) -> LocalFolder:
    """Register (or update) a named local folder. **Admin only.**

    Registered folders are global: ``LocalFolder`` has no owner/account
    column, and every active folder joins the server-wide path allow-list
    (``semabridge.auth.file_access``) for all users. Registration is therefore
    restricted to admins, and system/root directories are refused.
    """
    from semabridge.api.services.project_ownership_service import require_admin_request

    require_admin_request(request)
    tag_name = body.tag_name.strip()
    if not tag_name:
        raise HTTPException(status_code=400, detail="tag_name is required")

    resolved = _resolve_existing_directory(body.absolute_path)
    normalized_path = _normalize_path(str(resolved))

    existing = db.execute(
        select(LocalFolder).where(LocalFolder.tag_name == tag_name)
    ).scalar_one_or_none()

    path_conflict = db.execute(
        select(LocalFolder).where(LocalFolder.absolute_path == normalized_path)
    ).scalar_one_or_none()
    if path_conflict and (existing is None or path_conflict.id != existing.id):
        raise HTTPException(
            status_code=400,
            detail=f"Directory already registered under tag '{path_conflict.tag_name}'",
        )

    if existing:
        existing.absolute_path = normalized_path
        existing.is_active = bool(body.is_active)
        db.commit()
        db.refresh(existing)
        return existing

    local_folder = LocalFolder(
        id=str(uuid.uuid4()),
        tag_name=tag_name,
        absolute_path=normalized_path,
        is_active=bool(body.is_active),
    )
    db.add(local_folder)
    db.commit()
    db.refresh(local_folder)
    return local_folder


# ─── API Secrets endpoints ────────────────────────────────────────────────────

_SECRET_SERVICE = "api_secrets"
_UPPER_SNAKE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


class SecretCreate(BaseModel):
    """Payload for creating or updating a user-scoped API secret."""

    key: str = Field(min_length=1, max_length=100, description="Environment variable name (auto-uppercased).")
    value: str = Field(min_length=1, description="Secret value — never returned after save.")


class SecretResponse(BaseModel):
    """Safe representation of a stored secret: key + masked hint only."""

    key: str
    hint: str
    updated_at: Optional[datetime] = None


def _normalize_key(raw: str) -> str:
    """Normalize a secret key to UPPER_SNAKE_CASE.

    Examples:
        groq_api_key  → GROQ_API_KEY
        Openai-KEY    → OPENAI_KEY
        my secret     → MY_SECRET
    """
    return re.sub(r"[^A-Z0-9]+", "_", raw.strip().upper()).strip("_")


def _make_hint(value: str) -> str:
    """Return a masked hint showing only the last 4 characters.

    Examples:
        'sk-abc123jk9x'  → '••••jk9x'
        'ab'             → '••••'
    """
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"


@router.get("/secrets", response_model=List[SecretResponse])
def list_secrets(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> List[SecretResponse]:
    """List all stored API secret keys for the authenticated user.

    Values are NEVER included in the response — only the key name, a masked
    hint (last 4 chars), and the last-updated timestamp are returned.

    Args:
        db: Database session.
        current_user: The authenticated user (from JWT).

    Returns:
        List of SecretResponse items ordered by key name.
    """
    rows = db.execute(
        select(UserCredential)
        .where(
            UserCredential.user_id == current_user.id,
            UserCredential.service == _SECRET_SERVICE,
        )
        .order_by(UserCredential.key.asc())
    ).scalars().all()

    return [
        SecretResponse(
            key=row.key,
            hint=_make_hint(row.value),
            updated_at=row.updated_at or row.created_at,
        )
        for row in rows
    ]


@router.post("/secrets", response_model=SecretResponse, status_code=status.HTTP_201_CREATED)
def save_secret(
    body: SecretCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> SecretResponse:
    """Create or update a user-scoped API secret.

    The key is auto-normalized to UPPER_SNAKE_CASE before storage.
    After saving, the secret is immediately injected into ``os.environ``
    so the LLM comparator and other backend services can use it without
    a server restart.

    Args:
        body: SecretCreate payload with key and value.
        db: Database session.
        current_user: The authenticated user (from JWT).

    Returns:
        SecretResponse with the normalized key and masked hint.

    Raises:
        HTTPException 400: If the normalized key is empty after stripping.
    """
    normalized_key = _normalize_key(body.key)
    if not normalized_key:
        raise HTTPException(status_code=400, detail="key must contain at least one alphanumeric character.")

    existing = db.execute(
        select(UserCredential).where(
            UserCredential.user_id == current_user.id,
            UserCredential.service == _SECRET_SERVICE,
            UserCredential.key == normalized_key,
        )
    ).scalar_one_or_none()

    if existing:
        existing.value = body.value
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = UserCredential(
            user_id=current_user.id,
            service=_SECRET_SERVICE,
            key=normalized_key,
            value=body.value,
        )
        db.add(row)
        db.commit()
        db.refresh(row)

    # Secret injected into DB — the comparator reads user secrets from DB at request-time.
    # We do NOT mutate os.environ here because it is a process-level singleton:
    # doing so would allow one user's secret to overwrite another's in memory.
    logger.info("API secret '%s' saved for user %d.", normalized_key, current_user.id)

    return SecretResponse(
        key=normalized_key,
        hint=_make_hint(body.value),
        updated_at=row.updated_at or row.created_at,
    )


@router.post("/schema/refresh", status_code=status.HTTP_200_OK)
def refresh_schema_cache_endpoint(current_user=Depends(get_current_user)) -> dict:
    """Clear cached Snowflake schema metadata so next deploy reloads it."""
    clear_schema_cache()
    return {"status": "ok", "message": "Schema cache cleared"}


@router.delete("/secrets/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_secret(
    key: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> None:
    """Delete a stored API secret by key for the authenticated user.

    Also removes the key from ``os.environ`` so it is no longer usable
    in the current process.

    Args:
        key: The secret key name (case-insensitive — normalized to UPPER_SNAKE_CASE).
        db: Database session.
        current_user: The authenticated user (from JWT).

    Raises:
        HTTPException 404: If the secret does not exist for this user.
    """
    normalized_key = _normalize_key(key)

    result = db.execute(
        delete(UserCredential).where(
            UserCredential.user_id == current_user.id,
            UserCredential.service == _SECRET_SERVICE,
            UserCredential.key == normalized_key,
        )
    )
    db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"Secret '{normalized_key}' not found.")

    logger.info("API secret '%s' deleted for user %d.", normalized_key, current_user.id)
