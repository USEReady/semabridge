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


async def delete_folder_compat(folder_id: str):
    _compat_folders.pop(folder_id, None)
    for project in _compat_projects.values():
        if project.get("folder_id") == folder_id:
            project["folder_id"] = None
            project["updated_at"] = _compat_now_iso()
    return Response(status_code=204)


async def move_project_to_folder_compat(project_id: str, payload: dict):
    project = _compat_projects.get(project_id)
    if not project:
        raise NotFoundError("Project not found")
    folder_id = (payload or {}).get("folder_id")
    if folder_id is not None and folder_id not in _compat_folders:
        raise NotFoundError("Folder not found")
    project["folder_id"] = folder_id
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    return project
