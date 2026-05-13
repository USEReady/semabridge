from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/utils", tags=["utils"])


def _parse_allowed_root_values() -> List[Path]:
    """Return configured browse roots from the environment.

    ``SEMABRIDGE_BROWSE_ROOTS`` accepts a comma-separated list of absolute
    paths. When unset, browsing is limited to the user's home directory and
    the current working directory of the backend process.
    """
    configured = os.environ.get("SEMABRIDGE_BROWSE_ROOTS", "").strip()
    roots: List[Path] = []

    if configured:
        for raw_value in configured.split(","):
            value = raw_value.strip()
            if not value:
                continue
            try:
                root = Path(os.path.expandvars(os.path.expanduser(value))).resolve()
            except OSError:
                continue
            if root.exists() and root.is_dir():
                roots.append(root)
        return roots

    home = Path(os.path.expanduser("~")).resolve()
    cwd = Path.cwd().resolve()

    for candidate in [home, cwd]:
        if candidate.exists() and candidate.is_dir() and candidate not in roots:
            roots.append(candidate)

    return roots


def _normalize_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/")


def _allowed_roots() -> List[Path]:
    roots = _parse_allowed_root_values()
    if roots:
        return roots

    # Final fallback if the environment/home/cwd cannot be resolved.
    try:
        return [Path(os.path.expanduser("~")).resolve()]
    except Exception:
        return [Path.cwd().resolve()]


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
        raise HTTPException(status_code=403, detail="Requested path is outside allowed browse roots")

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
