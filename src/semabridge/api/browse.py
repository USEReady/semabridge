"""Directory browser for the folder picker.

Browsing is confined to the same allow-list used for every user-supplied
filesystem path (:mod:`semabridge.auth.file_access`): the upload root, the
repo ``Examples/`` folder, the local models path, registered local folders and
``SEMABRIDGE_ALLOWED_SOURCE_ROOTS``. There is deliberately no second
allow-list here -- the whole filesystem (``/`` or every Windows drive) is never
exposed.

Without ``base_path`` the endpoint lists the allowed roots themselves (the
equivalent of the old Windows "drive list").
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from semabridge.auth import file_access

router = APIRouter(prefix="/api/utils", tags=["utils"])


def _normalize_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _allowed_roots() -> List[Path]:
    """Existing allowed directories (single source: ``file_access``)."""
    return [root for root in file_access.allowed_source_roots() if root.is_dir()]


def _resolve_start_path(base_path: str, roots: List[Path]) -> Path:
    raw = os.path.expandvars(os.path.expanduser(base_path.strip()))
    try:
        return file_access.resolve_allowed_path(raw, expect="dir", roots=roots)
    except file_access.PermissionError as exc:
        raise HTTPException(status_code=403, detail="Requested path is outside the allowed folders") from exc
    except file_access.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/browse-directory")
def browse_directory(base_path: Optional[str] = Query(default=None)) -> List[dict]:
    roots = _allowed_roots()

    if not base_path or not base_path.strip():
        listing = [{"name": root.name or _normalize_path(root), "path": _normalize_path(root)} for root in roots]
        listing.sort(key=lambda item: item["name"].lower())
        return listing

    start_path = _resolve_start_path(base_path, roots)

    directories = []
    try:
        with os.scandir(start_path) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                try:
                    entry_path = Path(entry.path).resolve()
                except OSError:
                    continue
                if not file_access.is_path_allowed(str(entry_path), roots=roots):
                    continue
                directories.append({"name": entry.name, "path": _normalize_path(entry_path)})
    except PermissionError as exc:  # builtin OSError subclass
        raise HTTPException(status_code=403, detail=f"Permission denied: {start_path}") from exc

    directories.sort(key=lambda item: item["name"].lower())
    return directories


# Alias router so GET /api/browse-directory also resolves cleanly (preventing 404)
api_router = APIRouter(prefix="/api", tags=["utils"])
api_router.add_api_route("/browse-directory", browse_directory, methods=["GET"], response_model=List[dict])
