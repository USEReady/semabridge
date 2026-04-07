from fastapi import APIRouter

from semabridge.api.services.graph_service import (
    compare_graph_snapshots_compat,
    graph_snapshot_compat,
    graph_snapshots_compat,
)

router = APIRouter()
router.get('/api/graph/{model_name}/snapshots')(graph_snapshots_compat)
router.get('/api/graph/{model_name}/snapshot/{snapshot_id}')(graph_snapshot_compat)
router.get('/api/graph/{model_name}/compare')(compare_graph_snapshots_compat)
