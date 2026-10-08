import asyncio
import json
import logging
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from semabridge.api.services.pbix_source_validation import validate_pbix_model_list


class CreateProjectRequest(BaseModel):
    name: Optional[str] = None
    user_id: Optional[str] = None
    account_id: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    targets: Optional[List[Any]] = None
    config_yaml: Optional[str] = None
    connection_tag: Optional[str] = None
    # Optional: migrate synonym overrides edited during the dry run from the
    # preview project id(s) to the new project (see _migrate_preview_synonyms).
    dry_run_job_id: Optional[str] = None
    preview_project_id: Optional[str] = None
    preview_project_ids: Optional[List[str]] = None

    class Config:
        extra = "allow"


class PatchProjectRequest(BaseModel):
    name: Optional[str] = None
    config_yaml: Optional[str] = None
    connection_tag: Optional[str] = None
    source: Optional[Dict[str, Any]] = None
    targets: Optional[List[Any]] = None

    class Config:
        extra = "allow"


class SaveProjectConfigRequest(BaseModel):
    config_yaml: Optional[str] = None

    class Config:
        extra = "allow"


class CaptureSnapshotsRequest(BaseModel):
    label: Optional[str] = None
    roles: Optional[List[str]] = None
    stage: Optional[str] = None

    class Config:
        extra = "allow"


class RestoreProjectVersionRequest(BaseModel):
    snapshot_id: Optional[str] = None
    version_id: Optional[str] = None
    target_ids: Optional[List[str]] = None

    class Config:
        extra = "allow"

from semabridge.api.services.project_ownership_service import (
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
    validate_project_connector_accounts_belong_to_user,
)
from semabridge.api.services.project_domain_service import (
    apply_project_retention_policy,
    capture_manual_snapshots_compat,
    compare_project_snapshots_compat,
    delete_project_snapshots_compat,
    get_project_runs_compat,
    get_project_storage_stats,
    get_run_report_compat,
    get_run_model_report_compat,
    get_run_report_data_compat,
    get_run_model_report_data_compat,
    list_project_snapshots_compat,
    list_snapshot_groups_compat,
    restore_project_version_compat,
)
from semabridge.api.services.projects_service import (
    create_project_compat,
    delete_project_compat,
    get_project_compat,
    get_project_config_compat,
    list_project_discovery_compat,
    list_projects_compat,
    patch_project_compat,
    save_project_config_compat,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _assert_project_access(project_id: str, user_id: str | None) -> None:
    """Synchronous access guard — call via asyncio.to_thread from async endpoints."""
    if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


# Optional create/update payload fields naming the dry-run preview(s) a
# project is being created/saved from. Synonym edits made during a dry run are
# stored under the transient ``preview-*`` project id(s); these fields let the
# backend copy them to the real project id (deploy loads by real id).
_PREVIEW_MIGRATION_FIELDS = ("dry_run_job_id", "preview_project_id", "preview_project_ids")


def _pop_preview_migration_fields(payload_dict: Dict[str, Any]) -> Dict[str, Any]:
    return {key: payload_dict.pop(key, None) for key in _PREVIEW_MIGRATION_FIELDS}


def _preview_project_owned_by(preview_project_id: str, user_id: str | None) -> bool:
    if not auth_is_enabled():
        return True
    try:
        import semabridge.api.services.project_shared as project_shared

        project_shared._compat_ensure_loaded()
        preview = project_shared._compat_projects.get(preview_project_id)
    except Exception:  # noqa: BLE001
        preview = None
    if not isinstance(preview, dict):
        return False
    owner = str(preview.get("owner_user_id") or preview.get("user_id") or "").strip()
    return bool(owner) and owner == str(user_id or "").strip()


def _collect_preview_project_ids(
    fields: Dict[str, Any],
    user_id: str | None,
    source_refs: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Resolve the preview project ids to migrate from: the explicit
    ``preview_project_id`` / ``preview_project_ids`` plus every per-file
    preview id of ``dry_run_job_id``. Only ``preview-*`` ids the caller owns
    (when auth is on) are returned."""
    ids: List[str] = []

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if text.startswith("preview-") and text not in ids:
            ids.append(text)

    explicit = fields.get("preview_project_id")
    if isinstance(explicit, (list, tuple)):
        for item in explicit:
            _add(item)
    else:
        _add(explicit)
    for item in fields.get("preview_project_ids") or []:
        _add(item)
    explicit_ids = [pid for pid in ids if _preview_project_owned_by(pid, user_id)]

    job_ids: List[str] = []
    job_id = str(fields.get("dry_run_job_id") or "").strip()
    if job_id:
        try:
            from semabridge.api.services.dry_run_job_service import (
                get_dry_run_job_meta,
                get_dry_run_job_preview_project_ids,
            )

            meta = get_dry_run_job_meta(job_id)
            owner = str(meta.get("requested_by_user_id") or "").strip()
            if auth_is_enabled() and owner != str(user_id or "").strip():
                logger.warning(
                    "[SynonymMigration] skip dry_run_job_id=%s: not owned by user_id=%s", job_id, user_id
                )
            else:
                for entry in get_dry_run_job_preview_project_ids(job_id):
                    pid = str(entry.get("preview_project_id") or "").strip()
                    if pid.startswith("preview-") and pid not in job_ids:
                        job_ids.append(pid)
                        if source_refs is not None and entry.get("source_ref"):
                            source_refs[pid] = str(entry["source_ref"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("[SynonymMigration] could not resolve dry_run_job_id=%s: %s", job_id, exc)

    out: List[str] = []
    for pid in explicit_ids + job_ids:
        if pid not in out:
            out.append(pid)
    return out


def _migrate_preview_synonyms(target_project_id: str, fields: Dict[str, Any], user_id: str | None) -> Optional[Dict[str, Any]]:
    """Copy synonym overrides from the named preview project(s) to the real
    project. Returns a small report (or None when nothing was requested).
    Never raises — a synonym migration failure must not fail project save."""
    if not any(fields.get(key) for key in _PREVIEW_MIGRATION_FIELDS):
        return None
    target = str(target_project_id or "").strip()
    if not target or target == "preview" or target.startswith("preview-"):
        return None
    try:
        from semabridge.utils.synonyms import copy_synonym_overrides

        source_refs: Dict[str, str] = {}
        sources = _collect_preview_project_ids(fields, user_id, source_refs)
        copied = (
            copy_synonym_overrides(
                sources, target,
                model_name_by_source=_saved_model_names_for_previews(target, source_refs),
            )
            if sources else 0
        )
        return {"copied": copied, "source_project_ids": sources}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SynonymMigration] failed for project %s: %s", target, exc)
        return {"copied": 0, "source_project_ids": [], "error": str(exc)}


def _saved_model_names_for_previews(target_project_id: str, source_refs: Dict[str, str]) -> Dict[str, str]:
    """Multi-mode projects: map each model's dry-run preview project to the
    model name it deploys under (its saved view name), so that model's
    synonyms are stored for that model only. Empty for single-mode projects,
    whose preview and deploy model names already match."""
    if not source_refs:
        return {}
    try:
        import yaml

        import semabridge.api.services.project_shared as project_shared
        from semabridge.utils.project_models import is_multi_mode, saved_view_name_for

        project_shared._compat_ensure_loaded()
        cfg_text = project_shared._compat_project_configs.get(target_project_id) or ""
        parsed = yaml.safe_load(cfg_text) or {}
        source_cfg = parsed.get("source") if isinstance(parsed, dict) and isinstance(parsed.get("source"), dict) else {}
        if not is_multi_mode(source_cfg):
            return {}
        out: Dict[str, str] = {}
        for preview_id, source_ref in source_refs.items():
            name = saved_view_name_for(source_cfg, source_ref)
            if name:
                out[preview_id] = name
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[SynonymMigration] could not read saved model names for %s: %s", target_project_id, exc)
        return {}


def _attach_migration_report(response: Any, report: Optional[Dict[str, Any]]) -> Any:
    if report is not None and isinstance(response, dict):
        response = dict(response)
        response["synonym_migration"] = report
    return response


@router.get("/api/projects")
async def list_projects(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="Max projects to return"),
    offset: int = Query(0, ge=0, description="Number of projects to skip"),
):
    user_id = require_request_user_id(request)
    projects = await list_projects_compat(limit=limit, offset=offset)
    if auth_is_enabled():
        return [
            project
            for project in projects
            if (project.get("id") or project.get("project_id"))
            and is_project_owned_by_user(project.get("id") or project.get("project_id"), user_id, log_denied=False)
        ]
    return projects


@router.get("/api/projects/discovery")
async def list_project_discovery(request: Request):
    user_id = require_request_user_id(request)
    entries = await list_project_discovery_compat()
    if auth_is_enabled():
        return [
            entry
            for entry in entries
            if (entry.get("id") or entry.get("project_id"))
            and is_project_owned_by_user(entry.get("id") or entry.get("project_id"), user_id, log_denied=False)
        ]
    return entries


@router.post("/api/projects")
async def create_project(request: Request, payload: CreateProjectRequest):
    user_id = require_request_user_id(request)
    payload_dict = payload.model_dump(exclude_none=False)
    src_for_validation = payload_dict.get("source") if isinstance(payload_dict.get("source"), dict) else {}
    validation_source_type = str(src_for_validation.get("type") or "")
    validate_pbix_model_list(src_for_validation.get("models"), source_type=validation_source_type)
    if user_id:
        payload_dict["user_id"] = str(user_id)
        validate_project_connector_accounts_belong_to_user(user_id, payload_dict)
        src = payload_dict.get("source") if isinstance(payload_dict.get("source"), dict) else {}
        identity_id = src.get("identity_id") or payload_dict.get("account_id")
        logger.info(
            "[ProjectCreate] user_id=%s payload_name=%s source_identity_id=%s target_count=%s",
            user_id,
            payload_dict.get("name"),
            identity_id,
            len(payload_dict.get("targets") or []),
        )
    migration_fields = _pop_preview_migration_fields(payload_dict)
    created = await create_project_compat(payload_dict)
    if isinstance(created, dict):
        new_id = str(created.get("id") or created.get("project_id") or "").strip()
        report = await asyncio.to_thread(_migrate_preview_synonyms, new_id, migration_fields, user_id)
        return _attach_migration_report(created, report)
    return created


@router.get("/api/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await get_project_compat(project_id)


@router.patch("/api/projects/{project_id}")
async def patch_project(project_id: str, payload: PatchProjectRequest, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    payload_dict = payload.model_dump(exclude_none=False)
    migration_fields = _pop_preview_migration_fields(payload_dict)
    response = await patch_project_compat(project_id, payload_dict)
    report = await asyncio.to_thread(_migrate_preview_synonyms, project_id, migration_fields, user_id)
    return _attach_migration_report(response, report)


@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await delete_project_compat(project_id)


@router.get("/api/projects/{project_id}/config")
async def get_project_config(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await get_project_config_compat(project_id)


@router.put("/api/projects/{project_id}/config")
async def save_project_config(project_id: str, payload: SaveProjectConfigRequest, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    payload_dict = payload.model_dump(exclude_none=False)
    migration_fields = _pop_preview_migration_fields(payload_dict)
    response = await save_project_config_compat(project_id, payload_dict)
    report = await asyncio.to_thread(_migrate_preview_synonyms, project_id, migration_fields, user_id)
    return _attach_migration_report(response, report)


@router.get("/api/projects/{project_id}/snapshots")
async def list_project_snapshots(
    project_id: str,
    request: Request,
    role: str | None = Query(None),
    stage: str | None = Query(None),
    origin: str | None = Query(None),
    run_id: str | None = Query(None),
    group_id: str | None = Query(None),
    include_state: bool = Query(False),
    limit: int = Query(200, ge=1, le=500),
):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await list_project_snapshots_compat(
        project_id,
        role=role,
        stage=stage,
        origin=origin,
        run_id=run_id,
        group_id=group_id,
        include_state=include_state,
        limit=limit,
    )


@router.delete("/api/projects/{project_id}/snapshots")
async def delete_snapshots(project_id: str, snapshot_ids: List[str], request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await delete_project_snapshots_compat(project_id, snapshot_ids)


@router.get("/api/projects/{project_id}/snapshot-groups")
async def list_snapshot_groups(
    project_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=500),
):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await list_snapshot_groups_compat(project_id, limit=limit)


@router.post("/api/projects/{project_id}/snapshots/capture")
async def capture_snapshots(project_id: str, payload: CaptureSnapshotsRequest, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await capture_manual_snapshots_compat(project_id, payload.model_dump(exclude_none=False))


@router.get("/api/projects/{project_id}/snapshots/compare")
async def compare_snapshots(
    project_id: str,
    request: Request,
    from_snapshot_id: str | None = Query(None, alias="from_snapshot_id"),
    to_snapshot_id: str | None = Query(None, alias="to_snapshot_id"),
    base_id: str | None = Query(None),
    target_id: str | None = Query(None),
    s1: str | None = Query(None),
    s2: str | None = Query(None),
    max_changes: int = Query(200, ge=1, le=1000),
    include_states: bool = Query(False),
):
    resolved_from_snapshot_id = str(from_snapshot_id or base_id or s1 or "").strip() or None
    resolved_to_snapshot_id = str(to_snapshot_id or target_id or s2 or "").strip() or None
    if not resolved_from_snapshot_id or not resolved_to_snapshot_id:
        actual_params = dict(request.query_params)
        logger.warning(
            "[ProjectSnapshotCompareValidation] invalid request project_id=%s actual_params=%s "
            "expected_any_of_from=%s expected_any_of_to=%s resolved_from_snapshot_id=%s resolved_to_snapshot_id=%s",
            project_id,
            actual_params,
            ["from_snapshot_id", "base_id", "s1"],
            ["to_snapshot_id", "target_id", "s2"],
            resolved_from_snapshot_id,
            resolved_to_snapshot_id,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Snapshot compare request is missing required query parameters.",
                "expected": {
                    "from_snapshot_id": ["from_snapshot_id", "base_id", "s1"],
                    "to_snapshot_id": ["to_snapshot_id", "target_id", "s2"],
                },
                "actual": actual_params,
            },
        )

    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await compare_project_snapshots_compat(
        project_id,
        resolved_from_snapshot_id,
        resolved_to_snapshot_id,
        max_changes=max_changes,
        include_states=include_states,
    )


@router.post("/api/projects/{project_id}/restore-version")
async def restore_project_version(project_id: str, payload: RestoreProjectVersionRequest, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await restore_project_version_compat(project_id, payload.model_dump(exclude_none=False))


@router.get("/api/projects/{project_id}/runs")
async def get_project_runs(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await get_project_runs_compat(project_id)


@router.get("/api/projects/{project_id}/runs/{run_id}/report")
async def download_run_report(project_id: str, run_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    try:
        report = await get_run_report_compat(project_id, run_id)
        if not report:
            logger.warning("Run report requested but not found for project=%s run=%s", project_id, run_id)
            raise HTTPException(status_code=404, detail=f"No report found for run {run_id}")

        return Response(
            content=report["content"].encode("utf-8"),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{report["filename"]}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to serve run report for project=%s run=%s: %s", project_id, run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to generate report download: {exc}") from exc


@router.get("/api/projects/{project_id}/runs/{run_id}/report/{model_label:path}")
async def download_run_model_report(project_id: str, run_id: str, model_label: str, request: Request):
    """Per-model report download for a multi-model run -- see
    download_run_report() above for the whole-run equivalent.
    """
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    try:
        report = await get_run_model_report_compat(project_id, run_id, model_label)
        if not report:
            logger.warning(
                "Run model report requested but not found for project=%s run=%s model=%s",
                project_id, run_id, model_label,
            )
            raise HTTPException(status_code=404, detail=f"No report found for run {run_id}, model {model_label}")

        return Response(
            content=report["content"].encode("utf-8"),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{report["filename"]}"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to serve run model report for project=%s run=%s model=%s: %s",
            project_id, run_id, model_label, exc,
        )
        raise HTTPException(status_code=500, detail=f"Failed to generate report download: {exc}") from exc


@router.get("/api/projects/{project_id}/runs/{run_id}/report-summary")
async def get_run_report_summary(project_id: str, run_id: str, request: Request):
    """JSON counterpart to download_run_report, for the frontend's
    accordion-style summary view (clean counts collapsed, full per-item
    detail on expand) -- built fresh from the run's own snapshot/drop-
    ledger data rather than parsing the rendered Markdown file."""
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    try:
        data = await get_run_report_data_compat(project_id, run_id)
        if not data:
            logger.warning("Run report summary requested but not found for project=%s run=%s", project_id, run_id)
            raise HTTPException(status_code=404, detail=f"No report found for run {run_id}")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to build run report summary for project=%s run=%s: %s", project_id, run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to build report summary: {exc}") from exc


@router.get("/api/projects/{project_id}/runs/{run_id}/report-summary/{model_label:path}")
async def get_run_model_report_summary(project_id: str, run_id: str, model_label: str, request: Request):
    """Per-model counterpart to get_run_report_summary() -- see
    download_run_model_report() above for the Markdown equivalent.
    """
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    try:
        data = await get_run_model_report_data_compat(project_id, run_id, model_label)
        if not data:
            logger.warning(
                "Run model report summary requested but not found for project=%s run=%s model=%s",
                project_id, run_id, model_label,
            )
            raise HTTPException(status_code=404, detail=f"No report found for run {run_id}, model {model_label}")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to build run model report summary for project=%s run=%s model=%s: %s",
            project_id, run_id, model_label, exc,
        )
        raise HTTPException(status_code=500, detail=f"Failed to build report summary: {exc}") from exc


@router.get("/api/projects/{project_id}/vc/stats")
async def get_project_vc_stats(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await get_project_storage_stats(project_id)


@router.post("/api/projects/{project_id}/vc/retention")
async def run_project_retention(project_id: str, request: Request, days: int = 30):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    return await apply_project_retention_policy(project_id, days)


@router.get("/api/projects/{project_id}/export")
async def export_project(project_id: str, request: Request):
    user_id = require_request_user_id(request)
    await asyncio.to_thread(_assert_project_access, project_id, user_id)
    project = await get_project_config_compat(project_id)
    config_yaml = str(project.get("config_yaml") or "").strip()
    if not config_yaml:
        raise HTTPException(status_code=404, detail=f"No exportable config found for project {project_id}")

    filename = f"{project_id}.yaml"
    return Response(
        content=config_yaml.encode("utf-8"),
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/projects/export-bulk")
async def export_projects_bulk(project_ids: List[str], request: Request):
    user_id = require_request_user_id(request)
    buffer = BytesIO()
    exported = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for project_id in project_ids:
            if auth_is_enabled() and not is_project_owned_by_user(project_id, user_id):
                continue
            try:
                project = await get_project_config_compat(project_id)
                config_yaml = str(project.get("config_yaml") or "").strip()
                if not config_yaml:
                    continue
                zf.writestr(f"{project_id}.yaml", config_yaml)
                exported.append(project_id)
            except Exception:
                continue

        manifest = yaml.safe_dump({"exported_projects": exported}, sort_keys=False)
        zf.writestr("manifest.yaml", manifest)

    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="semabridge_projects.zip"'},
    )


@router.post("/api/projects/import")
async def import_projects(request: Request, files: List[UploadFile] = File(...)):
    user_id = require_request_user_id(request)
    imported = []
    errors = []

    for upload in files:
        filename = Path(upload.filename or "project.yaml").name
        try:
            raw = await upload.read()
            text = raw.decode("utf-8")
            parsed = yaml.safe_load(text)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
            if not isinstance(parsed, dict):
                raise ValueError("Project file must contain a mapping/object")

            project_id = str(
                parsed.get("project_id")
                or parsed.get("id")
                or Path(filename).stem
            ).strip()
            if not project_id:
                raise ValueError("Unable to determine project_id")

            validate_project_connector_accounts_belong_to_user(user_id, parsed)

            project_payload = {
                "id": project_id,
                "project_id": project_id,
                "name": parsed.get("project_name") or parsed.get("name") or project_id,
                "config_yaml": text,
                "source": parsed.get("source") if isinstance(parsed.get("source"), dict) else {},
                "target": parsed.get("target") if isinstance(parsed.get("target"), dict) else {},
            }
            if user_id:
                project_payload["user_id"] = str(user_id)
            created = await create_project_compat(project_payload)
            imported.append(
                {
                    "filename": filename,
                    "project_id": created.get("project_id") or project_id,
                    "name": created.get("name") or project_id,
                }
            )
        except Exception as exc:
            errors.append({"filename": filename, "error": str(exc)})

    status_code = 207 if errors and imported else 400 if errors and not imported else 200
    return JSONResponse(
        status_code=status_code,
        content={"imported": imported, "errors": errors},
    )
