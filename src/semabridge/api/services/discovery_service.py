import os
import asyncio
import logging
import time as _time
import httpx
import json

from fastapi import HTTPException
from pydantic import ValidationError

from semabridge.core.settings import get_settings
from semabridge.repository.credential_manager import CredentialManager
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
from semabridge.repository.model_repository import ModelRepository

logger = logging.getLogger("semabridge.api")

db_manager = ModelRepository()

# cache
_discovery_cache = {}
_DISCOVERY_CACHE_TTL = 300


# -------------------------------------------------------
# FABRIC DISCOVERY
# -------------------------------------------------------

async def discover_fabric_models():
    try:
        settings = get_settings()

        try:
            settings.fabric
        except ValidationError:
            raise HTTPException(status_code=400, detail="Fabric not configured")

        cm = CredentialManager()
        auth_method = cm.get_fabric_auth_method()

        env_token = os.environ.get("FABRIC_ACCESS_TOKEN", "").strip()
        if env_token and auth_method == "none":
            auth_method = "env_token"

        fabric_creds = cm.get_credentials("fabric", mask_secrets=False)

        workspace_id = (
            fabric_creds.get("workspace_id")
            or os.environ.get("FABRIC_WORKSPACE_ID", "")
        ).strip()

        if not workspace_id:
            raise HTTPException(status_code=400, detail="No workspace configured")

        # cache
        cache_key = f"fabric:{workspace_id}"
        cached = _discovery_cache.get(cache_key)
        if cached and _time.monotonic() < cached["expires_at"]:
            return cached["data"]

        access_token = env_token or ""

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://api.fabric.microsoft.com/v1/workspaces/{workspace_id}/semanticModels",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)

        models = resp.json().get("value", [])

        result = [
            {
                "id": m.get("id", ""),
                "name": m.get("displayName", "Unnamed"),
                "type": "semantic_model",
                "status": "Available",
            }
            for m in models
        ]

        _discovery_cache[cache_key] = {
            "data": result,
            "expires_at": _time.monotonic() + _DISCOVERY_CACHE_TTL,
        }

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Fabric discovery failed")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# SNOWFLAKE DISCOVERY
# -------------------------------------------------------

async def discover_snowflake():
    try:
        settings = get_settings()
        extractor = SnowflakeExtractor(settings.snowflake)

        views = extractor.discover_semantic_views()

        return [
            {
                "id": v["name"],
                "name": v["name"],
                "type": "semantic_view",
                "status": "Available",
                "description": v.get("comment", ""),
            }
            for v in views
        ]

    except Exception as e:
        logger.error(f"Snowflake discovery failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# REPOSITORY DISCOVERY
# -------------------------------------------------------

async def discover_repository():
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select, func

    try:
        session = db_manager._session()

        subq = (
            select(
                ModelVersion.model_id,
                func.max(ModelVersion.created_at).label("max_ts"),
            )
            .group_by(ModelVersion.model_id)
            .subquery()
        )

        latest_rows = session.execute(
            select(ModelVersion).join(
                subq,
                (ModelVersion.model_id == subq.c.model_id)
                & (ModelVersion.created_at == subq.c.max_ts),
            )
        ).scalars().all()

        count_rows = session.execute(
            select(ModelVersion.model_id, func.count().label("cnt"))
            .group_by(ModelVersion.model_id)
        ).all()

        count_map = {r.model_id: r.cnt for r in count_rows}

        results = []

        for row in latest_rows:
            model_id = row.model_id

            results.append({
                "id": model_id,
                "name": f"{model_id}.yaml",
                "type": "yaml",
                "status": "Available",
                "path": f"duckdb://{model_id}",
                "versioned": count_map.get(model_id, 0) > 0,
            })

        return sorted(results, key=lambda x: x["name"])

    except Exception as e:
        logger.error(f"Repository discovery failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------
# SEMANTIC DISCOVERY (FIXED)
# -------------------------------------------------------

async def discover_semantic():
    fabric_items = []
    snowflake_items = []
    fabric_error = None
    snowflake_error = None

    try:
        settings = get_settings()

        # Fabric
        try:
            extractor = FabricExtractor(settings.fabric)
            models = extractor.list_semantic_models()

            fabric_items = [
                {
                    "id": m.get("id"),
                    "name": m.get("displayName"),
                    "platform": "fabric",
                }
                for m in models
            ]
        except Exception as e:
            fabric_error = str(e)
            logger.warning(f"Fabric semantic error: {e}")

        # Snowflake
        try:
            extractor = SnowflakeExtractor(settings.snowflake)
            views = extractor.discover_semantic_views()

            snowflake_items = [
                {
                    "id": v["name"],
                    "name": v["name"],
                    "platform": "snowflake",
                }
                for v in views
            ]
        except Exception as e:
            snowflake_error = str(e)
            logger.warning(f"Snowflake semantic error: {e}")

        return {
            "fabric": fabric_items,
            "snowflake": snowflake_items,
            "fabric_error": fabric_error,
            "snowflake_error": snowflake_error,
        }

    except Exception as e:
        logger.error(f"Semantic discovery failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))