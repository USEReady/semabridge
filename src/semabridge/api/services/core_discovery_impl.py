from semabridge.api.services.core_shared import *
from semabridge.api.services.core_shared import (
    _extract_bearer_token,
    _resolve_fabric_access_token,
    _discovery_cache,
    _DISCOVERY_CACHE_TTL,
    _time,
)

_snowflake_discovery_cache = {}


async def discover_fabric_models(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
):
    import anyio
    import httpx
    from pydantic import ValidationError

    try:
        settings = get_settings()
        try:
            settings.fabric
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fabric is not configured. "
                    "Set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in .env or environment variables."
                ),
            )

        resolved_workspace_id = (workspace_id or "").strip()
        if not resolved_workspace_id:
            resolved_workspace_id = os.environ.get("FABRIC_WORKSPACE_ID", "").strip()
        if not resolved_workspace_id:
            try:
                resolved_workspace_id = settings.fabric.workspace_id
            except Exception:
                pass

        if not resolved_workspace_id:
            raise HTTPException(
                status_code=400,
                detail="No Fabric workspace configured. Select a workspace in Settings -> Connections.",
            )

        cache_key = f"fabric:{resolved_workspace_id}:{identity_id}"
        cached = _discovery_cache.get(cache_key)
        if cached and _time.monotonic() < cached["expires_at"]:
            return cached["data"]

        access_token = await anyio.to_thread.run_sync(
            _resolve_fabric_access_token,
            bearer_token,
            identity_id,
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://api.fabric.microsoft.com/v1/workspaces/{resolved_workspace_id}/semanticModels",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            if "invalid_token" in resp.text.lower() or "expired" in resp.text.lower():
                raise HTTPException(
                    status_code=401,
                    detail="Fabric token expired or invalid. Please sign in again via Connections.",
                )
            raise HTTPException(
                status_code=401,
                detail="Fabric API returned 401. Possibly invalid workspace ID or insufficient permissions. Please sign in again.",
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"Fabric API error: {resp.text}")

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
        _discovery_cache[cache_key] = {"data": result, "expires_at": _time.monotonic() + _DISCOVERY_CACHE_TTL}
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error in Fabric discovery: %s", e)
        raise HTTPException(status_code=500, detail=f"Fabric discovery failed ({type(e).__name__}): {e}")


async def discover_fabric_models_by_workspace(
    workspace_id: str,
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
):
    return await discover_fabric_models(
        bearer_token=bearer_token,
        identity_id=identity_id,
        workspace_id=(workspace_id or "").strip() or None,
    )


def discover_snowflake(identity_id: Optional[str] = Query(None)):
    import time
    from pydantic import ValidationError
    from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
    from sqlalchemy import select
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.account_credential_resolver import scoped_account_env

    global _snowflake_discovery_cache
    if "_snowflake_discovery_cache" not in globals():
        _snowflake_discovery_cache = {}

    cache_key = f"views:{identity_id}" if identity_id else "views"
    if cache_key in _snowflake_discovery_cache:
        cached_data, cached_time = _snowflake_discovery_cache[cache_key]
        if time.monotonic() - cached_time < 300:
            return cached_data

    try:
        if identity_id:
            with db_manager.get_session() as session:
                account = session.execute(
                    select(Account).where(
                        Account.connector_type == "SNOWFLAKE",
                        Account.id == identity_id,
                    )
                ).scalars().first()

                if not account:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"No Snowflake account found for identity_id '{identity_id}'. "
                            "Link this account in Settings -> Connections or POST to /api/accounts with connector_type 'SNOWFLAKE'."
                        ),
                    )
                    
                with scoped_account_env(account, session):
                    from semabridge.core.settings import reload_settings
                    scoped_settings = reload_settings()
                    extractor = SnowflakeExtractor(scoped_settings.snowflake)
                    views = extractor.discover_semantic_views()
        else:
            settings = get_settings()
            try:
                snowflake_config = settings.snowflake
            except ValidationError:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Snowflake is not configured. "
                        "Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and SNOWFLAKE_PASSWORD in .env or environment variables."
                    ),
                )
            extractor = SnowflakeExtractor(snowflake_config)
            views = extractor.discover_semantic_views()

        # Build final view format
        snowflake_cfg_to_use = scoped_settings.snowflake if identity_id and 'scoped_settings' in locals() else snowflake_config
        final_results = sorted([
            {
                "id": v["name"],
                "name": v["name"],
                "type": "semantic_view",
                "status": "Available",
                "description": v.get("comment") or "",
                "created_on": v.get("created_on"),
                "schema": v.get("schema", snowflake_cfg_to_use.schema_name),
                "database": v.get("database", snowflake_cfg_to_use.database),
            }
            for v in views
        ], key=lambda x: x["name"])
        
        _snowflake_discovery_cache[cache_key] = (final_results, time.monotonic())
        return final_results
    except HTTPException:
        raise
    except AttributeError as ae:
        if "execute" in str(ae).lower() or "SnowflakeConnection" in str(ae):
            raise HTTPException(
                status_code=500,
                detail=f"Snowflake connection error - this is likely a driver issue. Please check your Snowflake connection. Error: {ae}",
            )
        raise HTTPException(status_code=500, detail=f"Snowflake semantic view discovery failed: {ae}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snowflake semantic view discovery failed: {e}")

def _get_snowflake_extractor(identity_id: Optional[str] = None):
    from pydantic import ValidationError
    from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.account_credential_resolver import scoped_account_env
    from fastapi import HTTPException
    from sqlalchemy import select

    if identity_id:
        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(
                    Account.connector_type == "SNOWFLAKE",
                    Account.id == identity_id,
                )
            ).scalars().first()

            if not account:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"No Snowflake account found for identity_id '{identity_id}'. "
                        "Link this account in Settings -> Connections or POST to /api/accounts with connector_type 'SNOWFLAKE'."
                    ),
                )
                
            with scoped_account_env(account, session):
                from semabridge.core.settings import reload_settings
                scoped_settings = reload_settings()
                return SnowflakeExtractor(scoped_settings.snowflake)
    else:
        settings = get_settings()
        try:
            snowflake_config = settings.snowflake
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Snowflake is not configured. "
                    "Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and SNOWFLAKE_PASSWORD in .env or environment variables."
                ),
            )
        return SnowflakeExtractor(snowflake_config)

def discover_snowflake_warehouses(identity_id: Optional[str] = Query(None)):
    try:
        extractor = _get_snowflake_extractor(identity_id)
        return extractor.get_warehouses()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snowflake warehouse discovery failed: {e}")

def discover_snowflake_databases(identity_id: Optional[str] = Query(None)):
    try:
        extractor = _get_snowflake_extractor(identity_id)
        return extractor.get_databases()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snowflake database discovery failed: {e}")

def discover_snowflake_schemas(database: str, identity_id: Optional[str] = Query(None)):
    try:
        extractor = _get_snowflake_extractor(identity_id)
        return extractor.get_schemas(database)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Snowflake schema discovery failed: {e}")



def discover_repository():
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select, func

    try:
        session = db_manager._session()
        try:
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
                results.append({
                    "id": row.model_id,
                    "name": f"{row.model_id}.yaml",
                    "type": "yaml",
                    "status": "Available",
                    "path": f"duckdb://{row.model_id}",
                    "versioned": count_map.get(row.model_id, 0) > 0,
                })
            return sorted(results, key=lambda x: x["name"])
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Repository discovery failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Failed to scan repository: {e}")


async def discover_multi_workspace(payload: Dict[str, Any]):
    from semabridge.connectors.multi_workspace_orchestrator import MultiWorkspaceOrchestrator

    workspace_ids = payload.get("workspace_ids", [])
    if not workspace_ids:
        raise HTTPException(status_code=400, detail="workspace_ids array is required")

    try:
        orchestrator = MultiWorkspaceOrchestrator(settings.fabric)
        results = orchestrator.discover_all_workspaces(workspace_ids=workspace_ids)
        summary = {ws_id: {"count": len(items), "items": items} for ws_id, items in results.items()}
        total = sum(len(items) for items in results.values())
        return {
            "status": "success",
            "workspaces_scanned": len(workspace_ids),
            "total_items_discovered": total,
            "results": summary,
        }
    except Exception as e:
        logger.exception("Multi-workspace discovery failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
