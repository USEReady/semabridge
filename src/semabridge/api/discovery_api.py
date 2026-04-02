from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db
from semabridge.repository.orm.models import LocalFolder

logger = logging.getLogger("semabridge.api.discovery")

router = APIRouter(prefix="/api/folders", tags=["folder-discovery"])


@router.get("/{tag}/files")
def list_pbix_files_by_tag(tag: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    folder = db.execute(
        select(LocalFolder).where(
            LocalFolder.tag_name == tag,
            LocalFolder.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if not folder:
        raise HTTPException(status_code=404, detail=f"Local folder tag '{tag}' not found")

    folder_path = Path(folder.absolute_path)
    if not folder_path.exists():
        raise HTTPException(status_code=404, detail=f"Directory not found: {folder.absolute_path}")
    if not folder_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {folder.absolute_path}")

    try:
        entries = sorted(os.listdir(folder_path), key=lambda value: value.lower())
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Permission denied reading {folder.absolute_path}") from exc

    files: List[Dict[str, Any]] = []
    for name in entries:
        if not name.lower().endswith(".pbix"):
            continue

        file_path = folder_path / name
        if not file_path.is_file():
            continue

        stat = file_path.stat()
        files.append({
            "name": name,
            "path": str(file_path.resolve()).replace("\\", "/"),
            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })

    return {
        "tag_name": folder.tag_name,
        "absolute_path": folder.absolute_path,
        "files": files,
    }