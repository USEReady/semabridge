from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db
from semabridge.repository.orm.models import LocalFolder

logger = logging.getLogger("semabridge.api.settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])


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


def _resolve_existing_directory(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise HTTPException(status_code=400, detail="absolute_path is required")

    if not _is_absolute_path(raw_path):
        raise HTTPException(status_code=400, detail="absolute_path must be an absolute path")

    resolved = Path(raw_path).expanduser().resolve()
    if not resolved.exists():
        raise HTTPException(status_code=400, detail=f"Directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {resolved}")
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
def save_local_folder(body: LocalFolderCreate, db: Session = Depends(get_db)) -> LocalFolder:
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