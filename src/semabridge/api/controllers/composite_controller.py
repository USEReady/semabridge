from fastapi import APIRouter

from semabridge.api.services.composite_service import (
    get_all_composite_links,
    get_impact_analysis,
    register_composite_report,
)

router = APIRouter()
router.post('/api/composite/register')(register_composite_report)
router.get('/api/composite/impact/{model_guid}')(get_impact_analysis)
router.get('/api/composite/links')(get_all_composite_links)
