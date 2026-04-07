from fastapi import APIRouter

from semabridge.api.services.projects_service import (
    create_project_compat,
    delete_project_compat,
    get_project_compat,
    get_project_config_compat,
    list_projects_compat,
    patch_project_compat,
    save_project_config_compat,
)

router = APIRouter()
router.get('/api/projects')(list_projects_compat)
router.post('/api/projects')(create_project_compat)
router.get('/api/projects/{project_id}')(get_project_compat)
router.patch('/api/projects/{project_id}')(patch_project_compat)
router.delete('/api/projects/{project_id}')(delete_project_compat)
router.get('/api/projects/{project_id}/config')(get_project_config_compat)
router.put('/api/projects/{project_id}/config')(save_project_config_compat)
