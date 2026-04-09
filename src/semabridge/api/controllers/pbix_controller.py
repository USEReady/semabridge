from fastapi import APIRouter

from semabridge.api.services.pbix_service import (
    browse_pbix_files,
    import_pbix,
    upload_pbix_temp,
    upload_project_pbix,
)

router = APIRouter()
router.post('/api/upload')(upload_pbix_temp)
router.post('/api/projects/{project_id}/upload')(upload_project_pbix)
router.post('/api/pbix/import')(import_pbix)
router.get('/api/pbix/browse')(browse_pbix_files)
