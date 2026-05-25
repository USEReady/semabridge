from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from semabridge.api.services.pbix_service import (
    browse_pbix_files,
    import_pbix,
    upload_pbix_temp,
    upload_project_pbix,
)
from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
)

router = APIRouter()
router.post("/api/upload")(upload_pbix_temp)
router.post("/api/pbix/import")(import_pbix)
router.get("/api/pbix/browse")(browse_pbix_files)


@router.post("/api/projects/{project_id}/upload")
async def upload_project_pbix_with_auth(project_id: str, request: Request, file: UploadFile = File(...)):
    user_id = require_request_user_id(request)
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id, log_prefix="ProjectUploadAuth"):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")
    return await upload_project_pbix(project_id, file)
