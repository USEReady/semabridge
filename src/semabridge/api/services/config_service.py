import yaml
import json
import logging
from fastapi import HTTPException
from pathlib import Path

from semabridge.repository.model_repository import ModelRepository

logger = logging.getLogger("semabridge.api")
db_manager = ModelRepository()


async def get_config():
    from semabridge.core.config_loader import get_default_config_path

    config_path = get_default_config_path() or Path("config/semabridge.yaml")

    if not config_path.exists():
        raise HTTPException(status_code=404, detail="semabridge.yaml not found")

    return {"content": config_path.read_text(encoding="utf-8")}


async def validate_config(payload: dict):
    content = payload.get("content", "")

    try:
        yaml.safe_load(content)
        return {"valid": True, "errors": []}
    except yaml.YAMLError as e:
        return {"valid": False, "errors": [str(e)]}


async def validate_live(payload: dict = None):
    errors = []
    warnings = []

    try:
        conn = db_manager._get_connection()
        rows = conn.execute("SELECT model_id, snapshot FROM model_versions").fetchall()

        for model_id, snapshot in rows:
            try:
                data = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
            except:
                continue

            for ds in data.get("datasets", []):
                if not ds.get("source_table"):
                    warnings.append({
                        "model": model_id,
                        "message": "Missing source_table"
                    })

        conn.close()

    except Exception as e:
        errors.append(str(e))


    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


# --- MISSING SERVICE FUNCTIONS FOR CONFIG CONTROLLER ---
import datetime

async def generate_config(payload: dict):
    """
    Generate a new semabridge.yaml config file from a template or payload.
    """
    from semabridge.core.config_loader import get_default_config_path
    config_path = get_default_config_path() or Path("config/semabridge.yaml")
    content = payload.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="No config content provided.")
    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content, encoding="utf-8")
    return {"message": f"Config generated at {config_path}", "path": str(config_path)}


async def get_global_config():
    """
    Return the global config.yaml (system-wide settings).
    """
    from semabridge.core.global_settings import GlobalConfigManager
    mgr = GlobalConfigManager()
    try:
        cfg = mgr.load()
        if cfg is None:
            return {"exists": False, "content": "", "path": str(mgr.path)}
        import yaml as _yaml
        content = _yaml.dump(cfg.model_dump(), sort_keys=False, default_flow_style=False)
        return {"exists": True, "content": content, "path": str(mgr.path)}
    except Exception as e:
        logger.error(f"Failed to load global config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load global config: {e}")


async def save_global_config(payload: dict):
    """
    Save the global config.yaml (system-wide settings).
    """
    from semabridge.core.global_settings import GlobalConfigManager
    mgr = GlobalConfigManager()
    content = payload.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="No config content provided.")
    try:
        data = yaml.safe_load(content)
        mgr._path.parent.mkdir(parents=True, exist_ok=True)
        mgr._path.write_text(content, encoding="utf-8")
        return {"message": f"Global config saved at {mgr._path}", "path": str(mgr._path)}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    except Exception as e:
        logger.error(f"Failed to save global config: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save global config: {e}")