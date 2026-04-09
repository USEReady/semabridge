from fastapi import APIRouter

from semabridge.api.services.mappings_service import (
    auto_map_compat,
    delete_mappings_compat,
    list_mappings_compat,
    update_mapping_compat,
)

router = APIRouter()
router.get('/api/mappings')(list_mappings_compat)
router.post('/api/mappings/auto')(auto_map_compat)
router.put('/api/mappings/{mapping_id}')(update_mapping_compat)
router.delete('/api/mappings')(delete_mappings_compat)
