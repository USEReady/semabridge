from semabridge.api.services.core_shared import *
import time as _time

_VALIDATE_LIVE_CACHE_TTL_SECONDS = 3.0
_validate_live_cache: Dict[str, Any] = {
    "fetched_at": 0.0,
    "rows_count": None,
    "latest_created_at": None,
    "result": None,
}


async def get_config():
    try:
        from semabridge.core.config_loader import get_default_config_path
        config_path = get_default_config_path() or Path("config/semabridge.yaml")
        if not config_path.exists():
            raise HTTPException(status_code=404, detail="semabridge.yaml not found in project")
        content = await asyncio.to_thread(config_path.read_text, encoding="utf-8")
        return {"content": content}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _global_config_path() -> Path:
    return Path.home() / ".semabridge" / "config.yaml"


async def get_global_config():
    config_path = _global_config_path()
    if not config_path.exists():
        default_content = """\
# SemaBridge Global Configuration
# Located at: ~/.semabridge/config.yaml

fabric:
  tenant_id: ""
  client_id: ""

snowflake:
  account: ""
  database: ""
  schema: "PUBLIC"
  warehouse: ""
  role: ""

logging:
  level: INFO
  file: ~/.semabridge/semabridge.log

defaults:
  source_type: fabric
  target_type: snowflake
"""
        return {"content": default_content, "path": str(config_path), "exists": False}
    try:
        content = await asyncio.to_thread(config_path.read_text, encoding="utf-8")
        return {"content": content, "path": str(config_path), "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read global config: {e}")


def _check_inline_secrets(data: Any, path: str, errors: list) -> None:
    secret_keys = {"password", "secret", "token", "api_key", "private_key"}
    if isinstance(data, dict):
        for key, value in data.items():
            full_path = f"{path}.{key}" if path else key
            key_lower = key.lower()
            if any(s in key_lower for s in secret_keys) and not key_lower.endswith("_env"):
                if isinstance(value, str) and value.strip():
                    errors.append(f"Inline secret at '{full_path}' - use '{key}_env' suffix to reference an environment variable instead")
            _check_inline_secrets(value, full_path, errors)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _check_inline_secrets(item, f"{path}[{i}]", errors)


async def save_global_config(payload: Dict[str, Any]):
    content = payload.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Config content cannot be empty")
    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Config must be a YAML mapping (dict)")
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML syntax: {e}")

    errors = []
    _check_inline_secrets(parsed, "", errors)
    if errors:
        raise HTTPException(status_code=400, detail=f"Security violation: {'; '.join(errors)}")

    config_path = _global_config_path()
    try:
        await asyncio.to_thread(config_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(config_path.write_text, content, encoding="utf-8")
        return {"status": "saved", "path": str(config_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save global config: {e}")


async def generate_config(payload: Dict[str, Any]):
    try:
        settings = get_settings()
        selected_models: List[str] = payload.get("models", [])
        model_ids: List[str] = payload.get("modelIds", [])
        source_type: str = payload.get("sourceType", "fabric")
        target_type: str = payload.get("targetType", "snowflake")
        pbix_folder: str = str(payload.get("pbixFolder", "") or "").strip()

        if not model_ids and selected_models:
            uuid_like = re.compile(r"^[0-9a-fA-F-]{36}$")
            model_ids = [m for m in selected_models if isinstance(m, str) and uuid_like.match(m.strip())]

        project_name = str(payload.get("projectName") or settings.model.name or "semabridge-project").strip()
        workspace_name = str(payload.get("workspaceName") or "SemaBridge Workspace").strip()

        source_cfg: Dict[str, Any] = {"type": source_type}
        if source_type == "fabric":
            source_cfg["workspace_id"] = settings.fabric.workspace_id or ""
            source_cfg["workspace"] = workspace_name
        elif source_type == "snowflake":
            source_cfg["database"] = settings.snowflake.database or ""
            source_cfg["schema"] = settings.snowflake.schema_name or "PUBLIC"
        elif source_type == "pbix":
            local_models_path = pbix_folder or str(await asyncio.to_thread(_resolve_models_path))
            source_cfg["pbix_folder"] = local_models_path.replace("\\", "/")

        if selected_models:
            source_cfg["models"] = selected_models
        else:
            source_cfg["model"] = "*"

        config_doc: Dict[str, Any] = {
            "project_name": project_name,
            "source": source_cfg,
            "target": {"type": target_type},
            "ui": {"output_format": "osi", "editor_mode": "yaml"},
            "options": {
                "auto_relationships": True,
                "include_hidden_fields": True,
                "generate_descriptions": True,
            },
        }
        if model_ids:
            config_doc["selection"] = {"model_ids": model_ids}

        return {
            "content": yaml.safe_dump(
                config_doc,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )
        }
    except Exception as e:
        logger.exception("YAML generation failed")
        raise HTTPException(status_code=500, detail=str(e))


async def validate_config(payload: Dict[str, str]):
    content = payload.get("content", "")
    errors = []
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return {
            "valid": False,
            "errors": [{
                "line": getattr(e, "problem_mark", None).line + 1 if hasattr(e, "problem_mark") else 0,
                "message": str(e),
            }],
        }

    for section in ["source", "target"]:
        if section not in parsed:
            errors.append({"line": 0, "message": f"Missing required section: '{section}'"})

    return {"valid": len(errors) == 0, "errors": errors, "warnings": []}


async def get_history():
    try:
        project_id = settings.model.name
        snapshots = db_manager.list_snapshots(project_id, limit=20)
        return [
            {
                "version_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "description": s.version_tag or "Automated Sync",
                "status": s.status,
            }
            for s in snapshots
        ]
    except Exception as e:
        logger.exception("History fetch failed")
        raise HTTPException(status_code=500, detail=str(e))


async def validate_live(payload: Dict[str, Any] = None):
    errors: list[dict] = []
    warnings: list[dict] = []
    row_count = None
    latest_created_at = None
    try:
        conn = db_manager._get_connection()
        try:
            now_ts = _time.time()
            sig_row = conn.execute(
                """
                SELECT COUNT(*) AS row_count, MAX(created_at) AS latest_created_at
                FROM model_versions
                """
            ).fetchone()
            row_count = int(sig_row[0] or 0) if sig_row else 0
            latest_created_at = str(sig_row[1] or "") if sig_row else ""

            cache_fresh = (now_ts - float(_validate_live_cache.get("fetched_at") or 0.0)) < _VALIDATE_LIVE_CACHE_TTL_SECONDS
            cache_same_data = (
                _validate_live_cache.get("rows_count") == row_count
                and _validate_live_cache.get("latest_created_at") == latest_created_at
            )
            if cache_fresh and cache_same_data and isinstance(_validate_live_cache.get("result"), dict):
                return dict(_validate_live_cache["result"])

            rows = conn.execute(
                """
                WITH RankedVersions AS (
                    SELECT model_id, snapshot, created_at,
                           ROW_NUMBER() OVER(PARTITION BY model_id ORDER BY created_at DESC) as rn
                    FROM model_versions
                )
                SELECT model_id, snapshot
                FROM RankedVersions
                WHERE rn = 1
                """
            ).fetchall()

            for model_id, snapshot_data in rows:
                if isinstance(snapshot_data, str):
                    try:
                        data = json.loads(snapshot_data)
                    except Exception as e:
                        errors.append({"model": model_id, "severity": "error", "message": f"Failed to parse JSON: {e}"})
                        continue
                else:
                    data = snapshot_data or {}
                if not isinstance(data, dict):
                    continue
                display_model_name = (
                    str(data.get("model_name") or "").strip()
                    or str(data.get("name") or "").strip()
                    or str(data.get("label") or "").strip()
                    or str(data.get("unique_name") or "").strip()
                    or model_id
                )
                # Pre-calculate datasets that have metrics to avoid false-positive "no columns" warnings on measure tables.
                datasets_with_metrics = {m.get("dataset") for m in data.get("metrics", []) if m.get("dataset")}

                for ds in data.get("datasets", []):
                    tbl = ds.get("source_table") or ds.get("table", "")
                    cols = ds.get("columns", [])
                    ds_name = ds.get("unique_name") or ds.get("name") or "?"
                    display_ds_name = ds.get("name") or ds.get("unique_name") or "?"
                    
                    if not tbl:
                        warnings.append({"model": display_model_name, "severity": "warning", "message": f"Dataset '{display_ds_name}' has no source_table defined"})
                    
                    # Only warn about missing columns if there are also no metrics referencing this dataset.
                    # Measure-only tables are valid architectural constructs in Fabric/TMSL models.
                    if not cols and ds_name not in datasets_with_metrics:
                        warnings.append({"model": display_model_name, "severity": "warning", "message": f"Dataset '{display_ds_name}' has no columns defined"})
        finally:
            conn.close()
    except Exception as e:
        errors.append({"model": "system", "severity": "error", "message": str(e)})

    result = {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "total_issues": len(errors) + len(warnings)}
    _validate_live_cache["fetched_at"] = _time.time()
    _validate_live_cache["rows_count"] = row_count
    _validate_live_cache["latest_created_at"] = latest_created_at
    _validate_live_cache["result"] = result
    return result
