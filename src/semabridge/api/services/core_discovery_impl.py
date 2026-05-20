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
    from semabridge.connectors.snowflake_extractor import ExtractionError
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
                    try:
                        views = extractor.discover_semantic_views()
                    except ExtractionError as exc:
                        raise HTTPException(
                            status_code=503,
                            detail=(
                                "Snowflake semantic view discovery is unavailable right now. "
                                f"{exc}"
                            ),
                        ) from exc
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
            try:
                views = extractor.discover_semantic_views()
            except ExtractionError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Snowflake semantic view discovery is unavailable right now. "
                        f"{exc}"
                    ),
                ) from exc

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



def _run_snowflake_discovery(identity_id: Optional[str], action):
    """Run a Snowflake metadata query against the configured or linked account."""
    from pydantic import ValidationError
    from semabridge.connectors.snowflake_extractor import ExtractionError
    from semabridge.auth.account_credential_resolver import scoped_account_env
    from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from sqlalchemy import select

    def _fallback_items(config, *, kind: str, database: Optional[str] = None):
        warehouse = str(getattr(config, "warehouse", "") or "").strip()
        db_name = str(getattr(config, "database", "") or "").strip()
        schema_name = str(getattr(config, "schema_name", "") or "").strip()

        if kind == "warehouses" and warehouse:
            return [{"id": warehouse, "name": warehouse}]
        if kind == "databases" and db_name:
            return [{"id": db_name, "name": db_name}]
        if kind == "schemas" and schema_name:
            # When the account cannot be queried, fall back to the configured
            # default schema for the selected database so the UI still reflects
            # the linked account's actual connection settings.
            if database and db_name and database.strip().upper() != db_name.strip().upper():
                return []
            return [{"id": schema_name, "name": schema_name}]
        return []

    def _map_connection_error(exc: Exception) -> HTTPException:
        message = str(exc)
        lowered = message.lower()
        if "free trial has ended" in lowered or "virtual warehouses have been suspended" in lowered:
            return HTTPException(
                status_code=503,
                detail=(
                    "Snowflake discovery is unavailable because the linked account's "
                    "virtual warehouses are suspended. Reactivate the warehouse or add "
                    "billing information in Snowflake, then try again."
                ),
            )
        if any(token in lowered for token in ("auth", "authentication", "password", "invalid username/password")):
            return HTTPException(
                status_code=401,
                detail=f"Snowflake authentication failed: {message}",
            )
        return HTTPException(
            status_code=503,
            detail=f"Snowflake discovery is unavailable: {message}",
        )

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
                try:
                    with extractor.connection() as conn:
                        return action(conn, scoped_settings.snowflake)
                except ExtractionError as exc:
                    logger.warning("Snowflake discovery failed for identity %s: %s", identity_id, exc)
                    lowered = str(exc).lower()
                    if "free trial has ended" in lowered or "virtual warehouses have been suspended" in lowered:
                        return _fallback_items(scoped_settings.snowflake, kind=getattr(action, "_discovery_kind", ""), database=getattr(action, "_discovery_database", None))
                    raise _map_connection_error(exc) from exc

    try:
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
        try:
            with extractor.connection() as conn:
                return action(conn, snowflake_config)
        except ExtractionError as exc:
            logger.warning("Snowflake discovery failed: %s", exc)
            lowered = str(exc).lower()
            if "free trial has ended" in lowered or "virtual warehouses have been suspended" in lowered:
                return _fallback_items(snowflake_config, kind=getattr(action, "_discovery_kind", ""), database=getattr(action, "_discovery_database", None))
            raise _map_connection_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Snowflake discovery helper failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Snowflake discovery failed: {exc}")


def _rows_to_discovery_items(rows, *, id_index: int = 1, name_index: int = 1):
    items = []
    for row in rows:
        row_id = ""
        row_name = ""
        try:
            row_id = str(row[id_index]).strip() if len(row) > id_index and row[id_index] is not None else ""
        except Exception:
            row_id = ""
        try:
            row_name = str(row[name_index]).strip() if len(row) > name_index and row[name_index] is not None else ""
        except Exception:
            row_name = ""

        value = row_id or row_name
        label = row_name or row_id
        if not value and not label:
            continue
        items.append({"id": value, "name": label})
    return items


def discover_snowflake_warehouses(identity_id: Optional[str] = Query(None)):
    def _action(conn, _config):
        cur = conn.cursor()
        try:
            cur.execute("SHOW WAREHOUSES")
            return _rows_to_discovery_items(cur.fetchall())
        finally:
            try:
                cur.close()
            except Exception:
                pass

    _action._discovery_kind = "warehouses"

    return _run_snowflake_discovery(identity_id, _action)


def discover_snowflake_databases(identity_id: Optional[str] = Query(None)):
    def _action(conn, _config):
        cur = conn.cursor()
        try:
            cur.execute("SHOW DATABASES")
            return _rows_to_discovery_items(cur.fetchall())
        finally:
            try:
                cur.close()
            except Exception:
                pass

    _action._discovery_kind = "databases"

    return _run_snowflake_discovery(identity_id, _action)


def discover_snowflake_schemas(database: str, identity_id: Optional[str] = Query(None)):
    db_name = str(database or "").strip()
    if not db_name:
        raise HTTPException(status_code=400, detail="database path parameter is required")
    quoted_db_name = db_name.replace('"', '""')

    def _action(conn, _config):
        cur = conn.cursor()
        try:
            cur.execute(f'SHOW SCHEMAS IN DATABASE "{quoted_db_name}"')
            return _rows_to_discovery_items(cur.fetchall())
        finally:
            try:
                cur.close()
            except Exception:
                pass

    _action._discovery_kind = "schemas"
    _action._discovery_database = db_name

    return _run_snowflake_discovery(identity_id, _action)


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
