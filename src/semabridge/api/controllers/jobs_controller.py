from fastapi import APIRouter

from semabridge.api.services.jobs_service import (
    delete_project_schedule_compat,
    get_jobs_config_compat,
    get_project_schedule_compat,
    list_job_runs_compat,
    clear_job_runs_compat,
    list_job_schedules_compat,
    save_project_schedule_compat,
    trigger_job_compat,
    update_jobs_config_compat,
)

router = APIRouter()
router.get('/api/jobs/runs')(list_job_runs_compat)
router.delete('/api/jobs/runs')(clear_job_runs_compat)
router.get('/api/jobs/config')(get_jobs_config_compat)
router.get('/api/jobs/schedules')(list_job_schedules_compat)
router.get('/api/projects/{project_id}/schedule')(get_project_schedule_compat)
router.post('/api/projects/{project_id}/schedule')(save_project_schedule_compat)
router.delete('/api/projects/{project_id}/schedule')(delete_project_schedule_compat)
router.put('/api/jobs/config')(update_jobs_config_compat)
router.post('/api/jobs/trigger')(trigger_job_compat)
