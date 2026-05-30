from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


class MoveFolderRequest(BaseModel):
    folder_id: Optional[str] = None

    class Config:
        extra = "allow"

from semabridge.api.services.folders_service import (
    create_folder_compat,
    delete_folder_compat,
    list_folders_compat,
    move_project_to_folder_compat,
    rename_folder_compat,
)
from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
)

router = APIRouter()
router.get("/api/folders")(list_folders_compat)
router.post("/api/folders")(create_folder_compat)
router.patch("/api/folders/{folder_id}")(rename_folder_compat)
router.delete("/api/folders/{folder_id}")(delete_folder_compat)


@router.patch("/api/projects/{project_id}/folder")
async def move_project_to_folder(project_id: str, payload: MoveFolderRequest, request: Request):
    user_id = require_request_user_id(request)
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id, log_prefix="FolderProjectAuth"):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await move_project_to_folder_compat(project_id, payload.model_dump(exclude_none=False))
