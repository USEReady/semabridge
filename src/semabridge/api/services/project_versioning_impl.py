from semabridge.api.services.project_shared import *
from semabridge.domain.exceptions import InternalError, NotFoundError, SemaBridgeError, ValidationError

async def list_model_versions(
    model_id: str = "",
    workspace_id: str = "",
    limit: int = 50,
):
    """
    List version history.  If model_id is blank, return versions
    for ALL models found in DuckDB.
    """
    try:
        all_versions = version_control_service.list_versions(
            model_id=model_id,
            workspace_id=workspace_id,
            limit=limit,
        )

        # Mark capability flags for DuckDB-backed entries
        for item in all_versions:
            item.setdefault("source", "model_versions")
            item.setdefault("can_compare", True)
            item.setdefault("can_rollback", True)

        # Add ORM-backed config version history (ModelVersionHistory)
        try:
            from semabridge.repository.orm.models import ModelVersionHistory
            from semabridge.repository.orm.session_factory import get_session_factory

            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                query = session.query(ModelVersionHistory)
                if model_id:
                    query = query.filter(ModelVersionHistory.model_name == model_id)
                rows = query.order_by(ModelVersionHistory.applied_at.desc()).limit(limit).all()

            for row in rows:
                all_versions.append(
                    {
                        "version_id": f"cfgdb-{row.id}",
                        "model_id": row.model_name,
                        "workspace_id": workspace_id or "",
                        "author": "ui",
                        "timestamp": row.applied_at.isoformat() if row.applied_at else "",
                        "description": f"Config version {row.version_tag}",
                        "version_tag": row.version_tag,
                        "is_rollback": False,
                        "rollback_from_version": None,
                        "source": "config_db",
                        "can_compare": False,
                        "can_rollback": False,
                    }
                )
        except Exception as orm_exc:
            logger.debug(f"ORM model version history unavailable: {orm_exc}")

        # Sort by timestamp descending, cap at limit
        all_versions.sort(key=lambda v: v.get("timestamp", ""), reverse=True)
        return all_versions[:limit]
    except Exception as e:
        logger.error(f"list_model_versions failed: {e}", exc_info=True)
        raise InternalError(f"Failed to load version history: {e}")


async def compare_model_versions(v1: str = "", v2: str = ""):
    """Compare two model versions and return tabular diff."""
    try:
        diffs = version_control_service.compare_versions(v1, v2)
        return {"changes": diffs}
    except Exception as e:
        logger.warning(f"compare_model_versions failed: {e}")
        return {"changes": []}


async def delete_model_versions(
    model_id: str,
    workspace_id: str = "",
):
    """Delete all version history rows for a model."""
    if not model_id:
        raise ValidationError("model_id is required")
    try:
        deleted = version_control_service.delete_versions(
            model_id=model_id,
            workspace_id=workspace_id,
        )
        # Invalidate discovery cache entries for this model so stale data isn't served
        keys_to_clear = [k for k in _discovery_cache if model_id in k]
        for k in keys_to_clear:
            _discovery_cache.pop(k, None)
        logger.info(f"Deleted {deleted} version(s) for model={model_id} via API")
        return {"deleted": deleted, "model_id": model_id}
    except Exception as e:
        logger.warning(f"delete_model_versions failed: {e}")
        raise InternalError(str(e))


async def get_version_snapshot(version_id: str = ""):
    """Get the full snapshot JSON for a specific version."""
    if not version_id:
        raise ValidationError("version_id is required")
    try:
        snapshot = version_control_service.get_snapshot(version_id)
        if snapshot is None:
            raise NotFoundError(f"Version '{version_id}' not found")
        return {"version_id": version_id, "snapshot": snapshot}
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.warning(f"get_version_snapshot failed: {e}")
        raise InternalError(str(e))


async def rollback_model_version(payload: Dict[str, Any]):
    """
    Rollback a model to a previous version (non-destructive).

    Creates a new DuckDB version (flagged as rollback) AND writes the
    snapshot back to the local repository YAML file so the file system
    stays in sync.
    """
    version_id = payload.get("version_id")
    model_id = payload.get("model_id", "default")
    workspace_id = payload.get("workspace_id", "default")
    if not version_id:
        raise ValidationError("version_id is required")
    try:
        return version_control_service.rollback_version(
            version_id=version_id,
            model_id=model_id,
            workspace_id=workspace_id,
            author="ui",
        )
    except Exception as e:
        logger.exception(f"rollback_model_version failed: {e}")
        raise InternalError(str(e))


# -------------------------------------------------------
# Legacy Compare & Rollback (kept for backward compat)
# -------------------------------------------------------

async def compare_versions(version_from: str = "", version_to: str = ""):
    """Compare two version snapshots and return a list of changes."""
    try:
        changes = db_manager.compare_versions(version_from, version_to)
        return {"changes": changes}
    except Exception as e:
        logger.warning(f"Compare failed: {e}")
        return {"changes": []}


async def rollback_version(payload: Dict[str, Any]):
    """Rollback to a specific version."""
    version_id = payload.get("version_id")
    if not version_id:
        raise ValidationError("version_id is required")
    try:
        result = db_manager.rollback_to_version(version_id)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.exception(f"Rollback failed: {e}")
        raise InternalError(str(e))