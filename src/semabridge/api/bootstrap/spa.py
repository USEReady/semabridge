"""Serve the built React app from the API image (hosted profiles).

The SPA is mounted LAST so every API route keeps precedence. Static assets
are served from ``<dist>/assets``; any other GET that is not an API, auth,
websocket or docs path returns ``index.html`` so client-side routes work on
reload. Locally the Vite dev server serves the UI and this is a no-op unless
``SEMABRIDGE_FRONTEND_DIST`` points at a build.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# Paths the SPA must never answer (they belong to the API or are reserved).
_RESERVED_PREFIXES = ("api/", "auth/", "sync/", "ws", "docs", "redoc", "openapi.json", "health/")


def frontend_dist() -> Optional[Path]:
    raw = os.environ.get("SEMABRIDGE_FRONTEND_DIST")
    if raw:
        path = Path(raw)
    else:
        path = Path(__file__).resolve().parents[4] / "frontend" / "dist"
    return path if (path / "index.html").is_file() else None


def is_spa_path(path: str) -> bool:
    """True for paths the SPA serves (no sign-in needed to load the shell)."""
    p = path.lstrip("/")
    return not p.startswith(_RESERVED_PREFIXES)


def mount_spa(app: FastAPI) -> bool:
    dist = frontend_dist()
    if dist is None:
        return False
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="spa-assets")
    index = dist / "index.html"
    root = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if not is_spa_path(full_path):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and root in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    logger.info("Serving the web app from %s", dist)
    return True
