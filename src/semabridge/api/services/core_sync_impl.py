from semabridge.api.services.core_shared import *


async def sync_models(payload: Dict[str, Any]):
    """Trigger a full synchronization based on the current configuration."""
    try:
        return execute_sync_request(payload, _normalize_yaml_windows_path_fields)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sync execution failed")
        raise HTTPException(status_code=500, detail=str(e))
