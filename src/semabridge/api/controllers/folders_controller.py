from fastapi import APIRouter

from semabridge.api.services.folders_service import (
    create_folder_compat,
    delete_folder_compat,
    list_folders_compat,
    move_project_to_folder_compat,
    rename_folder_compat,
)

router = APIRouter()
router.get('/api/folders')(list_folders_compat)
router.post('/api/folders')(create_folder_compat)
router.patch('/api/folders/{folder_id}')(rename_folder_compat)
router.delete('/api/folders/{folder_id}')(delete_folder_compat)
router.patch('/api/projects/{project_id}/folder')(move_project_to_folder_compat)
