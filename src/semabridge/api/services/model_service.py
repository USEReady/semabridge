import json
import yaml
import hashlib
import logging
from fastapi import HTTPException
from sqlalchemy import select

from semabridge.repository.model_repository import ModelRepository
from semabridge.repository.orm.models import ModelVersion
from semabridge.core.settings import get_settings

logger = logging.getLogger("semabridge.api")

db_manager = ModelRepository()
settings = get_settings()
_last_snapshot_hash = {}


async def get_model(model_id: str):
    session = db_manager._session()
    try:
        row = session.execute(
            select(ModelVersion)
            .where(ModelVersion.model_id == model_id)
            .order_by(ModelVersion.created_at.desc())
            .limit(1)
        ).scalars().first()

        if not row:
            raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")

        snapshot_data = row.snapshot

        if isinstance(snapshot_data, str):
            try:
                parsed = json.loads(snapshot_data)
            except Exception:
                parsed = {}
        else:
            parsed = snapshot_data or {}

        content = yaml.dump(parsed, sort_keys=False) if parsed else ""

        return {
            "model_id": model_id,
            "filename": f"{model_id}.yaml",
            "content": content,
            "path": f"duckdb://{model_id}",
        }

    finally:
        session.close()


async def save_model(model_id: str, payload: dict):
    content = payload.get("content", "")
    author = payload.get("author", "ui")
    message = payload.get("message", "Saved from UI")
    version_tag = payload.get("version_tag", None)

    if not content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        parsed = {"raw": content}

    if not version_tag and isinstance(parsed, dict):
        version_tag = parsed.get("version_tag") or parsed.get("version") or None

    ws_id = "local"
    try:
        ws_id = settings.fabric.workspace_id or "local"
    except Exception:
        pass

    version_id = db_manager.insert_model_version(
        model_id=model_id,
        workspace_id=ws_id,
        snapshot=parsed,
        author=author,
        change_summary=message,
        version_tag=version_tag,
    )

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _last_snapshot_hash[model_id] = content_hash

    logger.info(f"Versioned {model_id} → {version_id[:8]} by {author}")

    return {
        "status": "saved",
        "model_id": model_id,
        "version_id": version_id,
        "version_tag": version_tag,
        "filename": f"{model_id}.yaml",
        "path": f"duckdb://{model_id}",
    }