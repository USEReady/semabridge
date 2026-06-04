import os
import uuid
import yaml
import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class AutoMapRequest(BaseModel):
    project_id: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    targets: Optional[List[Any]] = None
    user_id: Optional[str] = None

    class Config:
        extra = "allow"
from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from semabridge.api.services.mappings_service import (
    MappingService,
    get_mapping_service,
    list_mappings_compat,
    delete_mappings_compat,
)
from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
    validate_project_connector_accounts_belong_to_user,
)

# --- Request Models ---

class DryRunRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    selected_sources: List[str]

class UpdateMappingRequest(BaseModel):
    target_name: Optional[str] = None
    target_data_type: Optional[str] = None
    status: Optional[str] = "manual"

class AutoMapRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    selected_sources: List[str]

class DeployRequest(BaseModel):
    field_mappings: List[Dict[str, Any]]

# --- Router ---

router = APIRouter()
logger = logging.getLogger(__name__)

def _assert_project_access(project_id: str, user_id: str | None) -> None:
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id, log_prefix="ProjectMappingAuth"):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


@router.get('/api/mappings')
async def list_mappings(request: Request, project_id: Optional[str] = None):
    user_id = require_request_user_id(request)
    pid = str(project_id or "").strip()
    if auth_is_enabled():
        if not pid:
            raise HTTPException(status_code=400, detail="project_id is required")
        _assert_project_access(pid, user_id)
    return await list_mappings_compat(pid or None)


@router.delete('/api/mappings')
async def delete_mappings(request: Request, project_id: Optional[str] = None):
    user_id = require_request_user_id(request)
    pid = str(project_id or "").strip()
    if auth_is_enabled():
        if not pid:
            raise HTTPException(status_code=400, detail="project_id is required")
        _assert_project_access(pid, user_id)
    return await delete_mappings_compat(pid or None)


def _build_config_yaml_from_request(
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
    selected_sources: List[str],
    project_name: str = "dry-run-preview",
) -> str:
    """
    Build a minimal semabridge config YAML from the wizard's source/target config
    so the real sync pipeline can run extraction + OSI + SML without deployment.
    """
    source_type = str(source_config.get("type") or "fabric").strip().lower()
    target_type = str(target_config.get("type") or "snowflake").strip().lower()

    source_section: Dict[str, Any] = {"type": source_type}
    if source_config.get("workspace_id"):
        source_section["workspace_id"] = source_config["workspace_id"]
    if source_config.get("identity_id"):
        source_section["identity_id"] = source_config["identity_id"]
    if source_config.get("database"):
        source_section["database"] = source_config["database"]
    if source_config.get("schema"):
        source_section["schema"] = source_config["schema"]
    if selected_sources:
        source_section["models"] = selected_sources

    target_section: Dict[str, Any] = {"type": target_type}
    if target_config.get("database"):
        target_section["database"] = target_config["database"]
    if target_config.get("schema"):
        target_section["schema"] = target_config["schema"]
    if target_config.get("account"):
        target_section["account"] = target_config["account"]
    if target_config.get("warehouse"):
        target_section["warehouse"] = target_config["warehouse"]
    if target_config.get("identity_id"):
        target_section["identity_id"] = target_config["identity_id"]
    if target_config.get("workspace_id"):
        target_section["workspace_id"] = target_config["workspace_id"]

    config = {
        "project_name": project_name,
        "source": source_section,
        "targets": [target_section],
        "options": {
            "auto_relationships": False,
            "generate_descriptions": False,
        },
    }
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=False)


@router.post("/api/projects/{project_id}/dry-run")
async def dry_run_mapping(
    project_id: str,
    http_request: Request,
    request: DryRunRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Execute a dry run by running the full semantic pipeline (extraction → OSI → SML)
    WITHOUT deployment, then return the real field-level mappings (columns + measures).
    """
    from semabridge.api.services.core_domain_service import sync_models
    from semabridge.api.services.mapping_service import (
        _compat_build_project_entity_mappings,
        _compat_preferred_snapshot_id_from_sync_result,
        _compat_serialize_auto_map_entity_mappings,
    )
    import semabridge.api.services.project_shared as project_shared
    from semabridge.api.services.project_mapping_engine import sanitize_identifier

    try:
        request_user_id = require_request_user_id(http_request)
        validate_project_connector_accounts_belong_to_user(
            request_user_id,
            {
                "source": request.source_config,
                "target": request.target_config,
                "targets": [request.target_config],
            },
        )
        if project_id not in ("preview", ""):
            _assert_project_access(project_id, request_user_id)
        source_account_id = str(
            request.source_config.get("identity_id")
            or request.source_config.get("account_id")
            or ""
        ).strip() or None
        target_account_id = str(
            request.target_config.get("identity_id")
            or request.target_config.get("account_id")
            or ""
        ).strip() or None
        # ── 1. Resolve or create a stable preview project in the compat store ──
        # Use a deterministic project id based on source+models so repeated dry
        # runs for the same wizard session reuse the same project slot.
        user_seed = str(request_user_id or "").strip()
        seed = (
            f"{user_seed}-"
            f"{request.source_config.get('type','')}-"
            f"{request.source_config.get('workspace_id','')}-"
            f"{'|'.join(sorted(request.selected_sources))}"
        )
        preview_project_id = f"preview-{uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex[:12]}"

        project_shared._compat_ensure_loaded()
        preview_project = project_shared._compat_projects.get(preview_project_id)
        if not isinstance(preview_project, dict):
            preview_project = {
                "id": preview_project_id,
                "project_id": preview_project_id,
                "created_at": project_shared._compat_now_iso(),
            }
        preview_project.update({
            "name": "dry-run-preview",
            "updated_at": project_shared._compat_now_iso(),
            "status": "preview",
            "is_transient_preview": True,
            "preview_kind": "dry-run",
            "owner_user_id": str(request_user_id).strip() if request_user_id is not None else None,
            "user_id": str(request_user_id).strip() if request_user_id is not None else None,
            "account_id": source_account_id or target_account_id,
            "source_account_id": source_account_id,
            "target_account_id": target_account_id,
            "source": dict(request.source_config),
            "target": dict(request.target_config),
            "targets": [dict(request.target_config)],
        })
        project_shared._compat_projects[preview_project_id] = preview_project
        project_shared._compat_save_store()
        logger.info(
            "[PreviewProject] prepared preview_project_id=%s user_id=%s source_account_id=%s target_account_id=%s selected_sources=%s",
            preview_project_id,
            request_user_id,
            source_account_id,
            target_account_id,
            request.selected_sources,
        )

        # ── 2. Build a real config YAML from the wizard's source/target config ─
        config_yaml = _build_config_yaml_from_request(
            source_config=request.source_config,
            target_config=request.target_config,
            selected_sources=request.selected_sources,
            project_name="dry-run-preview",
        )
        project_shared._compat_project_configs[preview_project_id] = config_yaml

        # ── 3. Run the real pipeline with dry_run=True (no deployment) ──────────
        #    This runs: connector extraction → OSI conversion → SML generation
        #    and stores the resulting SML snapshot so entity mappings can be built.
        sync_result = await sync_models({
            "project_id": preview_project_id,
            "content": config_yaml,
            "dry_run": True,
        })

        preferred_snapshot_id = _compat_preferred_snapshot_id_from_sync_result(
            sync_result, request.selected_sources
        )

        # Debug: log what we got from the sync result
        print(f"[DryRun] sync_result status: {sync_result.get('status')}")
        print(f"[DryRun] preferred_snapshot_id: {preferred_snapshot_id!r}")
        results = sync_result.get("results") or []
        for r in results:
            summary = r.get("summary") or {}
            print(f"[DryRun] model={r.get('model')!r} sml_snapshot_id={summary.get('sml_snapshot_id')!r}")

        # ── 4. Build entity mappings from the real SML snapshot ──────────────────
        target_connector = str(request.target_config.get("type") or "snowflake").strip().lower()

        # Read the SML blob directly from the snapshot by snapshot_id.
        # We cannot use _compat_build_project_entity_mappings because the snapshot
        # is stored under the Fabric dataset_id (e.g. "d32e8900-..."), not our
        # preview project ID — so the project_id lookup would return an empty model.
        from semabridge.api.services.project_mapping_engine import build_entity_mappings
        from semabridge.api.services.project_shared import db_manager as _db_manager

        sml_blob: Dict[str, Any] = {}

        # Try preferred_snapshot_id first (most reliable)
        if preferred_snapshot_id:
            try:
                snap = _db_manager.get_snapshot(preferred_snapshot_id)
                if snap and isinstance(getattr(snap, "sml_blob", None), dict):
                    sml_blob = snap.sml_blob
                    print(f"[DryRun] Loaded SML blob from preferred_snapshot_id={preferred_snapshot_id[:12]}")
                    print(f"[DryRun] SML unique_name={sml_blob.get('unique_name')!r}")
                    print(f"[DryRun] datasets={[d.get('unique_name') for d in sml_blob.get('datasets', [])]}")
                    print(f"[DryRun] metrics={[m.get('unique_name') for m in sml_blob.get('metrics', [])]}")
            except Exception as snap_err:
                print(f"[DryRun] Failed to load preferred snapshot: {snap_err}")

        # Fallback: try each result's snapshot
        if not sml_blob:
            for result_row in (sync_result.get("results") or []):
                sid = str((result_row.get("summary") or {}).get("sml_snapshot_id") or "").strip()
                if not sid:
                    continue
                try:
                    snap = _db_manager.get_snapshot(sid)
                    if snap and isinstance(getattr(snap, "sml_blob", None), dict):
                        sml_blob = snap.sml_blob
                        print(f"[DryRun] Loaded SML blob from fallback snapshot={sid[:12]}")
                        break
                except Exception:
                    continue

        extraction_failed = not sml_blob
        if extraction_failed:
            sync_status = str((sync_result or {}).get("status") or "unknown")
            logger.warning("[DryRun] No SML blob found for project=%s sync_status=%s — extraction may have failed", preview_project_id, sync_status)

        # Scope to selected sources if specified
        if sml_blob and request.selected_sources:
            from semabridge.api.services.project_runs_impl import _compat_scope_model_for_dry_run
            sml_blob = _compat_scope_model_for_dry_run(sml_blob, request.selected_sources)

        built = build_entity_mappings(
            project_id=preview_project_id,
            model=sml_blob or {"unique_name": "preview", "datasets": [], "metrics": []},
            existing_mappings={},
            session_key=f"{preview_project_id}-mapping-session",
            target_connector=target_connector,
        )
        data = {
            "project_id": preview_project_id,
            "mappings": built.get("mappings", []),
            "source_fields": built.get("source_fields", []),
            "target_fields": built.get("target_fields", []),
            "collisions": built.get("collisions", []),
        }

        entity_mappings = _compat_serialize_auto_map_entity_mappings(
            data.get("mappings", []),
            target_connector=target_connector,
        )

        # ── 5. Filter to field-level only (columns + measures, no table rows) ────
        # entity_kind values from the mapping engine: "column", "metric" (measures), "table"
        # "metric" is the canonical kind for measures — include it alongside "column".
        FIELD_KINDS = {"field", "column", "measure", "metric"}
        filtered_mappings = []
        if request.selected_sources:
            fallback_model_name = request.selected_sources[0]
        else:
            fallback_model_name = "default"
        model_name = str((sml_blob or {}).get("unique_name") or (sml_blob or {}).get("label") or fallback_model_name)
        from semabridge.utils.synonyms import load_synonym_overrides, lookup_synonym_override
        synonym_overrides = load_synonym_overrides(preview_project_id)
        for m in entity_mappings:
            if not isinstance(m, dict):
                continue
            kind = str(m.get("entity_kind") or "").lower()
            if kind not in FIELD_KINDS:
                continue
            # Normalise "metric" → "measure" so the frontend field_type split works
            normalised_kind = "measure" if kind in ("metric", "measure") else kind
            source_name = str(m.get("source_name", "") or "").strip()
            source_table = str(m.get("source_table", m.get("source_entity", "")) or "").strip()
            measure_source_tables = list(m.get("measure_source_tables") or [])
            if normalised_kind == "measure" and not source_table and measure_source_tables:
                source_table = str(measure_source_tables[0] or "").strip()
            filtered_mappings.append({
                "id": m.get("id", f"field_{len(filtered_mappings)}"),
                "project_id": preview_project_id,
                "model_name": model_name,
                "entity_kind": normalised_kind,
                "source_name": source_name,
                "source_data_type": m.get("source_data_type", "unknown"),
                "source_table": source_table,
                "source_path": m.get("source_path", ""),
                "source_qualified_path": m.get("source_qualified_path", ""),
                "target_name": m.get("target_name", ""),
                "target_data_type": m.get("target_data_type", m.get("source_data_type", "unknown")),
                "mapping_status": m.get("status", "auto"),
                "status": m.get("status", "auto"),
                "suggested_target_name": m.get("suggested_target_name", ""),
                "collision_detected": bool(m.get("collision_detected")),
                "validation_status": m.get("validation_status", "valid"),
                "validation_code": m.get("validation_code", "OK"),
                "validation_message": m.get("validation_message", ""),
                "measure_source_tables": measure_source_tables,
                "source_expression": m.get("source_expression", ""),
                "synonym_overrides": lookup_synonym_override(
                    synonym_overrides,
                    [model_name],
                    source_table,
                    source_name,
                ),
            })

        # Apply collision handling on top of what the serializer already did
        filtered_mappings = service.add_collision_handling(filtered_mappings)

        auto_count = sum(1 for m in filtered_mappings if m.get("status") == "auto")
        unmapped_count = sum(1 for m in filtered_mappings if m.get("status") == "unmapped")
        collision_count = sum(1 for m in filtered_mappings if m.get("status") == "collision")

        return {
            "success": True,
            "project_id": preview_project_id,
            "model_name": model_name,
            "entity_mappings": filtered_mappings,
            "extraction_failed": extraction_failed,
            "summary": {
                "total_fields": len(filtered_mappings),
                "auto_mapped": auto_count,
                "unmapped": unmapped_count,
                "collisions": collision_count,
                "extraction_failed": extraction_failed,
            },
        }

    except Exception as e:
        print(f"[Dry Run Error] {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "entity_mappings": [],
                "summary": {"total_fields": 0, "auto_mapped": 0, "unmapped": 0, "collisions": 0},
            },
        )

@router.put("/api/projects/{project_id}/mappings/{mapping_id}")
async def update_mapping(
    project_id: str,
    mapping_id: str,
    http_request: Request,
    request: UpdateMappingRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Update a single field mapping (used when user edits target field).
    Sets status to "manual" automatically.
    """
    request_user_id = require_request_user_id(http_request)
    _assert_project_access(project_id, request_user_id)
    result = await service.update_mapping_compat(
        mapping_id=mapping_id,
        target_name=request.target_name or "",
        target_data_type=request.target_data_type,
        status="manual"
    )
    return {"success": True, "mapping": result}

@router.post("/api/projects/{project_id}/auto-map")
async def rerun_auto_map(
    project_id: str,
    http_request: Request,
    request: AutoMapRequest,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Re-run auto-mapping algorithm on demand.
    """
    request_user_id = require_request_user_id(http_request)
    validate_project_connector_accounts_belong_to_user(
        request_user_id,
        {
            "source": request.source_config,
            "target": request.target_config,
            "targets": [request.target_config],
        },
    )
    _assert_project_access(project_id, request_user_id)
    result = await service.auto_map_compat(
        source_config=request.source_config,
        target_config=request.target_config,
        selected_sources=request.selected_sources,
        dry_run=False
    )
    
    filtered_mappings = [
        m for m in result.get("entity_mappings", [])
        if m.get("entity_kind") == "field" or m.get("entity_kind") == "column" or m.get("entity_kind") == "measure"
    ]
    
    filtered_mappings = service.add_collision_handling(filtered_mappings)
    
    return {"entity_mappings": filtered_mappings}

@router.post("/api/projects/{project_id}/deploy")
async def deploy_mappings(
    project_id: str,
    http_request: Request,
    request: DeployRequest,
    background_tasks: BackgroundTasks,
    service: MappingService = Depends(get_mapping_service)
):
    """
    Deploy final mappings to target system by triggering the full semantic sync pipeline.
    Stores the user-edited field mappings first, then kicks off a SYNC run.
    For "preview" projects, creates the actual project first.
    """
    from semabridge.api.services.project_domain_service import (
        create_project_compat,
        run_project_now_compat,
    )
    from semabridge.api.services.project_mapping_engine import sanitize_identifier
    import semabridge.api.services.project_shared as project_shared
    request_user_id = require_request_user_id(http_request)

    # ── 1. Resolve / create the project ──────────────────────────────────────
    actual_project_id = project_id
    if project_id == "preview" or project_id.startswith("preview-"):
        preview_cfg = str(project_shared._compat_project_configs.get(project_id) or "").strip()
        new_project_payload = {
            "name": f"project-{uuid.uuid4().hex[:8]}",
            "source": {"type": "fabric"},
            "targets": [{"type": "snowflake"}],
            "preferred_interface": "ui",
            "user_id": request_user_id,
        }
        if preview_cfg:
            new_project_payload["config_yaml"] = preview_cfg
        created = await create_project_compat(new_project_payload)
        actual_project_id = str(created.get("id") or created.get("project_id") or "").strip()
        if not actual_project_id:
            raise HTTPException(status_code=500, detail="Failed to create actual project for deploy.")
    else:
        _assert_project_access(project_id, request_user_id)

    # ── 2. Persist the user-edited field mappings into the compat store ───────
    target_connector_type = ""
    try:
        cfg = str(project_shared._compat_project_configs.get(actual_project_id) or project_shared._compat_project_configs.get(project_id) or "")
        if cfg:
            parsed_cfg = yaml.safe_load(cfg) if isinstance(cfg, str) else {}
            targets = parsed_cfg.get("targets") if isinstance(parsed_cfg, dict) else []
            if isinstance(targets, list) and targets:
                target_connector_type = str((targets[0] or {}).get("type") or "").strip().lower()
    except Exception:
        target_connector_type = ""

    for idx, mapping in enumerate(request.field_mappings):
        if not isinstance(mapping, dict):
            continue
        source_path = str(mapping.get("source_path") or "").strip()
        source_name = str(mapping.get("source_name") or "").strip()
        target_name = str(mapping.get("target_name") or "").strip()
        if target_connector_type == "snowflake" and target_name:
            target_name = sanitize_identifier(target_name).upper()
        mapping_id = str(mapping.get("id") or "").strip()
        if not mapping_id:
            seed = source_path or source_name or f"row-{idx}"
            mapping_id = f"{actual_project_id}-{uuid.uuid5(uuid.NAMESPACE_URL, f'{actual_project_id}:{seed}').hex[:16]}"

        existing = project_shared._compat_mappings.get(mapping_id, {})
        if not isinstance(existing, dict):
            existing = {}

        merged = {**existing, **mapping}
        merged["id"] = mapping_id
        merged["project_id"] = actual_project_id
        merged["target_name"] = target_name
        merged["target_data_type"] = str(mapping.get("target_data_type") or "").strip()
        merged["updated_at"] = project_shared._compat_now_iso()
        if target_name:
            merged["status"] = "manual"
            merged["is_user_edited"] = True
        project_shared._compat_mappings[mapping_id] = merged
    project_shared._compat_save_store()

    # ── 3. Trigger the full semantic sync pipeline (SYNC run) ─────────────────
    try:
        run_result = await run_project_now_compat(
            actual_project_id,
            background_tasks,
            payload={
                "run_type": "SYNC",
                "field_mappings": request.field_mappings,
                "user_id": request_user_id,
            },
        )
        return {
            "success": True,
            "project_id": actual_project_id,
            "run_id": run_result.get("run_id"),
            "status": run_result.get("status", "running"),
            "deployed_count": len(request.field_mappings),
            "errors": [],
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "project_id": actual_project_id,
                "error": str(e),
                "deployed_count": 0,
                "errors": [str(e)],
            },
        )

# Keep old endpoint for backwards compatibility for now
@router.post('/api/mappings/auto')
async def auto_map_with_user_context(request: Request, payload: AutoMapRequest):
    from semabridge.api.services.project_domain_service import auto_map_compat
    body = payload.model_dump(exclude_none=False)
    user_id = require_request_user_id(request)
    if user_id:
        body["user_id"] = user_id
        validate_project_connector_accounts_belong_to_user(user_id, body)
    return await auto_map_compat(body)
