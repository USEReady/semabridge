from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/utils", tags=["utils"])


def _normalize_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _allowed_roots() -> List[Path]:
    if os.name != "nt":
        return [Path("/")]

    try:
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        bitmask = 0

    roots: List[Path] = []
    for index in range(26):
        if bitmask & (1 << index):
            drive = f"{chr(65 + index)}:/"
            roots.append(Path(drive).resolve())

    if not roots:
        home = Path(os.path.expanduser("~")).resolve()
        anchor = Path(home.anchor) if home.anchor else Path("C:/")
        roots = [anchor.resolve()]

    return roots


def _is_within_allowed_roots(path: Path, roots: List[Path]) -> bool:
    normalized = _normalize_path(path).rstrip("/").casefold()
    for root in roots:
        root_norm = _normalize_path(root).rstrip("/").casefold()
        if normalized == root_norm or normalized.startswith(f"{root_norm}/"):
            return True
    return False


def _resolve_start_path(base_path: Optional[str]) -> Path:
    if base_path and base_path.strip():
        candidate = Path(os.path.expandvars(os.path.expanduser(base_path.strip()))).resolve()
    else:
        home = Path(os.path.expanduser("~")).resolve()
        candidate = home if home.exists() else Path("/").resolve()

    if not candidate.exists() or not candidate.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {candidate}")

    roots = _allowed_roots()
    if not _is_within_allowed_roots(candidate, roots):
        raise HTTPException(status_code=403, detail="Requested path is outside allowed drives")

    return candidate


@router.get("/browse-directory")
def browse_directory(base_path: Optional[str] = Query(default=None)) -> List[dict]:
    start_path = _resolve_start_path(base_path)
    roots = _allowed_roots()

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

                if not _is_within_allowed_roots(entry_path, roots):
                    continue

                directories.append({
                    "name": entry.name,
                    "path": _normalize_path(entry_path),
                })
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=f"Permission denied: {start_path}") from exc

    directories.sort(key=lambda item: item["name"].lower())
    return directories
