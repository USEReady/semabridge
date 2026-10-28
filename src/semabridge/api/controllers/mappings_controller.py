import os
import uuid
import yaml
import logging
from typing import Any, Dict, List, Optional, Tuple
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
from semabridge.api.services.pbix_source_validation import validate_pbix_model_list
from semabridge.domain.exceptions import ValidationError
from semabridge.connectors.snowflake_emitter import LIVE_SCHEMA_UNAVAILABLE
from semabridge.connectors.table_binding_resolver import AMBIGUOUS_TABLE_BINDING

# --- Request Models ---

class DryRunRequest(BaseModel):
    source_config: Dict[str, Any]
    target_config: Dict[str, Any]
    selected_sources: List[str]
    # Sync mode chosen in the wizard (copy/upsert); overrides the saved one.
    sync_mode: Optional[str] = None

class UpdateMappingRequest(BaseModel):
    target_name: Optional[str] = None
    target_data_type: Optional[str] = None
    status: Optional[str] = "manual"
    # Multi-model projects only: the model (its source.models entry) this
    # edit belongs to, plus the row's identity, so the edit is stored against
    # this project + model and exported as a model-scoped override. Omitted
    # by the single-model flow, whose behaviour is unchanged.
    model: Optional[str] = None
    source_path: Optional[str] = None
    source_name: Optional[str] = None
    entity_kind: Optional[str] = None

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


# Source types whose selected_sources entries are each an independent model
# (one .pbix file / one Fabric semantic model). A synchronous /dry-run call
# can only preview ONE of them -- N of them must go through the dry-run jobs
# endpoint, which runs one isolated pipeline per model in parallel.
_PER_MODEL_SOURCE_TYPES = ("pbix", "fabric")


def _is_preview_project_id(project_id: Optional[str]) -> bool:
    pid = str(project_id or "").strip()
    return pid in ("", "preview") or pid.startswith("preview-")


def _dry_run_preview_project_id(
    request_user_id: Any,
    source_config: Dict[str, Any],
    selected_sources: List[str],
) -> str:
    """Deterministic preview project id for one dry-run scope (user + source
    type + workspace + selected sources). A dry-run job file's preview id is
    this function applied to selected_sources=[that file's source_ref], so
    every model in a job gets its own isolated preview project."""
    user_seed = str(request_user_id or "").strip()
    seed = (
        f"{user_seed}-"
        f"{(source_config or {}).get('type','')}-"
        f"{(source_config or {}).get('workspace_id','')}-"
        f"{'|'.join(sorted(selected_sources or []))}"
    )
    return f"preview-{uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex[:12]}"


def _dry_run_source_label(
    source_type: str,
    selected_sources: List[str],
    source_config: Dict[str, Any],
    model_name: str = "",
) -> str:
    """UI section title for one dry-run scope: the PBIX file stem, or the
    Fabric model name."""
    from pathlib import PurePath

    first = str((selected_sources or [""])[0] or "").strip()
    stype = str(source_type or "").strip().lower()
    if stype == "pbix":
        candidate = first if first.lower().endswith(".pbix") else str((source_config or {}).get("pbix_path") or first or "")
        if candidate.strip():
            return PurePath(candidate.replace("\\", "/")).stem or candidate
    if first:
        return first
    return str(model_name or "")


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
    if source_config.get("pbix_path"):
        source_section["pbix_path"] = source_config["pbix_path"]
    if source_config.get("pbix_folder"):
        source_section["pbix_folder"] = source_config["pbix_folder"]
    if selected_sources:
        source_section["models"] = selected_sources

    target_section: Dict[str, Any] = {"type": target_type}
    if target_config.get("database"):
        target_section["database"] = target_config["database"]
    if target_config.get("schema"):
        target_section["schema"] = target_config["schema"]
    if target_config.get("source_data_schema"):
        target_section["source_data_schema"] = target_config["source_data_schema"]
    if target_config.get("account"):
        target_section["account"] = target_config["account"]
    if target_config.get("warehouse"):
        target_section["warehouse"] = target_config["warehouse"]
    if target_config.get("role"):
        target_section["role"] = target_config["role"]
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
    if target_config.get("source_data_schema"):
        config["options"]["source_data_schema"] = target_config["source_data_schema"]
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=False)


def _saved_project_target(project_cfg: Dict[str, Any], requested: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The saved project target matching the requested target type, or None
    when the project has no saved configuration (new project wizard)."""
    if not project_cfg:
        return None
    target_type = str((requested or {}).get("type") or "").strip().lower()
    candidates = [t for t in (project_cfg.get("targets") or []) if isinstance(t, dict)]
    if isinstance(project_cfg.get("target"), dict):
        candidates.append(project_cfg["target"])
    for target in candidates:
        if str(target.get("type") or "").strip().lower() == target_type:
            saved = {"type": target_type}
            for key in ("identity_id", "account", "database", "schema", "warehouse", "role", "workspace_id", "source_data_schema"):
                value = target.get(key) or (target.get("schema_name") if key == "schema" else None)
                if value:
                    saved[key] = value
            if "identity_id" not in saved and (requested or {}).get("identity_id"):
                saved["identity_id"] = requested["identity_id"]
            return saved
    return None


def _project_sync_settings(project_id: str) -> Tuple[str, Dict[str, Any]]:
    """(sync mode, parsed config) from the stored configuration of `project_id`."""
    import yaml

    import semabridge.api.services.project_shared as project_shared
    from semabridge.core.run_helpers import normalize_sync_mode, sync_mode_from_project_config

    try:
        bundle = project_shared._compat_load_modular_project(project_id) or {}
        config_yaml = bundle.get("config_yaml") or project_shared._compat_project_configs.get(project_id)
        project = project_shared._compat_projects.get(project_id) or {}
        parsed = yaml.safe_load(config_yaml) if isinstance(config_yaml, str) else {}
        mode = (
            sync_mode_from_project_config(parsed)
            or normalize_sync_mode(project.get("sync_mode"))
            or "copy"
        )
        return mode, parsed if isinstance(parsed, dict) else {}
    except Exception as exc:
        logger.warning("Could not resolve sync mode for project %s: %s", project_id, exc)
        return "copy", {}


async def _run_dry_run_pipeline(
    *,
    project_id: str,
    request_user_id,
    source_config: Dict[str, Any],
    target_config: Dict[str, Any],
    selected_sources: List[str],
    sync_mode_override: Optional[str] = None,
) -> Dict[str, Any]:
    """The actual dry-run pipeline: builds a preview project, runs the real
    extraction -> OSI -> SML pipeline with dry_run=True, builds entity
    mappings, runs the trial-DDL pass, and returns the response shape
    dry_run_mapping() has always returned.

    Extracted verbatim out of dry_run_mapping() (no behavior change — every
    line below is unchanged except request.xxx -> the equivalent parameter)
    so it can be invoked two ways:
      - Synchronously, in-request, by dry_run_mapping() itself — today's
        flow, unchanged, still fully synchronous for a single call.
      - Once per file, from a background job (see dry_run_job_service.py),
        for the multi-PBIX background-job-plus-polling flow that avoids an
        HTTP timeout on a parallel multi-file batch. Each background
        invocation passes selected_sources=[one_file_path] and its own
        project_id, so this function has no awareness of "multi-file" at
        all — every file's run is a fully independent call, matching the
        "no merging" requirement structurally rather than by convention.
    """
    from semabridge.api.services.core_domain_service import sync_models
    from semabridge.api.services.mapping_service import (
        _compat_build_project_entity_mappings,
        _compat_preferred_snapshot_id_from_sync_result,
        _compat_serialize_auto_map_entity_mappings,
    )
    import semabridge.api.services.project_shared as project_shared
    from semabridge.api.services.project_mapping_engine import sanitize_identifier

    source_account_id = str(
        source_config.get("identity_id")
        or source_config.get("account_id")
        or ""
    ).strip() or None
    target_account_id = str(
        target_config.get("identity_id")
        or target_config.get("account_id")
        or ""
    ).strip() or None
    # ── 1. Resolve or create a stable preview project in the compat store ──
    # Use a deterministic project id based on source+models so repeated dry
    # runs for the same wizard session reuse the same project slot.
    preview_project_id = _dry_run_preview_project_id(request_user_id, source_config, selected_sources)
    source_type = str(source_config.get("type") or "").strip().lower()

    project_shared._compat_ensure_loaded()
    # Up to MAX_PARALLEL_DRY_RUN_FILES of these pipelines run concurrently on
    # worker threads (dry_run_job_service) -- every compat-store mutation +
    # save goes through the shared store lock.
    with project_shared.compat_store_lock():
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
            "source": dict(source_config),
            "target": dict(target_config),
            "targets": [dict(target_config)],
        })
        project_shared._compat_projects[preview_project_id] = preview_project
        project_shared._compat_save_store()
    logger.info(
        "[PreviewProject] prepared preview_project_id=%s user_id=%s source_account_id=%s target_account_id=%s selected_sources=%s",
        preview_project_id,
        request_user_id,
        source_account_id,
        target_account_id,
        selected_sources,
    )

    # An existing project's dry run targets exactly what its real sync uses:
    # the saved target (account, database, schema, warehouse, role) — not
    # values the wizard filled in by default but never saved.
    sync_mode, project_cfg = _project_sync_settings(project_id)
    saved_target = _saved_project_target(project_cfg, target_config)
    if saved_target is not None:
        target_config = saved_target

    # ── 2. Build a real config YAML from the wizard's source/target config ─
    config_yaml = _build_config_yaml_from_request(
        source_config=source_config,
        target_config=target_config,
        selected_sources=selected_sources,
        project_name="dry-run-preview",
    )
    with project_shared.compat_store_lock():
        project_shared._compat_project_configs[preview_project_id] = config_yaml

    # ── 3. Run the real pipeline with dry_run=True (no deployment) ──────────
    #    This runs: connector extraction → OSI conversion → SML generation
    #    and stores the resulting SML snapshot so entity mappings can be built.
    #    A project configured for UPSERT also gets its delta against the real
    #    project's last deployed snapshot (the preview id has none).
    from semabridge.core.run_helpers import normalize_sync_mode
    sync_mode = normalize_sync_mode(sync_mode_override) or sync_mode
    delta_preview_overrides = None
    if sync_mode == "upsert":
        delta_preview_overrides = {
            "mappings_overrides": project_cfg.get("mappings_overrides") or [],
            "targets": project_cfg.get("targets") or ([project_cfg["target"]] if isinstance(project_cfg.get("target"), dict) else []),
        }
    sync_result = await sync_models({
        "project_id": preview_project_id,
        "content": config_yaml,
        "dry_run": True,
        "sync_mode": sync_mode,
        "delta_baseline_project_id": project_id if sync_mode == "upsert" else None,
        "delta_preview_overrides": delta_preview_overrides,
        # Table-binding preview reads the real project's saved bindings.
        "binding_project_id": project_id,
        # Pass account_id and user_id so credential scoping works for the
        # transient preview project (not in ORM, so sync_models can't resolve
        # account_id automatically via the DB lookup).
        "account_id": source_account_id or target_account_id,
        "user_id": str(request_user_id) if request_user_id is not None else None,
    })

    preferred_snapshot_id = _compat_preferred_snapshot_id_from_sync_result(
        sync_result, selected_sources
    )

    # Debug: log what we got from the sync result
    print(f"[DryRun] sync_result status: {sync_result.get('status')}")
    print(f"[DryRun] preferred_snapshot_id: {preferred_snapshot_id!r}")
    results = sync_result.get("results") or []
    # PBIX only: report-layer field references that don't match any
    # current column/measure (see SourceFormat.unresolved_report_field_references).
    # Table-scoped, not entity-scoped, so it's collected separately from
    # entity_mappings below rather than attached to any one row.
    unresolved_report_field_references: List[Dict[str, Any]] = []
    # PBIX only: report-layer aliases claimed by more than one field and
    # therefore excluded from synonyms (see SourceFormat.ambiguous_report_aliases).
    ambiguous_report_aliases: List[Dict[str, Any]] = []
    upsert_delta: Optional[Dict[str, Any]] = None
    table_binding_plan: Optional[Dict[str, Any]] = None
    dry_run_blockers: List[Dict[str, Any]] = []
    # Step 8 of the engine dry run built the target DDL against the live
    # schema; its drops/placeholders are what the sync would report.
    target_ddl_drops: List[Dict[str, Any]] = []
    for r in results:
        summary = r.get("summary") or {}
        live_schema_error = next(
            (
                str(e.get("message") or "")
                for e in (summary.get("errors") or [])
                if isinstance(e, dict) and any(
                    marker in str(e.get("message") or "")
                    for marker in (LIVE_SCHEMA_UNAVAILABLE, AMBIGUOUS_TABLE_BINDING)
                )
            ),
            None,
        )
        if live_schema_error:
            raise HTTPException(status_code=422, detail=live_schema_error)
        target_ddl_drops.extend(summary.get("dropped_entities") or [])
        print(f"[DryRun] model={r.get('model')!r} sml_snapshot_id={summary.get('sml_snapshot_id')!r}")
        if table_binding_plan is None:
            table_binding_plan = summary.get("table_binding_plan")
        for b in summary.get("blockers") or []:
            if b not in dry_run_blockers:
                dry_run_blockers.append(b)
        if sync_mode == "upsert" and upsert_delta is None:
            upsert_delta = r.get("upsert_delta") or summary.get("upsert_delta")
            if upsert_delta is None and str(r.get("status") or "").lower() != "success":
                errors = summary.get("errors") or []
                upsert_delta = {
                    "sync_mode": "upsert",
                    "error": (errors[0].get("message") if errors else None) or "UPSERT delta could not be computed",
                }
        unresolved_report_field_references.extend(
            summary.get("unresolved_report_field_references") or []
        )
        ambiguous_report_aliases.extend(
            summary.get("ambiguous_report_aliases") or []
        )
    if isinstance(table_binding_plan, dict):
        for b in table_binding_plan.get("blockers") or []:
            if b not in dry_run_blockers:
                dry_run_blockers.append(b)

    # ── 4. Build entity mappings from the real SML snapshot ──────────────────
    target_connector = str(target_config.get("type") or "snowflake").strip().lower()

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
    if sml_blob and selected_sources:
        from semabridge.api.services.project_runs_impl import _compat_scope_model_for_dry_run
        sml_blob = _compat_scope_model_for_dry_run(sml_blob, selected_sources)

    built = build_entity_mappings(
        project_id=preview_project_id,
        model=sml_blob or {"unique_name": "preview", "datasets": [], "metrics": []},
        existing_mappings={},
        session_key=f"{preview_project_id}-mapping-session",
        target_connector=target_connector,
    )
    dropped_entities = list(built.get("dropped_entities", []))

    # Target DDL drops come from the engine dry run's live-schema Step 8 —
    # never from a schema-less build (it would drop live-only columns and
    # turn time-intelligence metrics into placeholders).
    trial_sml = None
    if sml_blob and target_connector == "snowflake":
        dropped_entities.extend(target_ddl_drops)

    # ── Reconcile: a field the trial DDL pass just dropped (e.g. a
    # live-schema-only column like MonthIndex when no live schema was
    # available) must not simultaneously show as status "auto" in
    # entity_mappings — that's the same "two independently-computed
    # views of success disagree" gap as the advisory-category
    # override above, closed the same way. Mutates built["mappings"]
    # in place, so every downstream consumer (entity_mappings below,
    # filtered_mappings, auto_count) sees the corrected status.
    from semabridge.api.services.project_mapping_engine import (
        reconcile_predicted_failures_with_drops,
        reconcile_enrichment_unverifiable_advisories,
    )
    reconcile_predicted_failures_with_drops(built.get("mappings", []), dropped_entities)
    # ── Reconcile the OTHER direction: the trial pass can also
    # RESCUE a metric (tag it "cannot verify offline" instead of
    # dropping it — metrics_clause_builder.py's enrichment-flag-column
    # handling). That advisory only exists on trial_sml's live metric
    # objects, mutated after build_entity_mappings already built its
    # rows from the pre-trial-pass model snapshot — merge it back the
    # same way, by structural identity, not by re-deriving it here.
    if trial_sml is not None:
        reconcile_enrichment_unverifiable_advisories(
            built.get("mappings", []), getattr(trial_sml, "metrics", None)
        )

    data = {
        "project_id": preview_project_id,
        "mappings": built.get("mappings", []),
        "source_fields": built.get("source_fields", []),
        "target_fields": built.get("target_fields", []),
        "collisions": built.get("collisions", []),
        "dropped_entities": dropped_entities,
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
    if selected_sources:
        fallback_model_name = selected_sources[0]
    else:
        fallback_model_name = "default"
    model_name = str((sml_blob or {}).get("unique_name") or (sml_blob or {}).get("label") or fallback_model_name)
    source_label = _dry_run_source_label(source_type, selected_sources, source_config, model_name)
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
            "target_expression": m.get("target_expression", ""),
            "sync_enabled": m.get("sync_enabled") != False if m.get("sync_enabled") is not None else True,
            "sync_failure_reason": m.get("sync_failure_reason", ""),
            "advisory_notes": list(m.get("advisory_notes") or []),
            "advisory_categories": list(m.get("advisory_categories") or []),
            "depends_on_measures": list(m.get("depends_on_measures") or []),
            "synonyms": list(m.get("synonyms") or []),
            "synonym_sources": dict(m.get("synonym_sources") or {}),
            "complexity_tier": m.get("complexity_tier"),
            # Static risk tier + label (metric-only; None for
            # tables/columns) and the Tier-5 provider's own self-
            # reported estimate (metric-only, Tier-5-only; None
            # otherwise). See project_mapping_engine.py's
            # _compute_static_risk_tier. The frontend gates on these
            # being non-null rather than on entity_kind, since
            # normalised_kind above already renamed "metric" to
            # "measure" for this response.
            "static_risk_tier": m.get("static_risk_tier"),
            "static_risk_label": m.get("static_risk_label"),
            "llm_self_reported_confidence": m.get("llm_self_reported_confidence"),
            # Which .pbix file this row came from — required so a multi-PBIX
            # project's per-file dry-run results are never ambiguous. This is
            # the exact layer where has_report_alias was previously dropped
            # silently (populated through project_mapping_engine.py and
            # mapping_service.py, then never added to this dict) — see
            # Tests/test_source_file_attribution.py for the regression test
            # that specifically guards this response boundary.
            # Falls back to this scope's label (PBIX stem / Fabric model
            # name) so every row of a per-model result is attributable.
            "source_file": m.get("source_file") or source_label or None,
        })

    auto_count = sum(1 for m in filtered_mappings if m.get("status") == "auto")
    unmapped_count = sum(1 for m in filtered_mappings if m.get("status") == "unmapped")
    collision_count = sum(1 for m in filtered_mappings if m.get("status") == "collision")
    # Structurally known to fail at real-deploy time (unreachable-dimension
    # advisory, or reconciled against a trial-DDL-pass drop) even though it
    # mapped/translated cleanly pre-deploy — never folded into auto_count.
    predicted_failure_count = sum(1 for m in filtered_mappings if m.get("status") == "predicted_failure")

    return {
        "success": True,
        "project_id": preview_project_id,
        "model_name": model_name,
        # Per-model section identity for the UI (multi-model dry-run jobs
        # render one section per result).
        "label": source_label,
        "source_file": source_label,
        "source_type": source_type,
        "source_ref": (selected_sources[0] if len(selected_sources or []) == 1 else None),
        "entity_mappings": filtered_mappings,
        "extraction_failed": extraction_failed,
        "dropped_entities": data.get("dropped_entities", []),
        "unresolved_report_field_references": unresolved_report_field_references,
        "ambiguous_report_aliases": ambiguous_report_aliases,
        "sync_mode": sync_mode,
        "upsert_delta": upsert_delta,
        "table_binding_plan": table_binding_plan,
        "blockers": dry_run_blockers,
        "summary": {
            "total_fields": len(filtered_mappings),
            "auto_mapped": auto_count,
            "unmapped": unmapped_count,
            "collisions": collision_count,
            "predicted_failures": predicted_failure_count,
            "extraction_failed": extraction_failed,
        },
    }


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

    Synchronous, in-request — unchanged from before the multi-PBIX feature.
    For N-file parallel dry-run without a request timeout risk, see the
    dry_run_jobs_controller.py background-job endpoints, which call
    _run_dry_run_pipeline() (the function this delegates to) once per file
    from a background task instead of once, inline, here.
    """
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
        source_type = str(request.source_config.get("type") or "").strip().lower()
        validate_pbix_model_list(
            request.selected_sources,
            source_type=source_type,
        )
        selected_sources = list(request.selected_sources or [])
        if source_type in _PER_MODEL_SOURCE_TYPES:
            from semabridge.api.services.pbix_source_validation import dedupe_sources_case_insensitive

            deduped = dedupe_sources_case_insensitive(selected_sources)
            if len(deduped) > 1:
                # A single synchronous pipeline can only build ONE model's
                # preview (it used to silently return just the first model's
                # fields). Multi-model dry runs run one isolated pipeline per
                # model via the jobs endpoint.
                raise ValidationError(
                    f"{len(deduped)} models were selected; the synchronous dry run previews "
                    f"exactly one model. Use POST /api/projects/{project_id or 'preview'}/dry-run-jobs "
                    "to dry run multiple models in parallel (one result per model)."
                )
            if len(deduped) != len(selected_sources):
                selected_sources = deduped
        return await _run_dry_run_pipeline(
            project_id=project_id,
            request_user_id=request_user_id,
            source_config=request.source_config,
            target_config=request.target_config,
            selected_sources=selected_sources,
            sync_mode_override=request.sync_mode,
        )

    except ValidationError as e:
        # Bad multi-file request input (e.g. malformed source.models) — a client
        # error, not a pipeline crash. Same response shape as the generic handler
        # below so existing frontend error-reading code keeps working, just with
        # the correct 400 status instead of a misleading 500.
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": str(e),
                "use_dry_run_jobs": "/dry-run-jobs" in str(e),
                "entity_mappings": [],
                "dropped_entities": [],
                "summary": {"total_fields": 0, "auto_mapped": 0, "unmapped": 0, "collisions": 0},
            },
        )
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
                "dropped_entities": [],
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
    model_scope = None
    if str(request.model or "").strip():
        model_scope = {
            "project_id": project_id,
            "model": str(request.model).strip(),
            "source_path": str(request.source_path or "").strip(),
            "source_name": str(request.source_name or "").strip(),
            "entity_kind": str(request.entity_kind or "").strip().lower(),
        }
    result = await service.update_mapping_compat(
        mapping_id=mapping_id,
        target_name=request.target_name or "",
        target_data_type=request.target_data_type,
        status="manual",
        model_scope=model_scope,
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


class SaveTableBindingRequest(BaseModel):
    model_name: Optional[str] = None
    dataset: str
    table: str


class SaveSourceDataSchemaRequest(BaseModel):
    source_data_schema: str


@router.post('/api/projects/{project_id}/table-bindings')
async def save_project_table_binding(
    project_id: str,
    request: Request,
    payload: SaveTableBindingRequest,
):
    from semabridge.repository.model_repository import ModelRepository
    from semabridge.connectors.table_binding_resolver import clean_ident
    from semabridge.core.upsert_delta import normalize_model_name

    user_id = require_request_user_id(request)
    if not _is_preview_project_id(project_id):
        _assert_project_access(project_id, user_id)

    model_key = normalize_model_name(payload.model_name or "default")
    binding = {
        "dataset_key": clean_ident(payload.dataset),
        "dataset": payload.dataset,
        "table": payload.table,
        "matched_by": "manual",
    }
    saved = ModelRepository().save_table_bindings(project_id, model_key, [binding])
    return {
        "success": True,
        "saved": saved,
        "project_id": project_id,
        "model_key": model_key,
        "binding": binding,
    }


@router.post('/api/projects/{project_id}/source-data-schema')
async def save_project_source_data_schema(
    project_id: str,
    request: Request,
    payload: SaveSourceDataSchemaRequest,
):
    user_id = require_request_user_id(request)
    if not _is_preview_project_id(project_id):
        _assert_project_access(project_id, user_id)

    schema_val = payload.source_data_schema.strip()
    if not _is_preview_project_id(project_id):
        import semabridge.api.services.project_shared as project_shared
        project_shared._compat_ensure_loaded()
        proj = project_shared._compat_projects.get(project_id)
        if proj:
            proj["source_data_schema"] = schema_val
            if isinstance(proj.get("target"), dict):
                proj["target"]["source_data_schema"] = schema_val
            if isinstance(proj.get("targets"), list) and proj["targets"]:
                for t in proj["targets"]:
                    if isinstance(t, dict):
                        t["source_data_schema"] = schema_val
            cfg_text = project_shared._compat_project_configs.get(project_id)
            if cfg_text:
                try:
                    cfg_dict = yaml.safe_load(cfg_text) or {}
                    if "targets" in cfg_dict and isinstance(cfg_dict["targets"], list):
                        for t in cfg_dict["targets"]:
                            if isinstance(t, dict):
                                t["source_data_schema"] = schema_val
                    cfg_dict.setdefault("options", {})["source_data_schema"] = schema_val
                    project_shared._compat_project_configs[project_id] = yaml.safe_dump(cfg_dict, sort_keys=False)
                except Exception:
                    pass
            project_shared._compat_save_store()

    return {"success": True, "project_id": project_id, "source_data_schema": schema_val}
