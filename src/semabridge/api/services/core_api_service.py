from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
import time
import time as _time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import BackgroundTasks, Depends, File, Header, HTTPException, Query, UploadFile
from semabridge.api.deps import get_db
from semabridge.api.semantic_models import SemanticRefreshRequest, SemanticSyncRequest
from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.api.services.version_control_service import VersionControlService
from semabridge.auth.fabric_validator import fabric_validator
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.core.env import get_fabric_access_token_from_env
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import setup_logging
from sqlalchemy.orm import Session

from semabridge.api.services.connection_api_service import (
    _extract_bearer_token,
    _resolve_fabric_access_token,
)

setup_logging(level="INFO")
logger = logging.getLogger("semabridge.api")
db_manager = ModelRepository()
engine = ExecutionEngine(db_manager=db_manager)
settings = get_settings()
_last_snapshot_hash: dict[str, str] = {}
_discovery_cache: Dict[str, Any] = {}
_DISCOVERY_CACHE_TTL = 60

version_control_service = VersionControlService(
    db_manager=db_manager,
    models_path_resolver=lambda: _resolve_models_path(),
    hash_tracker=_last_snapshot_hash,
)
def _normalize_yaml_windows_path_fields(yaml_text: str) -> str:
    """Normalize backslashes in known YAML path fields to forward slashes.

    Prevents invalid escape sequence errors like "\\U" inside double-quoted
    Windows paths for fields such as pbix_folder.
    """
    pattern = re.compile(
        r'^(\s*(?:pbix_folder|pbix_path|repository_path)\s*:\s*")([^"]*)("\s*)$',
        flags=re.MULTILINE,
    )

    def _replace(match: re.Match[str]) -> str:
        prefix, value, suffix = match.groups()
        safe_value = value.replace("\\", "/")
        return f"{prefix}{safe_value}{suffix}"

    return pattern.sub(_replace, yaml_text)

def _resolve_models_path() -> Path:
    """
    Resolve the local models directory using a strict precedence chain.

        Resolution order:
            1. SEMABRIDGE_LOCAL_MODELS_PATH env var
            2. core.local_models_path in config.yaml (from semabridge init)
            3. semabridge.yaml source.pbix_folder
            4. semabridge.yaml source.repository_path
            5. Default: project root (current working directory)
    """
    # 1. Environment variable
    env_val = os.environ.get("SEMABRIDGE_LOCAL_MODELS_PATH")
    if env_val:
        return Path(env_val).expanduser().resolve()

    # 2. Global config.yaml
    try:
        from semabridge.core.initializer import SemabridgeInitializer
        global_cfg_path = SemabridgeInitializer.get_default_config_path()
        if global_cfg_path.exists():
            global_cfg = yaml.safe_load(global_cfg_path.read_text(encoding="utf-8"))
            raw = (global_cfg or {}).get("core", {}).get("local_models_path")
            if raw:
                return Path(raw).expanduser().resolve()
    except Exception:
        pass

    # 3/4. Project-level semabridge.yaml
    try:
        from semabridge.core.config_loader import get_default_config_path
        sema_yaml = get_default_config_path() or Path("config/semabridge.yaml")
        if sema_yaml.exists():
            cfg = yaml.safe_load(sema_yaml.read_text(encoding="utf-8"))
            source_cfg = (cfg or {}).get("source", {})
            raw = source_cfg.get("pbix_folder") or source_cfg.get("repository_path")
            if raw:
                return Path(raw).expanduser().resolve()
    except Exception:
        pass

    # 5. Default â€” project root (no more ./models subdirectory)
    return Path.cwd()

def _snapshot_model_if_changed(
    model_id: str, content: str, author: str = "discovery",
) -> str | None:
    """
    Create a DuckDB version row for a model ONLY if its content has changed
    since the last snapshot.  Returns the new version_id or None if skipped.

    Extracts `version_tag` from the model YAML itself so each version
    is tagged with the model's own semantic version identifier.
    """
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if _last_snapshot_hash.get(model_id) == content_hash:
        return None

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        parsed = {"raw": content}

    # Auto-extract version_tag from the model YAML
    version_tag = parsed.get("version_tag") or parsed.get("version") or None

    ws_id = ""
    try:
        ws_id = settings.fabric.workspace_id
    except Exception:
        ws_id = "local"

    version_id = db_manager.insert_model_version(
        model_id=model_id,
        workspace_id=ws_id or "local",
        snapshot=parsed,
        author=author,
        change_summary=f"Snapshot by {author}",
        version_tag=version_tag,
    )
    _last_snapshot_hash[model_id] = content_hash
    return version_id

async def health_check():
    """Return service status including database connectivity.

    Executes a lightweight ``SELECT 1`` ping using the current engine.
    If the ping fails the response indicates a degraded state so
    load-balancers and monitoring tools can react accordingly.
    """
    from sqlalchemy import text
    from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager

    db_status = "connected"
    db_dialect = "unknown"
    try:
        _engine = _orm_db_manager.get_engine()
        db_dialect = _engine.dialect.name
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check DB ping failed: %s", exc)
        db_status = "unreachable"

    overall = "ok" if db_status == "connected" else "degraded"
    return {
        "status": overall,
        "service": "semabridge-api",
        "database": db_status,
        "db_dialect": db_dialect,
    }

async def discover_fabric_models(
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    workspace_id: Optional[str] = Query(None),
):
    import asyncio
    from pydantic import ValidationError
    import anyio
    import httpx

    try:
        settings = get_settings()
        
        # Validate Fabric configuration exists and is properly set up
        try:
            fabric_config = settings.fabric
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fabric is not configured. "
                    "Set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in .env or environment variables."
                )
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

        logger.info(
            "Attempting to discover Fabric models in workspace: %s with Identity: %s",
            resolved_workspace_id,
            identity_id,
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"https://api.fabric.microsoft.com/v1/workspaces/{resolved_workspace_id}/semanticModels",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code == 401:
            logger.error("Fabric API returned 401 Unauthorized. Token may be invalid or expired.")
            logger.error("Response: %s", resp.text[:500])
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
            logger.error("Fabric API error %s: %s", resp.status_code, resp.text[:500])
            raise HTTPException(status_code=resp.status_code, detail=f"Fabric API error: {resp.text}")

        models = resp.json().get("value", [])
        logger.info("Successfully discovered %s Fabric semantic models", len(models))
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
        err_str = str(e) or f"<no message from {type(e).__name__}>"
        logger.exception(f"Unexpected error in Fabric discovery: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Fabric discovery failed ({type(e).__name__}): {err_str}",
        )

async def discover_fabric_models_by_workspace(
    workspace_id: str,
    bearer_token: Optional[str] = Depends(_extract_bearer_token),
    identity_id: Optional[str] = Query(None),
    connection_id: Optional[str] = Query(None, alias="connectionId"),
):
    """Compatibility route for workspace-scoped Fabric discovery."""
    return await discover_fabric_models(
        bearer_token=bearer_token,
        identity_id=(identity_id or connection_id),
        workspace_id=(workspace_id or "").strip() or None,
    )

async def discover_snowflake():
    """
    Discover **semantic views** in the configured Snowflake schema.

    Physical tables are no longer returned by this endpoint; only Snowflake
    semantic views (``CREATE OR REPLACE SEMANTIC VIEW``) are exposed as the
    primary data objects in the discovery process.

    Returns a list of objects with keys: id, name, type, status, description.
    Falls back to a DDL-scan approach when ``SHOW SEMANTIC VIEWS`` is
    unavailable in the connected environment.
    """
    import time
    global _snowflake_discovery_cache
    if '_snowflake_discovery_cache' not in globals():
        _snowflake_discovery_cache = {}

    cache_key = "views"
    if cache_key in _snowflake_discovery_cache:
        cached_data, cached_time = _snowflake_discovery_cache[cache_key]
        if time.monotonic() - cached_time < 300: # 5 minutes TTL
            return cached_data

    try:
        from pydantic import ValidationError
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor

        # Re-inject stored credentials and bust the settings cache so that
        # credentials saved via the UI (Settings → Connections or SSO) are
        # always visible to SnowflakeConfig, even if they were stored after
        # the last server restart.
        try:
            from semabridge.repository.credential_manager import CredentialManager
            from semabridge.core.settings import reload_settings
            _cm = CredentialManager()
            _cm.inject_credentials_to_env("snowflake")
            settings = reload_settings()
        except Exception:
            settings = get_settings()

        # Validate Snowflake configuration exists
        try:
            snowflake_config = settings.snowflake
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Snowflake is not configured. "
                    "Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, and SNOWFLAKE_PASSWORD in .env or environment variables."
                )
            )
        
        extractor = SnowflakeExtractor(snowflake_config)

        logger.info(
            f"Discovering Snowflake semantic views in "
            f"{snowflake_config.database}.{snowflake_config.schema_name}"
        )

        views = extractor.discover_semantic_views()

        results = [
            {
                "id": v["name"],
                "name": v["name"],
                "type": "semantic_view",
                "status": "Available",
                "description": v.get("comment") or "",
                "created_on": v.get("created_on"),
                "schema": v.get("schema", snowflake_config.schema_name),
                "database": v.get("database", snowflake_config.database),
            }
            for v in views
        ]

        logger.info(f"Discovered {len(results)} Snowflake semantic view(s)")
        
        final_results = sorted(results, key=lambda x: x["name"])
        import time
        _snowflake_discovery_cache[cache_key] = (final_results, time.monotonic())
        return final_results

    except HTTPException:
        raise
    except AttributeError as ae:
        logger.error(f"Snowflake semantic view discovery failed (AttributeError): {ae}")
        # Catch the specific SnowflakeConnection.execute() error
        if "execute" in str(ae).lower() or "SnowflakeConnection" in str(ae):
            raise HTTPException(
                status_code=500,
                detail=f"Snowflake connection error - this is likely a driver issue. Please check your Snowflake connection. Error: {ae}",
            )
        raise HTTPException(
            status_code=500,
            detail=f"Snowflake semantic view discovery failed: {ae}",
        )
    except Exception as e:
        logger.error(f"Snowflake semantic view discovery failed: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Snowflake semantic view discovery failed: {e}",
        )

async def discover_repository():
    """
    Discover local models from the repository.
    Uses SQLAlchemy ORM so it works with any configured backend
    (DuckDB, PostgreSQL, SQLite, etc.).
    """
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select, func

    try:
        session = db_manager._session()
        try:
            # Subquery: latest created_at timestamp per model_id
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

            # Version counts per model_id
            count_rows = session.execute(
                select(ModelVersion.model_id, func.count().label("cnt"))
                .group_by(ModelVersion.model_id)
            ).all()
            count_map = {r.model_id: r.cnt for r in count_rows}

            results = []
            for row in latest_rows:
                model_id = row.model_id
                snapshot_data = row.snapshot
                if isinstance(snapshot_data, str):
                    try:
                        snapshot = json.loads(snapshot_data)
                    except Exception:
                        snapshot = {}
                else:
                    snapshot = snapshot_data or {}

                results.append({
                    "id": model_id,
                    "name": f"{model_id}.yaml",
                    "type": "yaml",
                    "status": "Available",
                    "path": f"duckdb://{model_id}",
                    "versioned": count_map.get(model_id, 0) > 0,
                })

            return sorted(results, key=lambda x: x["name"])
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Repository discovery failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to scan repository: {e}")

async def discover_semantic():
    """
    Unified semantic discovery across **both** Fabric and Snowflake.

    Returns semantic models from Fabric and semantic views from Snowflake
    together with cross-platform mapping / sync-status information.
    Platform errors are surfaced as non-fatal fields so partial results
    are always returned.
    """
    from pydantic import ValidationError
    from semabridge.api.semantic_models import (
        SemanticDiscoveryResponse,
        SemanticMappingItem,
        SemanticModelItem,
    )

    fabric_items: list = []
    snowflake_items: list = []
    fabric_error: Optional[str] = None
    snowflake_error: Optional[str] = None

    # --- Fabric ---
    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        settings = get_settings()
        
        # Check if Fabric is properly configured before trying to initialize
        try:
            fabric_config = settings.fabric
        except ValidationError as ve:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fabric is not properly configured. "
                    "Please set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in settings."
                )
            )
        
        extractor = FabricExtractor(fabric_config)
        models = extractor.list_semantic_models()
        fabric_items = [
            SemanticModelItem(
                id=m.get("id", m.get("displayName", "")),
                name=m.get("displayName", ""),
                type="semantic_model",
                platform="fabric",
                description=m.get("description", ""),
                extra={k: v for k, v in m.items() if k not in ("id", "displayName", "description")},
            )
            for m in models
        ]
    except HTTPException:
        raise
    except Exception as exc:
        fabric_error = str(exc)
        logger.warning(f"Fabric semantic discovery failed (non-fatal): {exc}")

    # --- Snowflake ---
    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        settings = get_settings()
        
        # Check if Snowflake is properly configured before trying to initialize
        try:
            snowflake_config = settings.snowflake
        except ValidationError as ve:
            snowflake_error = (
                "Snowflake is not properly configured. "
                "Please set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD in settings."
            )
            logger.warning(f"Snowflake semantic view discovery failed (non-fatal): {snowflake_error}")
            snowflake_config = None
        
        if snowflake_config is not None:
            extractor = SnowflakeExtractor(snowflake_config)
            views = extractor.discover_semantic_views()
            snowflake_items = [
                SemanticModelItem(
                    id=v["name"],
                    name=v["name"],
                    type="semantic_view",
                    platform="snowflake",
                    description=v.get("comment", ""),
                    extra={"schema": v.get("schema"), "database": v.get("database")},
                )
                for v in views
            ]
    except HTTPException:
        raise
    except Exception as exc:
        snowflake_error = str(exc)
        logger.warning(f"Snowflake semantic view discovery failed (non-fatal): {exc}")

    # --- Cross-platform mappings ---
    mappings: list = []
    try:
        from semabridge.sync.repository import SyncRepository
        repo = SyncRepository()
        all_mappings = repo.list_mappings()
        sf_view_names = {i.name for i in snowflake_items}
        fabric_name_map = {i.name: i for i in fabric_items}

        for mapping in all_mappings:
            if mapping.source_type not in ("fabric",) and mapping.target_type not in (
                "snowflake_semantic_view",
                "fabric",
            ):
                continue
            sf_view = mapping.snowflake_semantic_view or mapping.target_identifier
            fabric_name = (
                mapping.model_name
                if mapping.source_type == "fabric"
                else mapping.model_name
            )
            mappings.append(
                SemanticMappingItem(
                    fabric_name=fabric_name,
                    fabric_id=mapping.fabric_model_id or mapping.source_identifier,
                    snowflake_view=sf_view if sf_view in sf_view_names else sf_view,
                    last_synced=mapping.last_synced_at,
                    in_sync=bool(mapping.last_osi_hash),
                    osi_hash=mapping.last_osi_hash,
                )
            )
    except Exception as exc:
        logger.warning(f"Mapping enrichment failed (non-fatal): {exc}")

    return SemanticDiscoveryResponse(
        fabric=fabric_items,
        snowflake=snowflake_items,
        mappings=mappings,
        fabric_error=fabric_error,
        snowflake_error=snowflake_error,
    )

async def semantic_sync(request_body: SemanticSyncRequest):
    """
    Trigger a semantic model synchronization between Fabric and Snowflake.

    Accepted ``direction`` values:
    - ``fabric_to_snowflake`` — push Fabric semantic models as Snowflake semantic views
    - ``snowflake_to_fabric`` — push Snowflake semantic views to Fabric as semantic models
    - ``fabric_snowflake_bidirectional`` — sync in both directions

    The sync runs in **background** via the existing ``SyncOrchestrator``
    pipeline (Extract → OSI → Convert → Deploy).
    """
    from semabridge.api.semantic_models import SemanticSyncRequest, SemanticSyncResponse
    from semabridge.sync.models import SyncConfig, SyncDirection
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

    # Validate direction is a Fabric↔Snowflake one
    allowed = {
        SyncDirection.FABRIC_TO_SNOWFLAKE,
        SyncDirection.SNOWFLAKE_TO_FABRIC,
        SyncDirection.FABRIC_SNOWFLAKE_BIDIRECTIONAL,
    }
    if request_body.direction not in allowed:
        raise HTTPException(
            status_code=422,
            detail=(
                f"direction '{request_body.direction.value}' is not a semantic sync direction. "
                f"Use one of: {[d.value for d in allowed]}"
            ),
        )

    config = SyncConfig(
        direction=request_body.direction,
        conflict_resolution=request_body.conflict_resolution,
        fabric_workspace_id=request_body.fabric_workspace_id,
        snowflake_schema=request_body.snowflake_schema,
        fabric_model_names=request_body.fabric_model_names,
        snowflake_semantic_views=request_body.snowflake_semantic_views,
        max_workers=request_body.max_workers,
        incremental=request_body.incremental,
    )

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repo)
    job = orchestrator.run(config, initiated_by="api")

    return SemanticSyncResponse(
        job_id=job.job_id,
        direction=job.direction.value,
        status=job.status.value,
        total_items=job.total_items,
        message=(
            f"Semantic sync job started: {job.total_items} item(s) "
            f"queued for direction '{job.direction.value}'"
        ),
    )

async def semantic_refresh(request_body: SemanticRefreshRequest):
    """
    Refresh semantic metadata from both Fabric and Snowflake **without deploying**.

    Fetches the latest OSI representation from each platform, stores a new
    snapshot in the repository, and returns a diff summary showing what has
    changed since the last refresh.

    This is a read-only operation — nothing is written to Fabric or Snowflake.
    """
    from semabridge.api.semantic_models import (
        SemanticRefreshRequest,
        SemanticRefreshResponse,
        SemanticRefreshSummary,
    )
    from semabridge.repository.semantic_diff_engine import SemanticDiffEngine

    response = SemanticRefreshResponse()
    diff_engine = SemanticDiffEngine()

    # --- Fabric ---
    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
        from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager

        settings = get_settings()
        extractor = FabricExtractor(settings.fabric)
        converter = TMSLToOSIConverter()
        snapshot_mgr = SemanticSnapshotManager()

        models = extractor.list_semantic_models()
        if request_body.fabric_model_names:
            lower_filter = {n.lower() for n in request_body.fabric_model_names}
            models = [m for m in models if m.get("displayName", "").lower() in lower_filter]

        for model_meta in models:
            try:
                tmsl = extractor.get_model_definition(model_meta["id"])
                osi = converter.to_osi(
                    {
                        "tmsl": tmsl,
                        "workspace_id": settings.fabric.workspace_id,
                        "dataset_id": model_meta.get("displayName", model_meta["id"]),
                    }
                )
                prev_snapshot = snapshot_mgr.get_latest_snapshot(osi.unique_name)
                if prev_snapshot:
                    diff = diff_engine.compare_states(
                        prev_snapshot.semantic_entities or {},
                        osi.model_dump(),
                    )
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name,
                        platform="fabric",
                        added_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "added"
                        ],
                        removed_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "removed"
                        ],
                        added_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "added"
                        ],
                        removed_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "removed"
                        ],
                        added_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "added"
                        ],
                        removed_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "removed"
                        ],
                        osi_hash=prev_snapshot.schema_hash,
                    )
                else:
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name, platform="fabric"
                    )
                snapshot_mgr.create_snapshot(osi.unique_name, osi.model_dump())
                response.summaries.append(summary)
                response.refreshed_fabric += 1
            except Exception as exc:
                response.errors.append(
                    f"Fabric '{model_meta.get('displayName', '')}': {exc}"
                )
    except Exception as exc:
        response.errors.append(f"Fabric refresh setup failed: {exc}")

    # --- Snowflake ---
    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
        from semabridge.repository.semantic_snapshot_manager import SemanticSnapshotManager

        settings = get_settings()
        extractor = SnowflakeExtractor(settings.snowflake)
        converter = SemanticViewToOSIConverter()
        snapshot_mgr = SemanticSnapshotManager()

        views = extractor.discover_semantic_views()
        if request_body.snowflake_semantic_views:
            lower_filter = {v.lower() for v in request_body.snowflake_semantic_views}
            views = [v for v in views if v["name"].lower() in lower_filter]

        for view_meta in views:
            try:
                ddl = extractor.extract_semantic_view_ddl(view_meta["name"])
                osi = converter.to_osi(
                    {"ddl": ddl, "view_name": view_meta["name"]}
                )
                prev_snapshot = snapshot_mgr.get_latest_snapshot(osi.unique_name)
                if prev_snapshot:
                    diff = diff_engine.compare_states(
                        prev_snapshot.semantic_entities or {},
                        osi.model_dump(),
                    )
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name,
                        platform="snowflake",
                        added_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "added"
                        ],
                        removed_datasets=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "dataset" and c.change_type.value == "removed"
                        ],
                        added_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "added"
                        ],
                        removed_metrics=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "measure" and c.change_type.value == "removed"
                        ],
                        added_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "added"
                        ],
                        removed_relationships=[
                            c.entity_name for c in diff.changes
                            if c.entity_type.value == "relationship" and c.change_type.value == "removed"
                        ],
                        osi_hash=prev_snapshot.schema_hash,
                    )
                else:
                    summary = SemanticRefreshSummary(
                        model_name=osi.unique_name, platform="snowflake"
                    )
                snapshot_mgr.create_snapshot(osi.unique_name, osi.model_dump())
                response.summaries.append(summary)
                response.refreshed_snowflake += 1
            except Exception as exc:
                response.errors.append(f"Snowflake view '{view_meta['name']}': {exc}")
    except Exception as exc:
        response.errors.append(f"Snowflake refresh setup failed: {exc}")

    return response

async def get_model(model_id: str):
    """
    Read a single model from the repository (latest version).
    Uses SQLAlchemy ORM so it works with any configured backend
    (DuckDB, PostgreSQL, SQLite, etc.).
    """
    from semabridge.repository.orm.models import ModelVersion
    from sqlalchemy import select

    session = db_manager._session()
    try:
        row = session.execute(
            select(ModelVersion)
            .where(ModelVersion.model_id == model_id)
            .order_by(ModelVersion.created_at.desc())
            .limit(1)
        ).scalars().first()

        if not row:
            raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found in repository")

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

async def save_model(model_id: str, payload: Dict[str, Any]):
    """
    Save a new immutable version of the model directly to DuckDB.
    """
    content = payload.get("content", "")
    author = payload.get("author", "ui")
    message = payload.get("message", "Saved from UI")
    version_tag = payload.get("version_tag", None)

    if not content.strip():
        raise HTTPException(status_code=400, detail="content is required")

    # Parse content for DuckDB snapshot
    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        parsed = {"raw": content}

    # Auto-extract version_tag from model YAML if not provided explicitly
    if not version_tag and isinstance(parsed, dict):
        version_tag = parsed.get("version_tag") or parsed.get("version") or None

    # Resolve workspace
    ws_id = "local"
    try:
        ws_id = settings.fabric.workspace_id or "local"
    except Exception:
        pass

    # Create version in DuckDB
    version_id = db_manager.insert_model_version(
        model_id=model_id,
        workspace_id=ws_id,
        snapshot=parsed,
        author=author,
        change_summary=message,
        version_tag=version_tag,
    )

    # Update hash tracker so next discovery won't double-snapshot
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    _last_snapshot_hash[model_id] = content_hash

    tag_str = f" tag={version_tag}" if version_tag else ""
    logger.info(f"Versioned {model_id} â†’ {version_id[:8]}â€¦{tag_str} by {author}")

    return {
        "status": "saved",
        "model_id": model_id,
        "version_id": version_id,
        "version_tag": version_tag,
        "filename": f"{model_id}.yaml",
        "path": f"duckdb://{model_id}",
    }

async def get_config():
    try:
        from semabridge.core.config_loader import get_default_config_path
        config_path = get_default_config_path() or Path("config/semabridge.yaml")

        if not config_path.exists():
            raise HTTPException(
                status_code=404,
                detail="semabridge.yaml not found in project"
            )

        content = config_path.read_text(encoding="utf-8")

        return {"content": content}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def _global_config_path() -> Path:
    """Return the path to the global config file."""
    return Path.home() / ".semabridge" / "config.yaml"

async def get_global_config():
    """Read the global config file as raw YAML text."""
    config_path = _global_config_path()
    if not config_path.exists():
        # Return a sensible default template
        default_content = """\
# SemaBridge Global Configuration
# Located at: ~/.semabridge/config.yaml

fabric:
  tenant_id: ""
  client_id: ""
  # client_secret_env: FABRIC_CLIENT_SECRET

snowflake:
  account: ""
  database: ""
  schema: "PUBLIC"
  warehouse: ""
  role: ""
  # password_env: SNOWFLAKE_PASSWORD

logging:
  level: INFO
  file: ~/.semabridge/semabridge.log

defaults:
  source_type: fabric
  target_type: snowflake
"""
        return {"content": default_content, "path": str(config_path), "exists": False}

    try:
        content = config_path.read_text(encoding="utf-8")
        return {"content": content, "path": str(config_path), "exists": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read global config: {e}")

async def save_global_config(payload: Dict[str, Any]):
    """Validate and save the global config YAML."""
    content = payload.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Config content cannot be empty")

    # Validate YAML syntax
    try:
        parsed = yaml.safe_load(content)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Config must be a YAML mapping (dict)")
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML syntax: {e}")

    # Ensure no inline secrets
    errors = []
    _check_inline_secrets(parsed, "", errors)
    if errors:
        raise HTTPException(status_code=400, detail=f"Security violation: {'; '.join(errors)}")

    config_path = _global_config_path()
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content, encoding="utf-8")
        return {"status": "saved", "path": str(config_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save global config: {e}")

def _check_inline_secrets(data: Any, path: str, errors: list) -> None:
    """Recursively check that no inline secrets appear in config values."""
    secret_keys = {"password", "secret", "token", "api_key", "private_key"}
    if isinstance(data, dict):
        for key, value in data.items():
            full_path = f"{path}.{key}" if path else key
            key_lower = key.lower()
            # Allow keys ending in _env (they reference env vars, not inline secrets)
            if any(s in key_lower for s in secret_keys) and not key_lower.endswith("_env"):
                if isinstance(value, str) and value.strip():
                    errors.append(f"Inline secret at '{full_path}' â€” use '{key}_env' suffix to reference an environment variable instead")
            _check_inline_secrets(value, full_path, errors)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _check_inline_secrets(item, f"{path}[{i}]", errors)

async def generate_config(payload: Dict[str, Any]):
    """
    Generate semabridge.yaml configuration based on selected models.
    Output intentionally follows the same schema used by the project config UI.
    """
    try:
        settings = get_settings()
        selected_models: List[str] = payload.get("models", [])
        model_ids: List[str] = payload.get("modelIds", [])
        source_type: str = payload.get("sourceType", "fabric")
        target_type: str = payload.get("targetType", "snowflake")
        pbix_folder: str = str(payload.get("pbixFolder", "") or "").strip()

        # If modelIds were not provided explicitly, infer UUID-like values from model list.
        if not model_ids and selected_models:
            uuid_like = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
            model_ids = [m for m in selected_models if isinstance(m, str) and uuid_like.match(m.strip())]

        project_name = str(payload.get("projectName") or settings.model.name or "semabridge-project").strip()
        workspace_name = str(payload.get("workspaceName") or "SemaBridge Workspace").strip()

        source_cfg: Dict[str, Any] = {
            "type": source_type,
        }

        if source_type == "fabric":
            source_cfg["workspace_id"] = settings.fabric.workspace_id or ""
            source_cfg["workspace"] = workspace_name
        elif source_type == "snowflake":
            source_cfg["database"] = settings.snowflake.database or ""
            source_cfg["schema"] = settings.snowflake.schema_name or "PUBLIC"
        elif source_type == "pbix":
            local_models_path = pbix_folder or str(_resolve_models_path())
            source_cfg["pbix_folder"] = local_models_path.replace("\\", "/")

        if selected_models:
            source_cfg["models"] = selected_models
        else:
            source_cfg["model"] = "*"

        config_doc: Dict[str, Any] = {
            "project_name": project_name,
            "source": source_cfg,
            "target": {
                "type": target_type,
            },
            "ui": {
                "output_format": "osi",
                "editor_mode": "yaml",
            },
            "options": {
                "auto_relationships": True,
                "include_hidden_fields": True,
                "generate_descriptions": True,
            },
        }

        if model_ids:
            config_doc["selection"] = {"model_ids": model_ids}

        yaml_content = yaml.safe_dump(
            config_doc,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )

        return {"content": yaml_content}

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
                "line": getattr(e, "problem_mark", None).line + 1
                if hasattr(e, "problem_mark") else 0,
                "message": str(e)
            }]
        }

    # Required sections
    required_sections = ["source", "target"]

    for section in required_sections:
        if section not in parsed:
            errors.append({
                "line": 0,
                "message": f"Missing required section: '{section}'"
            })

    # â”€â”€ Warnings for missing tables â”€â”€
    warnings = []
    source = parsed.get("source", {}) if parsed else {}
    if source.get("type") == "snowflake" or source.get("repository_path"):
        model_names = source.get("models", [])
        try:
            for mname in (model_names or []):
                models_path = _resolve_models_path()
                candidate = models_path / f"{mname}.yaml"
                if candidate.exists():
                    mcfg = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                    for ds in mcfg.get("datasets", []):
                        tbl = ds.get("source_table") or ds.get("table", "")
                        if tbl:
                            # Quick check â€” cannot verify Snowflake tables without connection,
                            # but we can flag if the YAML references tables not defined locally
                            pass
        except Exception:
            pass

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }

async def get_history():

    try:
        project_id = settings.model.name
        snapshots = db_manager.list_snapshots(project_id, limit=20)

        return [
            {
                "version_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "description": s.version_tag or "Automated Sync",
                "status": s.status
            }
            for s in snapshots
        ]

    except Exception as e:
        logger.exception("History fetch failed")
        raise HTTPException(status_code=500, detail=str(e))

async def sync_models(payload: Dict[str, Any]):
    """
    Trigger a full synchronization based on the current configuration.
    """
    try:
        return execute_sync_request(payload, _normalize_yaml_windows_path_fields)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sync execution failed")
        raise HTTPException(status_code=500, detail=str(e))

async def validate_live(
    payload: Dict[str, Any] = None,
    db: "Session" = Depends(get_db),
):
    """
    Run live-validation on the current model state.

    Returns both errors and warnings (e.g. missing source tables).
    This endpoint is called periodically by the frontend.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    try:
        from sqlalchemy import text

        rows = db.execute(text("""
                WITH RankedVersions AS (
                    SELECT model_id, snapshot, created_at,
                           ROW_NUMBER() OVER(PARTITION BY model_id ORDER BY created_at DESC) as rn
                    FROM model_versions
                )
                SELECT model_id, snapshot
                FROM RankedVersions
                WHERE rn = 1
            """)).fetchall()

        for row in rows:
            model_id = row[0]
            snapshot_data = row[1]
            
            if isinstance(snapshot_data, str):
                try:
                    data = json.loads(snapshot_data)
                except Exception as e:
                    errors.append({
                        "model": model_id,
                        "severity": "error",
                        "message": f"Failed to parse JSON: {str(e)}",
                    })
                    continue
            else:
                data = snapshot_data or {}

            if not isinstance(data, dict):
                continue

            if not any(
                k in data
                for k in (
                    "datasets",
                    "metrics",
                    "measures",
                    "model_name",
                    "name",
                    "unique_name",
                    "label",
                )
            ):
                continue

            display_model_name = (
                str(data.get("model_name") or "").strip()
                or str(data.get("name") or "").strip()
                or str(data.get("label") or "").strip()
                or str(data.get("unique_name") or "").strip()
                or model_id
            )

            # Check datasets for missing source references
            for ds in data.get("datasets", []):
                tbl = ds.get("source_table") or ds.get("table", "")
                cols = ds.get("columns", [])
                ds_name = ds.get("name") or ds.get("unique_name") or "?"
                if not tbl:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "warning",
                        "message": f"Dataset '{ds_name}' has no source_table defined",
                    })
                if not cols:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "warning",
                        "message": f"Dataset '{ds_name}' has no columns defined",
                    })

            # Check relationships for dangling references
            datasets_names = {
                (ds.get("name") or ds.get("unique_name") or "").upper()
                for ds in data.get("datasets", [])
                if (ds.get("name") or ds.get("unique_name"))
            }
            for rel in data.get("relationships", []):
                from_m = (rel.get("from_model") or rel.get("from_table") or "").upper()
                to_m = (rel.get("to_model") or rel.get("to_table") or "").upper()
                if from_m and from_m not in datasets_names:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "error",
                        "message": f"Relationship references unknown dataset '{from_m}'",
                    })
                if to_m and to_m not in datasets_names:
                    warnings.append({
                        "model": display_model_name,
                        "severity": "error",
                        "message": f"Relationship references unknown dataset '{to_m}'",
                    })

            # Schema checks
            if not any(
                str(data.get(k) or "").strip()
                for k in ("model_name", "name", "unique_name", "label")
            ):
                errors.append({
                    "model": display_model_name,
                    "severity": "error",
                    "message": "Model missing identity field (expected one of: model_name, name, unique_name, label)",
                })
    except Exception as e:
        errors.append({"model": "system", "severity": "error", "message": str(e)})

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "total_issues": len(errors) + len(warnings),
    }

async def discover_multi_workspace(payload: Dict[str, Any]):
    """Discover semantic models across multiple Fabric workspaces concurrently.

    Request body:
        {"workspace_ids": ["ws-id-1", "ws-id-2", ...]}

    Returns:
        Dictionary mapping workspace_id â†’ list of discovered items.
    """
    from semabridge.connectors.multi_workspace_orchestrator import MultiWorkspaceOrchestrator

    workspace_ids = payload.get("workspace_ids", [])
    if not workspace_ids:
        raise HTTPException(status_code=400, detail="workspace_ids array is required")

    try:
        orchestrator = MultiWorkspaceOrchestrator(settings.fabric)
        results = orchestrator.discover_all_workspaces(workspace_ids=workspace_ids)

        summary = {
            ws_id: {"count": len(items), "items": items}
            for ws_id, items in results.items()
        }
        total = sum(len(items) for items in results.values())

        return {
            "status": "success",
            "workspaces_scanned": len(workspace_ids),
            "total_items_discovered": total,
            "results": summary,
        }
    except Exception as e:
        logger.exception(f"Multi-workspace discovery failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


