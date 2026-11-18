"""Folder management service — CRUD operations for project folders.

Provides list, create, rename, delete, and move-to-folder operations
against the in-memory compatibility store.
"""
import time as _time
from typing import Any, Dict

from fastapi import Response

from semabridge.api.services.project_shared import (
    _compat_ensure_loaded,
    _compat_folders,
    _compat_now_iso,
    _compat_projects,
)
from semabridge.domain.exceptions import NotFoundError


async def list_folders_compat():
    return list(_compat_folders.values())


async def create_folder_compat(payload: dict):
    folder_id = str(
        (payload or {}).get("id")
        or (payload or {}).get("folder_id")
        or f"folder-{int(_time.time() * 1000)}"
    )
    folder = {
        "id": folder_id,
        "folder_id": folder_id,
        "name": (payload or {}).get("name") or f"Folder {folder_id[-4:]}",
        "color": (payload or {}).get("color") or "#6366f1",
    }
    _compat_folders[folder_id] = folder
    return folder


async def rename_folder_compat(folder_id: str, payload: dict):
    folder = _compat_folders.get(folder_id)
    if not folder:
        raise NotFoundError("Folder not found")
    if "name" in (payload or {}):
        folder["name"] = (payload or {}).get("name")
    if "color" in (payload or {}):
        folder["color"] = (payload or {}).get("color")
    _compat_folders[folder_id] = folder
    return folder


def _clear_folder_in_db(folder_id: str) -> None:
    """Projects of a deleted folder: "" (= no folder) in their database rows."""
    from sqlalchemy import update

    from semabridge.api.services.project_shared import _ensure_project_tables, _orm_db
    from semabridge.repository.orm.models import Project

    _ensure_project_tables()
    with _orm_db().get_session() as session:
        session.execute(
            update(Project).where(Project.folder_id == str(folder_id)).values(folder_id="")
            .execution_options(synchronize_session=False)
        )
        session.commit()


async def delete_folder_compat(folder_id: str):
    import asyncio

    from semabridge.api.services.project_shared import ProjectPersistenceError

    try:
        await asyncio.to_thread(_clear_folder_in_db, folder_id)
    except Exception as exc:  # noqa: BLE001
        raise ProjectPersistenceError(f"Folder {folder_id} could not be cleared in the database: {exc}") from exc
    _compat_folders.pop(folder_id, None)
    for project in _compat_projects.values():
        if project.get("folder_id") == folder_id:
            project["folder_id"] = None
            project["updated_at"] = _compat_now_iso()
    return Response(status_code=204)


async def move_project_to_folder_compat(project_id: str, payload: dict):
    import asyncio

    from semabridge.api.services.project_shared import _compat_get_project, _compat_write_project_meta

    project = await asyncio.to_thread(_compat_get_project, project_id, _compat_projects)
    if not project:
        raise NotFoundError("Project not found")
    folder_id = (payload or {}).get("folder_id")
    if folder_id is not None and folder_id not in _compat_folders:
        raise NotFoundError("Folder not found")
    # Database first ("" = no folder), then the cache.
    await asyncio.to_thread(
        _compat_write_project_meta, project_id, strict=True, folder_id=str(folder_id or "")
    )
    project["folder_id"] = folder_id
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    return project
