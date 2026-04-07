from fastapi import APIRouter

from semabridge.api.services.project_runs_service import get_project_runs_compat, run_project_now_compat

router = APIRouter()
router.get('/api/projects/{project_id}/runs')(get_project_runs_compat)
router.post('/api/projects/{project_id}/run')(run_project_now_compat)
