from semabridge.api.services.core_shared import *


async def discover_semantic():
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

    try:
        from semabridge.connectors.fabric_extractor import FabricExtractor
        settings = get_settings()
        try:
            fabric_config = settings.fabric
        except ValidationError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Fabric is not properly configured. "
                    "Please set FABRIC_TENANT_ID, FABRIC_CLIENT_ID, and FABRIC_WORKSPACE_ID in settings."
                ),
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
        logger.warning("Fabric semantic discovery failed (non-fatal): %s", exc)

    try:
        from semabridge.connectors.snowflake_extractor import SnowflakeExtractor
        settings = get_settings()
        try:
            snowflake_config = settings.snowflake
        except ValidationError:
            snowflake_error = (
                "Snowflake is not properly configured. "
                "Please set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD in settings."
            )
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
        logger.warning("Snowflake semantic view discovery failed (non-fatal): %s", exc)

    mappings: list = []
    try:
        from semabridge.sync.repository import SyncRepository
        repo = SyncRepository()
        all_mappings = repo.list_mappings()
        sf_view_names = {i.name for i in snowflake_items}
        for mapping in all_mappings:
            if mapping.source_type not in ("fabric",) and mapping.target_type not in ("snowflake_semantic_view", "fabric"):
                continue
            sf_view = mapping.snowflake_semantic_view or mapping.target_identifier
            fabric_name = mapping.model_name
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
        logger.warning("Mapping enrichment failed (non-fatal): %s", exc)

    return SemanticDiscoveryResponse(
        fabric=fabric_items,
        snowflake=snowflake_items,
        mappings=mappings,
        fabric_error=fabric_error,
        snowflake_error=snowflake_error,
    )


async def semantic_sync(request_body: SemanticSyncRequest):
    from semabridge.api.semantic_models import SemanticSyncResponse
    from semabridge.sync.models import SyncConfig, SyncDirection
    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository

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
        message=f"Semantic sync job started: {job.total_items} item(s) queued for direction '{job.direction.value}'",
    )


async def semantic_refresh(request_body: SemanticRefreshRequest):
    from semabridge.api.semantic_models import SemanticRefreshResponse

    # Keep the existing behavior surface while this logic is still being
    # carved down further from the old monolith.
    response = SemanticRefreshResponse()
    return response
