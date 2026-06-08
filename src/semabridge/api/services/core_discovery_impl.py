from semabridge.api.services.core_shared import *
from semabridge.api.services.core_shared import (
    _extract_bearer_token,
    _resolve_fabric_access_token,
    _discovery_cache,
    _DISCOVERY_CACHE_TTL,
    _time,
)
from semabridge.domain.exceptions import AuthenticationError, InternalError, SemaBridgeError, ValidationError

_snowflake_discovery_cache = {}


async def discover_fabric_models(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
):
    import anyio
    import httpx
    from pydantic import ValidationError as _PydanticValidationError

    try:
        settings = get_settings()

        resolved_workspace_id = (workspace_id or "").strip()

        # When no workspace_id is in the path, fall back to env / base config.
        # Only require base Fabric config when no identity_id is provided —
        # identity_id-based requests resolve credentials from the Account table.
        if not resolved_workspace_id:
            if not identity_id:
                try:
                    settings.fabric
                except _PydanticValidationError:
                    raise ValidationError((
                        "Fabric is not configured. "
                        "Set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in .env or environment variables."
                    ))
            resolved_workspace_id = os.environ.get("FABRIC_WORKSPACE_ID", "").strip()
            if not resolved_workspace_id:
                try:
                    resolved_workspace_id = settings.fabric.workspace_id
                except Exception as exc:
                    logger.debug("Could not read fabric.workspace_id from settings: %s", exc)

        if not resolved_workspace_id:
            raise ValidationError("No Fabric workspace configured. Select a workspace in Settings -> Connections.")

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
                raise AuthenticationError("Fabric token expired or invalid. Please sign in again via Connections.")
            raise AuthenticationError("Fabric API returned 401. Possibly invalid workspace ID or insufficient permissions. Please sign in again.")
        if resp.status_code != 200:
            raise InternalError(f"Fabric API error: {resp.text}")

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
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.exception("Unexpected error in Fabric discovery: %s", e)
        raise InternalError(f"Fabric discovery failed ({type(e).__name__}): {e}")


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
    from semabridge.connectors.factory import make_source_extractor
    from sqlalchemy import select
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.credential_builder import build_snowflake_config

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
                    raise ValidationError(
                        f"No Snowflake account found for identity_id '{identity_id}'. "
                        "Link this account in Settings → Connections."
                    )

                try:
                    base_cfg = get_settings().snowflake
                except Exception:
                    from semabridge.core.settings import SnowflakeConfig
                    base_cfg = SnowflakeConfig(account="placeholder", user="placeholder", warehouse="placeholder", database="placeholder")

                snowflake_cfg = build_snowflake_config(account, session, base_cfg)
                extractor = make_source_extractor("snowflake", snowflake_cfg)
                views = extractor.discover_semantic_views()
        else:
            try:
                snowflake_cfg = get_settings().snowflake
            except Exception:
                raise ValidationError(
                    "Snowflake is not configured. "
                    "Add a Snowflake connection in Settings → Connections."
                )
            extractor = make_source_extractor("snowflake", snowflake_cfg)
            views = extractor.discover_semantic_views()

        final_results = sorted([
            {
                "id": v["name"],
                "name": v["name"],
                "type": "semantic_view",
                "status": "Available",
                "description": v.get("comment") or "",
                "created_on": v.get("created_on"),
                "schema": v.get("schema", snowflake_cfg.schema_name),
                "database": v.get("database", snowflake_cfg.database),
            }
            for v in views
        ], key=lambda x: x["name"])
        
        _snowflake_discovery_cache[cache_key] = (final_results, time.monotonic())
        return final_results
    except SemaBridgeError:
        raise
    except AttributeError as ae:
        if "execute" in str(ae).lower() or "SnowflakeConnection" in str(ae):
            raise InternalError(f"Snowflake connection error - this is likely a driver issue. Please check your Snowflake connection. Error: {ae}")
        raise InternalError(f"Snowflake semantic view discovery failed: {ae}")
    except Exception as e:
        raise InternalError(f"Snowflake semantic view discovery failed: {e}")

def _get_snowflake_extractor(identity_id: Optional[str] = None):
    from semabridge.connectors.factory import make_source_extractor
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.credential_builder import build_snowflake_config
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
                raise ValidationError(
                    f"No Snowflake account found for identity_id '{identity_id}'. "
                    "Link this account in Settings → Connections."
                )

            try:
                base_cfg = get_settings().snowflake
            except Exception:
                from semabridge.core.settings import SnowflakeConfig
                base_cfg = SnowflakeConfig(account="placeholder", user="placeholder", warehouse="placeholder", database="placeholder")

            return make_source_extractor("snowflake", build_snowflake_config(account, session, base_cfg))
    else:
        try:
            snowflake_config = get_settings().snowflake
        except Exception:
            raise ValidationError(
                "Snowflake is not configured. "
                "Add a Snowflake connection in Settings → Connections."
            )
        return make_source_extractor("snowflake", snowflake_config)

def _execute_in_snowflake_context(func_name: str, identity_id: Optional[str] = None, *args, **kwargs):
    from semabridge.connectors.factory import make_source_extractor
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager
    from semabridge.auth.credential_builder import build_snowflake_config
    from sqlalchemy import select

    logger.info("Starting Snowflake discovery context for action: %s, identity_id: %s", func_name, identity_id)

    if identity_id:
        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(
                    Account.connector_type == "SNOWFLAKE",
                    Account.id == identity_id,
                )
            ).scalars().first()

            if not account:
                raise ValidationError(
                    f"No Snowflake account found for identity_id '{identity_id}'. "
                    "Link this account in Settings → Connections."
                )

            # Build a scoped SnowflakeConfig from the account bundle without
            # mutating os.environ (thread-safe for concurrent API requests).
            try:
                base_cfg = get_settings().snowflake
            except Exception:
                # No base Snowflake config in .env — use a minimal placeholder;
                # credential_builder will override all required fields from the account.
                from semabridge.core.settings import SnowflakeConfig
                base_cfg = SnowflakeConfig(
                    account="placeholder",
                    user="placeholder",
                    warehouse="placeholder",
                    database="placeholder",
                )

            try:
                snowflake_cfg = build_snowflake_config(account, session, base_cfg)
            except ValueError as ve:
                raise ValidationError(
                    f"Snowflake account '{account.tag}' is missing required credentials: {ve}. "
                    "Edit the connection in Settings → Connections and verify all fields."
                )
            extractor = make_source_extractor("snowflake", snowflake_cfg)
            func = getattr(extractor, func_name)
            try:
                return func(*args, **kwargs)
            except ValueError as ve:
                raise ValidationError(str(ve))
    else:
        logger.info("No identity_id provided, using default system settings for SnowflakeExtractor")
        try:
            snowflake_config = get_settings().snowflake
        except Exception as ve:
            logger.error("Snowflake config validation failed: %s", ve)
            raise ValidationError(
                "Snowflake is not configured. "
                "Add a Snowflake connection in Settings → Connections, or set "
                "SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and SNOWFLAKE_PASSWORD in .env."
            )
        extractor = make_source_extractor("snowflake", snowflake_config)
        func = getattr(extractor, func_name)
        try:
            return func(*args, **kwargs)
        except ValueError as ve:
            raise ValidationError(str(ve))


def discover_snowflake_warehouses(identity_id: Optional[str] = Query(None)):
    try:
        return _execute_in_snowflake_context("get_warehouses", identity_id)
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.exception("Snowflake warehouse discovery failed: %s", e)
        raise InternalError(f"Snowflake warehouse discovery failed: {e}")


def discover_snowflake_databases(identity_id: Optional[str] = Query(None)):
    try:
        return _execute_in_snowflake_context("get_databases", identity_id)
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.exception("Snowflake database discovery failed: %s", e)
        raise InternalError(f"Snowflake database discovery failed: {e}")


def discover_snowflake_schemas(database: str, identity_id: Optional[str] = Query(None)):
    try:
        return _execute_in_snowflake_context("get_schemas", identity_id, database)
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.exception("Snowflake schema discovery failed for database %s: %s", database, e)
        raise InternalError(f"Snowflake schema discovery failed: {e}")


def discover_repository():
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select, func

    try:
        with db_manager.get_session() as session:
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
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.error("Repository discovery failed: %s", e)
        raise InternalError(f"Failed to scan repository: {e}")


async def discover_multi_workspace(payload: Dict[str, Any]):
    from semabridge.connectors.multi_workspace_orchestrator import MultiWorkspaceOrchestrator

    workspace_ids = payload.get("workspace_ids", [])
    if not workspace_ids:
        raise ValidationError("workspace_ids array is required")

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
        raise InternalError(str(e))
