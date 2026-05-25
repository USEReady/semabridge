import asyncio
import json
import re
import time as _time
from pathlib import Path
from typing import Any, Dict, List

import yaml
from fastapi import HTTPException, Query
from starlette.responses import Response

from semabridge.api.services.project_shared import (
    _compat_clean_project_name,
    _compat_default_project_yaml,
    _compat_deleted_project_ids,
    _compat_ensure_loaded,
    _compat_folders,
    _compat_load_project_yaml_text,
    _compat_load_repo_yaml_text,
    _compat_mappings,
    _compat_now_iso,
    _compat_project_configs,
    _compat_project_payload,
    _compat_project_runs,
    _compat_project_schedules,
    _compat_project_snapshots,
    _compat_project_yaml_paths,
    _compat_project_yaml_path,
    _compat_projects,
    _compat_projects_dirs,
    _compat_run_snapshots,
    _compat_save_project_yaml_text,
    _compat_save_store,
    _compat_snapshot_groups,
    _compat_store_loaded,
    db_manager,
    logger,
)


def _resolve_project_account_id(payload: dict) -> str | None:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    targets = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    first_target = targets[0] if targets and isinstance(targets[0], dict) else {}
    account_id = (
        payload.get("account_id")
        or source.get("identity_id")
        or source.get("account_id")
        or target.get("identity_id")
        or target.get("account_id")
        or first_target.get("identity_id")
        or first_target.get("account_id")
    )
    token = str(account_id or "").strip()
    return token or None


def _upsert_project_in_orm(project_id: str, project: Dict[str, Any], payload: dict) -> None:
    pid = str(project_id or "").strip()
    if not pid:
        return

    try:
        from datetime import datetime
        from semabridge.repository.orm.models import Project
        from semabridge.repository.orm.session_factory import db_manager as orm_db_manager

        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        project_name = str(project.get("name") or pid).strip() or pid
        workspace_id = str(
            source.get("workspace_id")
            or payload.get("workspace_id")
            or project.get("workspace_id")
            or ""
        ).strip() or None
        adapter = str(project.get("adapter") or source.get("type") or "fabric").strip() or "fabric"
        connection_tag = str(project.get("connection_tag") or "").strip() or None
        account_id = _resolve_project_account_id(payload)

        with orm_db_manager.get_session() as session:
            existing = session.get(Project, pid)
            if existing is None:
                existing = Project(
                    project_id=pid,
                    name=project_name,
                )
                session.add(existing)

            existing.name = project_name
            existing.workspace_id = workspace_id
            existing.adapter = adapter
            existing.account_id = account_id
            existing.connection_tag = connection_tag
            existing.last_updated = datetime.utcnow()
            session.commit()
    except Exception as exc:
        logger.warning("Failed to upsert ORM project %s: %s", pid, exc)


def _backfill_project_metadata(project_id: str, project: Dict[str, Any]) -> None:
    pid = str(project_id or "").strip()
    if not pid or not isinstance(project, dict):
        return

    raw_yaml = str(_compat_project_configs.get(pid) or _compat_load_project_yaml_text(pid) or "").strip()
    parsed = {}
    if raw_yaml:
        try:
            parsed = yaml.safe_load(raw_yaml) or {}
        except Exception:
            parsed = {}

    source_cfg = parsed.get("source") if isinstance(parsed, dict) and isinstance(parsed.get("source"), dict) else {}
    target_cfg = parsed.get("target") if isinstance(parsed, dict) and isinstance(parsed.get("target"), dict) else {}
    targets_cfg = parsed.get("targets") if isinstance(parsed, dict) and isinstance(parsed.get("targets"), list) else []
    first_target_cfg = targets_cfg[0] if targets_cfg and isinstance(targets_cfg[0], dict) else {}

    changed = False

    if source_cfg and not isinstance(project.get("source_config"), dict):
        project["source_config"] = source_cfg
        changed = True
    if target_cfg and not isinstance(project.get("target"), dict):
        project["target"] = target_cfg
        changed = True
    if targets_cfg and not isinstance(project.get("targets"), list):
        project["targets"] = targets_cfg
        changed = True

    source_account_id = str(
        project.get("source_account_id")
        or source_cfg.get("identity_id")
        or source_cfg.get("account_id")
        or ""
    ).strip() or None
    target_account_id = str(
        project.get("target_account_id")
        or target_cfg.get("identity_id")
        or target_cfg.get("account_id")
        or first_target_cfg.get("identity_id")
        or first_target_cfg.get("account_id")
        or ""
    ).strip() or None
    account_id = str(
        project.get("account_id")
        or source_account_id
        or target_account_id
        or ""
    ).strip() or None

    for key, value in (
        ("source_account_id", source_account_id),
        ("target_account_id", target_account_id),
        ("account_id", account_id),
    ):
        if project.get(key) != value and value:
            project[key] = value
            changed = True

    if not project.get("workspace_id") and source_cfg.get("workspace_id"):
        project["workspace_id"] = str(source_cfg.get("workspace_id"))
        changed = True

    if changed:
        _compat_projects[pid] = project
        _compat_save_store()

    orm_payload = {
        "account_id": account_id,
        "workspace_id": project.get("workspace_id"),
        "source": source_cfg or project.get("source_config") or {},
        "target": target_cfg or project.get("target") or {},
        "targets": targets_cfg or project.get("targets") or [],
    }
    _upsert_project_in_orm(pid, project, orm_payload)

def _clear_project_mapping_cache(project_id: str) -> None:
    """Clear compat mapping cache entries for a project after config creation/save."""
    pid = str(project_id or "").strip()
    if not pid:
        return
    removed = 0
    for mapping_id, mapping in list(_compat_mappings.items()):
        if not isinstance(mapping, dict):
            continue
        if str(mapping.get("project_id") or "").strip() != pid:
            continue
        _compat_mappings.pop(mapping_id, None)
        removed += 1
    if removed:
        logger.info("Cleared %s cached mapping row(s) for project %s after config write", removed, pid)


def _delete_project_config_files(project_id: str) -> None:
    pid = str(project_id or "").strip()
    if not pid:
        return

    failures: List[str] = []
    for path in _compat_project_yaml_paths(pid):
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except Exception as exc:
            logger.warning("Failed to delete project config file %s: %s", path, exc)
            failures.append(f"{path}: {exc}")

    remaining = [str(path) for path in _compat_project_yaml_paths(pid) if path.exists()]
    if failures or remaining:
        details = "; ".join(failures + [f"still exists: {path}" for path in remaining])
        raise RuntimeError(f"Failed to delete project config file(s) for {pid}: {details}")


def _delete_project_from_orm(project_id: str) -> None:
    pid = str(project_id or "").strip()
    if not pid:
        return

    try:
        from semabridge.repository.orm.models import Project
        from semabridge.repository.orm.session_factory import db_manager as orm_db_manager

        with orm_db_manager.get_session() as session:
            project = session.get(Project, pid)
            if project is not None:
                session.delete(project)
                session.commit()
    except Exception as exc:
        logger.warning("Failed to delete ORM project %s: %s", pid, exc)


def _project_display_name_from_cfg(project_cfg: Dict[str, Any], fallback: str) -> str:
    if not isinstance(project_cfg, dict):
        return fallback
    meta = project_cfg.get("project_metadata") if isinstance(project_cfg.get("project_metadata"), dict) else {}
    return _compat_clean_project_name(
        project_cfg.get("display_name") or meta.get("display_name") or meta.get("name") or project_cfg.get("project_name"),
        fallback,
    )


def _normalize_project_config_yaml(
    project_id: str,
    yaml_text: str,
    default_name: str,
    owner_user_id: str | None = None,
) -> str:
    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid YAML content: {exc}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="Project YAML must be an object mapping")

    display_name = _project_display_name_from_cfg(parsed, default_name)
    if not display_name:
        display_name = default_name

    parsed["project_id"] = project_id
    parsed["display_name"] = display_name
    if not str(parsed.get("project_name") or "").strip():
        parsed["project_name"] = display_name or default_name
    normalized_owner = str(owner_user_id or parsed.get("owner_user_id") or "").strip()
    if normalized_owner:
        parsed["owner_user_id"] = normalized_owner
        metadata = parsed.get("project_metadata") if isinstance(parsed.get("project_metadata"), dict) else {}
        metadata["owner_user_id"] = normalized_owner
        parsed["project_metadata"] = metadata
    return yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)


def _project_discovery_entry(project_id: str, file_path: Path, project_cfg: Dict[str, Any]) -> Dict[str, Any]:
    source = project_cfg.get("source") if isinstance(project_cfg.get("source"), dict) else {}
    target = project_cfg.get("target") if isinstance(project_cfg.get("target"), dict) else {}
    if not target and isinstance(project_cfg.get("targets"), list) and project_cfg.get("targets"):
        first_target = project_cfg.get("targets")[0]
        if isinstance(first_target, dict):
            target = first_target

    display_name = _project_display_name_from_cfg(project_cfg, project_id)
    return {
        "id": project_id,
        "project_id": project_id,
        "name": display_name,
        "display_name": display_name,
        "file_name": file_path.name,
        "file_path": str(file_path.resolve()).replace('\\', '/'),
        "source": source.get("type", "fabric"),
        "target_type": target.get("type", "snowflake"),
        "workspace_id": source.get("workspace_id", ""),
    }


def _project_id_exists_anywhere(project_id: str) -> bool:
    pid = str(project_id or "").strip()
    if not pid:
        return False
    if pid in _compat_projects or pid in _compat_project_configs:
        return True
    if str(_compat_load_project_yaml_text(pid) or "").strip():
        return True
    return any(path.exists() for path in _compat_project_yaml_paths(pid))


async def list_project_discovery_compat():
    await asyncio.to_thread(_compat_ensure_loaded)
    entries: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for projects_dir in _compat_projects_dirs():
        if not projects_dir.exists() or not projects_dir.is_dir():
            continue

        for file_path in sorted(projects_dir.glob("*.y*ml")):
            project_id = file_path.stem.strip()
            if (
                not project_id
                or project_id in seen
                or project_id in _compat_deleted_project_ids
                or project_id == "preview"
                or project_id.startswith("preview-")
            ):
                continue
            try:
                file_text = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
                project_cfg = yaml.safe_load(file_text) or {}
                if not isinstance(project_cfg, dict):
                    continue
                entries.append(_project_discovery_entry(project_id, file_path, project_cfg))
                seen.add(project_id)
            except Exception as exc:
                logger.warning("Failed to load project discovery entry %s: %s", file_path, exc)
    return entries


async def list_projects_compat():
    """Compatibility: newfrontend expects a projects collection."""
    await asyncio.to_thread(_compat_ensure_loaded)
    deduped: Dict[str, Dict[str, Any]] = {}
    
    # Load modular projects from Config/projects (or config/projects)
    for projects_dir in _compat_projects_dirs():
        if not projects_dir.exists() or not projects_dir.is_dir():
            continue
        for file_path in sorted(projects_dir.glob("*.y*ml")):
            pid = file_path.stem.strip()
            if (
                not pid
                or pid in deduped
                or pid in _compat_deleted_project_ids
                or pid == "preview"
                or pid.startswith("preview-")
            ):
                continue
            try:
                file_text = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
                project_cfg = yaml.safe_load(file_text) or {}
                if not isinstance(project_cfg, dict):
                    continue
                deduped[pid] = _project_discovery_entry(pid, file_path, project_cfg)
            except Exception as e:
                logger.warning(f"Failed to load project config {file_path}: {e}")

    for p in _compat_projects.values():
        pid = str(p.get("id") or p.get("project_id") or "").strip()
        if not pid or pid in deduped:
            continue
        if p.get("is_transient_preview") or pid == "preview" or pid.startswith("preview-"):
            continue
        
        # Filter out auto-generated test projects
        project_name = str(p.get("name") or "").strip().lower()
        is_test = (
            project_name in ("test", "teste", "test project") or
            project_name == "fabricmodel"  # Auto-generated default
        )
        if is_test:
            # Skip test/auto-generated projects
            continue
        
        current = deduped.get(pid)
        if not current:
            deduped[pid] = p
            continue
        cur_ts = str(current.get("updated_at") or current.get("created_at") or "")
        new_ts = str(p.get("updated_at") or p.get("created_at") or "")
        if new_ts >= cur_ts:
            deduped[pid] = p

    return list(deduped.values())


async def create_project_compat(request: dict):
    """Compatibility: create in-memory project for UI continuity."""
    await asyncio.to_thread(_compat_ensure_loaded)
    payload = request or {}
    payload_name = _compat_clean_project_name(payload.get("name"), "")
    src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    src_type = (src.get("type") or payload.get("source_type") or "fabric").strip().lower()
    ws_id = str(src.get("workspace_id") or payload.get("workspace_id") or "").strip()

    # Always create a distinct project record for create requests.
    # Projects that target the same semantic model/workspace must not share
    # mapping state implicitly, because each project can have different naming.

    safe_name = re.sub(r'[^a-zA-Z0-9_]+', '-', payload_name.lower()).strip('-')
    default_id = f"proj-{safe_name}" if safe_name else f"proj-{int(_time.time() * 1000)}"
    requested_id = str(payload.get("id") or payload.get("project_id") or default_id).strip()
    project_id = requested_id or default_id
    _compat_deleted_project_ids.discard(project_id)
    if _project_id_exists_anywhere(project_id):
        # Avoid reusing an existing project's mapping cache/state when the user
        # creates another project with the same semantic model/name or a stale
        # modular YAML file still exists for that id.
        project_id = f"{project_id}-{int(_time.time() * 1000)}"
    project = _compat_project_payload(project_id, payload)
    _compat_projects[project_id] = project
    await asyncio.to_thread(_upsert_project_in_orm, project_id, project, payload)

    config_yaml = payload.get("config_yaml")
    if isinstance(config_yaml, str) and config_yaml.strip():
        normalized_yaml = _normalize_project_config_yaml(
            project_id,
            config_yaml,
            project.get("name") or project_id,
            project.get("owner_user_id"),
        )
        _compat_project_configs[project_id] = normalized_yaml
        try:
            await asyncio.to_thread(_compat_save_project_yaml_text, project_id, normalized_yaml)
            _clear_project_mapping_cache(project_id)
        except Exception as exc:
            logger.warning("Failed to persist project config for %s: %s", project_id, exc)
    else:
        project_yaml = await asyncio.to_thread(_compat_load_project_yaml_text, project_id)
        repo_yaml = await asyncio.to_thread(_compat_load_repo_yaml_text)
        selected_yaml = project_yaml or repo_yaml or _compat_default_project_yaml(project)
        normalized_yaml = _normalize_project_config_yaml(
            project_id,
            selected_yaml,
            project.get("name") or project_id,
            project.get("owner_user_id"),
        )
        _compat_project_configs.setdefault(project_id, normalized_yaml)
        try:
            await asyncio.to_thread(_compat_save_project_yaml_text, project_id, normalized_yaml)
            _clear_project_mapping_cache(project_id)
        except Exception as exc:
            logger.warning("Failed to initialize project config file for %s: %s", project_id, exc)

    _compat_project_runs.setdefault(project_id, [])
    await asyncio.to_thread(_compat_save_store)
    return project


async def get_project_compat(project_id: str):
    await asyncio.to_thread(_compat_ensure_loaded)
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await asyncio.to_thread(_backfill_project_metadata, project_id, project)
    return project


async def patch_project_compat(project_id: str, payload: dict):
    await asyncio.to_thread(_compat_ensure_loaded)
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for key in ("name", "display_name", "description", "folder_id", "status"):
        if key in (payload or {}):
            project[key] = payload.get(key)

    if payload.get("name") and not payload.get("display_name"):
        project["display_name"] = payload.get("name")
        project["name"] = payload.get("name")
    elif payload.get("display_name") and not payload.get("name"):
        project["name"] = payload.get("display_name")

    if isinstance(payload.get("source"), dict):
        project["source"] = payload["source"].get("type") or project.get("source")
        project["source_config"] = payload["source"]
        project["adapter"] = project["source"]
        if "workspace_id" in payload["source"]:
            project["workspace_id"] = payload["source"].get("workspace_id")
        if "connection_tag" in payload["source"]:
            project["connection_tag"] = payload["source"].get("connection_tag")
        source_account_id = str(
            payload["source"].get("identity_id") or payload["source"].get("account_id") or ""
        ).strip() or None
        project["source_account_id"] = source_account_id
        if source_account_id:
            project["account_id"] = source_account_id

    if isinstance(payload.get("target"), dict):
        project["target"] = payload["target"]
        project["target_type"] = payload["target"].get("type") or project.get("target_type")
        target_account_id = str(
            payload["target"].get("identity_id") or payload["target"].get("account_id") or ""
        ).strip() or None
        project["target_account_id"] = target_account_id
        if not project.get("account_id") and target_account_id:
            project["account_id"] = target_account_id

    if isinstance(payload.get("targets"), list) and payload.get("targets"):
        project["targets"] = payload["targets"]
        first_target = payload["targets"][0] if isinstance(payload["targets"][0], dict) else {}
        if first_target.get("type"):
            project["target_type"] = first_target.get("type")
        if first_target.get("connection_tag"):
            project["connection_tag"] = first_target.get("connection_tag")
        target_account_id = str(
            first_target.get("identity_id") or first_target.get("account_id") or ""
        ).strip() or None
        project["target_account_id"] = target_account_id
        if not project.get("account_id") and target_account_id:
            project["account_id"] = target_account_id

    if payload.get("connection_tag"):
        project["connection_tag"] = payload.get("connection_tag")
    if payload.get("account_id"):
        project["account_id"] = str(payload.get("account_id")).strip() or project.get("account_id")
    if payload.get("user_id"):
        owner_user_id = str(payload.get("user_id")).strip() or project.get("owner_user_id")
        project["owner_user_id"] = owner_user_id
        project["user_id"] = owner_user_id

    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    await asyncio.to_thread(_upsert_project_in_orm, project_id, project, payload)
    await asyncio.to_thread(_compat_save_store)
    return project


async def delete_project_compat(project_id: str):
    await asyncio.to_thread(_compat_ensure_loaded)
    _compat_deleted_project_ids.add(project_id)
    await asyncio.to_thread(_delete_project_config_files, project_id)
    await asyncio.to_thread(_delete_project_from_orm, project_id)
    _compat_projects.pop(project_id, None)
    _compat_project_configs.pop(project_id, None)
    _compat_project_runs.pop(project_id, None)
    _compat_project_snapshots.pop(project_id, None)
    _compat_snapshot_groups.pop(project_id, None)
    _compat_run_snapshots.pop(project_id, None)
    _compat_project_schedules.pop(project_id, None)
    for mapping_id, mapping in list(_compat_mappings.items()):
        if isinstance(mapping, dict) and str(mapping.get("project_id") or "").strip() == project_id:
            _compat_mappings.pop(mapping_id, None)
    await asyncio.to_thread(_compat_save_store)
    return Response(status_code=204)


async def get_project_config_compat(project_id: str, prefer_repo: bool = Query(default=False)):
    await asyncio.to_thread(_compat_ensure_loaded)
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await asyncio.to_thread(_backfill_project_metadata, project_id, project)
    # IMPORTANT: default behavior prefers per-project config so "Copy Presets"
    # can load different YAMLs for different projects. Some UI flows (for
    # example Model Mapping) can opt into prefer_repo=True to reflect the
    # workspace semabridge.yaml as the single source of truth.
    repo_yaml = await asyncio.to_thread(_compat_load_repo_yaml_text)
    prefer_repo_flag = prefer_repo if isinstance(prefer_repo, bool) else False
    project_yaml = await asyncio.to_thread(_compat_load_project_yaml_text, project_id)
    if prefer_repo_flag and repo_yaml:
        yaml_text = repo_yaml
        _compat_project_configs[project_id] = repo_yaml
    else:
        yaml_text = project_yaml or _compat_project_configs.get(project_id) or repo_yaml or _compat_default_project_yaml(project)
        yaml_text = _normalize_project_config_yaml(
            project_id,
            yaml_text,
            project.get("name") or project_id,
            project.get("owner_user_id"),
        )
        if not project_yaml and yaml_text:
            try:
                await asyncio.to_thread(_compat_save_project_yaml_text, project_id, yaml_text)
            except Exception as exc:
                logger.warning("Failed to persist hydrated project config for %s: %s", project_id, exc)
    _compat_project_configs[project_id] = yaml_text
    return {
        "project_id": project_id,
        "config_yaml": yaml_text,
        "yaml_path": str(_compat_project_yaml_path(project_id).resolve()).replace('\\\\', '/'),
    }


async def save_project_config_compat(project_id: str, payload: dict):
    await asyncio.to_thread(_compat_ensure_loaded)
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    yaml_text = str((payload or {}).get("config_yaml") or "").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="config_yaml is required")
    yaml_text = _normalize_project_config_yaml(
        project_id,
        yaml_text,
        project.get("name") or project_id,
        project.get("owner_user_id"),
    )
    _compat_project_configs[project_id] = yaml_text
    try:
        await asyncio.to_thread(_compat_save_project_yaml_text, project_id, yaml_text)
        _clear_project_mapping_cache(project_id)
    except Exception as exc:
        logger.warning("Failed to persist project config for %s: %s", project_id, exc)
    project["updated_at"] = _compat_now_iso()
    await asyncio.to_thread(_compat_save_store)
    return {
        "status": "saved",
        "project_id": project_id,
        "yaml_path": str(_compat_project_yaml_path(project_id).resolve()).replace('\\\\', '/'),
        "warnings": [],
    }


def _extract_snapshot_connectors(snapshot_obj: Any) -> List[str]:
    """Extract source/target connector names from snapshot SML payload."""
    connectors: List[str] = []
    sml = getattr(snapshot_obj, "sml_blob", {}) or {}
    if not isinstance(sml, dict):
        return connectors

    for key in ("source_type", "source", "adapter", "connector"):
        val = sml.get(key)
        if isinstance(val, str) and val.strip():
            connectors.append(val.strip())
            break

    target_val = sml.get("target_type") or sml.get("target")
    if isinstance(target_val, str) and target_val.strip():
        connectors.append(target_val.strip())
    elif isinstance(sml.get("targets"), list):
        for tgt in sml.get("targets"):
            if isinstance(tgt, dict):
                t = str(tgt.get("type") or "").strip()
                if t:
                    connectors.append(t)

    deduped: List[str] = []
    seen = set()
    for c in connectors:
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped


def _snapshot_graph_payload(snapshot_obj: Any, model_name: str, include_system_tables: bool = False) -> Dict[str, Any]:
    """Build React-Flow compatible graph payload from a snapshot object."""
    snapshot_id = getattr(snapshot_obj, "snapshot_id", "")
    sml = getattr(snapshot_obj, "sml_blob", {}) or {}
    if not isinstance(sml, dict):
        sml = {}

    def _safe_id(raw: str) -> str:
        return re.sub(r"[^a-zA-Z0-9_\-:.]", "_", str(raw or "unknown"))

    system_prefixes = (
        "information_schema.",
        "pg_",
        "sqlite_",
        "duckdb_",
        "sys.",
        "__",
    )

    def _is_system_table(table_name: str) -> bool:
        val = str(table_name or "").strip().lower()
        if not val:
            return False
        return val.startswith(system_prefixes)

    def _qualify(schema_name: str, table_name: str) -> str:
        schema_name = str(schema_name or "").strip()
        table_name = str(table_name or "").strip()
        if not table_name:
            return ""
        if "." in table_name:
            return table_name
        if schema_name:
            return f"{schema_name}.{table_name}"
        return table_name

    def _card_symbol(card: str) -> str:
        c = str(card or "").strip().lower().replace("_", "-")
        mapping = {
            "one-to-one": "1:1",
            "one-to-many": "1:*",
            "many-to-one": "*:1",
            "many-to-many": "*:*",
        }
        return mapping.get(c, c or "?:?")

    detected_model = str(sml.get("model_name") or model_name or getattr(snapshot_obj, "project_id", "") or "model").strip()
    model_node_id = f"model-{_safe_id(detected_model)}"

    nodes: List[Dict[str, Any]] = [{
        "id": model_node_id,
        "type": "modelNode",
        "position": {"x": 400, "y": 150},
        "data": {
            "label": detected_model,
            "model_id": detected_model,
            "workspace_id": str(sml.get("workspace_id") or ""),
            "description": str(sml.get("description") or ""),
            "status": "valid",
            "nodeType": "model",
        },
    }]
    edges: List[Dict[str, Any]] = []

    table_nodes: Dict[str, str] = {}
    excluded_system_tables = 0

    datasets = sml.get("datasets", [])
    if not isinstance(datasets, list):
        datasets = []

    entities = sml.get("entities", {})
    if isinstance(entities, dict):
        for ent_name, ent_def in entities.items():
            if not isinstance(ent_def, dict):
                continue
            datasets.append({
                "name": ent_name,
                "table": ent_def.get("table") or ent_name,
                "schema": ent_def.get("schema") or ent_def.get("database_schema") or "PUBLIC",
                "source_type": ent_def.get("source_type") or "snapshot",
                "columns": ent_def.get("columns") or [],
            })

    for idx, ds in enumerate(datasets):
        if not isinstance(ds, dict):
            continue
        schema = str(ds.get("source_schema") or ds.get("schema") or ds.get("database_schema") or "PUBLIC")
        table = str(
            ds.get("source_table")
            or ds.get("table")
            or ds.get("name")
            or ds.get("unique_name")
            or ds.get("label")
            or f"table_{idx}"
        )
        qualified = _qualify(schema, table)

        if qualified in table_nodes and table.startswith("unknown"):
            qualified = _qualify(schema, f"{table}_{idx + 1}")

        if not include_system_tables and _is_system_table(qualified):
            excluded_system_tables += 1
            continue

        if qualified in table_nodes:
            continue

        table_node_id = f"table-{_safe_id(qualified)}"
        table_nodes[qualified] = table_node_id
        columns = ds.get("columns") if isinstance(ds.get("columns"), list) else []

        nodes.append({
            "id": table_node_id,
            "type": "tableNode",
            "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
            "data": {
                "label": qualified,
                "table_name": table,
                "schema": schema,
                "source_type": str(ds.get("source_type") or "snapshot"),
                "columns": columns,
                "nodeType": "table",
                "status": "valid",
            },
        })

        edges.append({
            "id": f"e-{table_node_id}-{model_node_id}",
            "source": table_node_id,
            "target": model_node_id,
            "animated": True,
            "style": {"stroke": "#22C55E"},
        })

    measures = sml.get("metrics", sml.get("measures", []))
    if not isinstance(measures, list):
        measures = []

    for midx, measure in enumerate(measures):
        if not isinstance(measure, dict):
            continue
        measure_name = str(measure.get("name") or measure.get("unique_name") or measure.get("label") or f"measure_{midx}")
        measure_node_id = f"measure-{_safe_id(detected_model)}-{midx}"
        nodes.append({
            "id": measure_node_id,
            "type": "measureNode",
            "position": {"x": 120 + midx * 260, "y": 320},
            "data": {
                "label": measure_name,
                "expression": str(measure.get("expression") or ""),
                "data_type": str(measure.get("data_type") or measure.get("format_string") or ""),
                "parent_model": detected_model,
                "nodeType": "measure",
            },
        })
        edges.append({
            "id": f"e-{model_node_id}-{measure_node_id}",
            "source": model_node_id,
            "target": measure_node_id,
            "animated": False,
            "style": {"stroke": "#EAB308"},
        })

    relationships = sml.get("relationships", [])
    if not isinstance(relationships, list):
        relationships = []

    for ridx, rel in enumerate(relationships):
        if not isinstance(rel, dict):
            continue

        from_schema = rel.get("from_schema") or rel.get("source_schema") or ""
        to_schema = rel.get("to_schema") or rel.get("target_schema") or ""
        from_table = rel.get("from_table") or rel.get("from_model") or rel.get("from") or rel.get("source")
        to_table = rel.get("to_table") or rel.get("to_model") or rel.get("to") or rel.get("target")

        from_key = _qualify(from_schema, str(from_table or "")).strip()
        to_key = _qualify(to_schema, str(to_table or "")).strip()

        if not from_key or not to_key:
            continue
        if (not include_system_tables) and (_is_system_table(from_key) or _is_system_table(to_key)):
            continue

        if from_key not in table_nodes:
            nid = f"table-{_safe_id(from_key)}"
            table_nodes[from_key] = nid
            nodes.append({
                "id": nid,
                "type": "tableNode",
                "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
                "data": {
                    "label": from_key,
                    "table_name": from_key,
                    "schema": from_schema or "PUBLIC",
                    "source_type": "snapshot",
                    "columns": [],
                    "nodeType": "table",
                    "status": "broken",
                },
            })

        if to_key not in table_nodes:
            nid = f"table-{_safe_id(to_key)}"
            table_nodes[to_key] = nid
            nodes.append({
                "id": nid,
                "type": "tableNode",
                "position": {"x": 120 + (len(table_nodes) - 1) * 260, "y": 0},
                "data": {
                    "label": to_key,
                    "table_name": to_key,
                    "schema": to_schema or "PUBLIC",
                    "source_type": "snapshot",
                    "columns": [],
                    "nodeType": "table",
                    "status": "broken",
                },
            })

        cardinality = str(rel.get("cardinality") or rel.get("relationship_type") or rel.get("type") or "many-to-one")
        from_col = str(rel.get("from_column") or (rel.get("from_columns") or [""])[0] or rel.get("join_key") or "")
        to_col = str(rel.get("to_column") or (rel.get("to_columns") or [""])[0] or "")
        join_label = f"{from_col} → {to_col}" if from_col and to_col else from_col

        edges.append({
            "id": f"rel-{ridx}-{table_nodes[from_key]}-{table_nodes[to_key]}",
            "source": table_nodes[from_key],
            "target": table_nodes[to_key],
            "label": f"{_card_symbol(cardinality)}{f' • {join_label}' if join_label else ''}",
            "type": "smoothstep",
            "style": {"stroke": "#818CF8", "strokeDasharray": "6 3"},
            "data": {
                "cardinality": cardinality,
                "from_column": from_col,
                "to_column": to_col,
            },
        })

    return {
        "nodes": nodes,
        "edges": edges,
        "snapshot_id": snapshot_id,
        "model": model_name,
        "timestamp": getattr(snapshot_obj, "timestamp", None),
        "version_tag": getattr(snapshot_obj, "version_tag", None),
        "run_id": getattr(snapshot_obj, "run_id", None),
        "status": getattr(snapshot_obj, "status", "success"),
        "connectors": _extract_snapshot_connectors(snapshot_obj),
        "meta": {
            "tables_included": len(table_nodes),
            "system_tables_excluded": excluded_system_tables,
            "include_system_tables": include_system_tables,
        },
    }


async def graph_snapshots_compat(model_name: str):
    """Snapshot history for Explore time-machine (newest first)."""
    try:
        logger.info("[Explore] Snapshot list requested model=%s", model_name)
        snapshots = db_manager.list_snapshots(model_name, limit=200)
        logger.info("[Explore] Snapshot list resolved model=%s count=%s", model_name, len(snapshots or []))
        return [
            {
                "snapshot_id": s.snapshot_id,
                "timestamp": s.timestamp,
                "version_tag": s.version_tag or f"v{s.snapshot_id[:8]}",
                "status": s.status or "success",
                "duration_ms": s.duration_ms or 0,
                "model_name": model_name,
                "run_id": s.run_id,
                "initiated_by": s.initiated_by,
                "connectors": _extract_snapshot_connectors(s),
            }
            for s in snapshots
        ]
    except Exception as exc:
        logger.debug("Failed to list snapshots for %s: %s", model_name, exc)
        return []


async def graph_snapshot_compat(model_name: str, snapshot_id: str, include_system_tables: bool = False):
    """Load graph for a single snapshot."""
    try:
        logger.info(
            "[Explore] Snapshot graph requested model=%s snapshot_id=%s include_system_tables=%s",
            model_name,
            snapshot_id,
            include_system_tables,
        )
        snapshot = db_manager.get_snapshot(snapshot_id)
        if not snapshot:
            logger.warning("Snapshot %s not found", snapshot_id)
            return {"nodes": [], "edges": [], "snapshot_id": snapshot_id, "model": model_name}
        payload = _snapshot_graph_payload(snapshot, model_name, include_system_tables)
        logger.info(
            "[Explore] Snapshot graph ready model=%s snapshot_id=%s nodes=%s edges=%s",
            model_name,
            snapshot_id,
            len(payload.get("nodes") or []),
            len(payload.get("edges") or []),
        )
        return payload
    except Exception as exc:
        logger.debug("Failed to load snapshot graph %s: %s", snapshot_id, exc)
        return {"nodes": [], "edges": [], "snapshot_id": snapshot_id, "model": model_name}


async def compare_graph_snapshots_compat(
    model_name: str,
    from_snapshot_id: str,
    to_snapshot_id: str,
    include_system_tables: bool = False,
):
    """Compare two snapshots and return graph diff + tabular change details."""
    try:
        snap_from = db_manager.get_snapshot(from_snapshot_id)
        snap_to = db_manager.get_snapshot(to_snapshot_id)

        if not snap_from or not snap_to:
            return {
                "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
                "changes": [],
                "relationships": [],
                "styled_graph": {"nodes": [], "edges": []},
            }

        from_graph = _snapshot_graph_payload(snap_from, model_name, include_system_tables)
        to_graph = _snapshot_graph_payload(snap_to, model_name, include_system_tables)

        from_nodes = from_graph.get("nodes", [])
        to_nodes = to_graph.get("nodes", [])
        from_edges = from_graph.get("edges", [])
        to_edges = to_graph.get("edges", [])

        def _node_key(n: Dict[str, Any]) -> str:
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            return f"{d.get('nodeType', '')}:{str(d.get('label') or n.get('id') or '')}"

        def _edge_key(e: Dict[str, Any], node_map: Dict[str, Dict[str, Any]]) -> str:
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            d_src = (node_map.get(src, {}).get("data") or {}) if isinstance(node_map.get(src, {}), dict) else {}
            d_tgt = (node_map.get(tgt, {}).get("data") or {}) if isinstance(node_map.get(tgt, {}), dict) else {}
            src_lbl = str(d_src.get("label") or src)
            tgt_lbl = str(d_tgt.get("label") or tgt)
            rel_lbl = str(e.get("label") or "")
            return f"{src_lbl}->{tgt_lbl}:{rel_lbl}"

        from_node_map = {_node_key(n): n for n in from_nodes}
        to_node_map = {_node_key(n): n for n in to_nodes}
        from_id_map = {str(n.get("id")): n for n in from_nodes}
        to_id_map = {str(n.get("id")): n for n in to_nodes}
    except Exception as e:
        logger.error(f"Error comparing snapshots: {str(e)}")
        return {
            "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
            "changes": [],
            "relationships": [],
            "styled_graph": {"nodes": [], "edges": []},
        }

        changes: List[Dict[str, Any]] = []

        added_keys = sorted(set(to_node_map.keys()) - set(from_node_map.keys()))
        removed_keys = sorted(set(from_node_map.keys()) - set(to_node_map.keys()))
        common_keys = sorted(set(from_node_map.keys()) & set(to_node_map.keys()))

        for k in added_keys:
            n = to_node_map[k]
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            changes.append({
                "change_type": "added",
                "entity_type": d.get("nodeType") or "node",
                "entity_name": d.get("label") or n.get("id"),
                "details": "Entity added",
                "from_value": None,
                "to_value": d,
            })

        for k in removed_keys:
            n = from_node_map[k]
            d = n.get("data", {}) if isinstance(n.get("data"), dict) else {}
            changes.append({
                "change_type": "removed",
                "entity_type": d.get("nodeType") or "node",
                "entity_name": d.get("label") or n.get("id"),
                "details": "Entity removed",
                "from_value": d,
                "to_value": None,
            })

        for k in common_keys:
            n1 = from_node_map[k]
            n2 = to_node_map[k]
            d1 = n1.get("data", {}) if isinstance(n1.get("data"), dict) else {}
            d2 = n2.get("data", {}) if isinstance(n2.get("data"), dict) else {}
            left = {
                "columns": d1.get("columns") or [],
                "expression": d1.get("expression") or "",
                "data_type": d1.get("data_type") or "",
                "status": d1.get("status") or "",
            }
            right = {
                "columns": d2.get("columns") or [],
                "expression": d2.get("expression") or "",
                "data_type": d2.get("data_type") or "",
                "status": d2.get("status") or "",
            }
            if json.dumps(left, sort_keys=True, default=str) != json.dumps(right, sort_keys=True, default=str):
                changes.append({
                    "change_type": "modified",
                    "entity_type": d2.get("nodeType") or d1.get("nodeType") or "node",
                    "entity_name": d2.get("label") or d1.get("label") or n2.get("id"),
                    "details": "Schema/metric definition changed",
                    "from_value": left,
                    "to_value": right,
                })
            else:
                changes.append({
                    "change_type": "unchanged",
                    "entity_type": d2.get("nodeType") or d1.get("nodeType") or "node",
                    "entity_name": d2.get("label") or d1.get("label") or n2.get("id"),
                    "details": "No changes detected",
                    "from_value": left,
                    "to_value": right,
                })

        from_edge_map = {_edge_key(e, from_id_map): e for e in from_edges}
        to_edge_map = {_edge_key(e, to_id_map): e for e in to_edges}
        rel_changes: List[Dict[str, Any]] = []

        for k in sorted(set(to_edge_map.keys()) - set(from_edge_map.keys())):
            rel_changes.append({"change_type": "added", "relationship": k})
        for k in sorted(set(from_edge_map.keys()) - set(to_edge_map.keys())):
            rel_changes.append({"change_type": "removed", "relationship": k})

        # Build styled graph (base = to_graph), append removed entities as ghost nodes/edges
        styled_nodes = []
        for n in to_nodes:
            key = _node_key(n)
            status = "unchanged"
            if key in added_keys:
                status = "added"
            elif any(c.get("change_type") == "modified" and c.get("entity_name") == (n.get("data", {}) or {}).get("label") for c in changes):
                status = "modified"
            nn = dict(n)
            nn["data"] = {**(n.get("data") if isinstance(n.get("data"), dict) else {}), "diffStatus": status}
            styled_nodes.append(nn)

        for k in removed_keys:
            n = from_node_map[k]
            ghost = dict(n)
            ghost["id"] = f"removed-{n.get('id')}"
            ghost["data"] = {**(n.get("data") if isinstance(n.get("data"), dict) else {}), "diffStatus": "removed"}
            styled_nodes.append(ghost)

        styled_edges = []
        for e in to_edges:
            key = _edge_key(e, to_id_map)
            status = "added" if key not in from_edge_map else "unchanged"
            ee = dict(e)
            ee["data"] = {**(e.get("data") if isinstance(e.get("data"), dict) else {}), "diffStatus": status}
            styled_edges.append(ee)

        for k in sorted(set(from_edge_map.keys()) - set(to_edge_map.keys())):
            e = dict(from_edge_map[k])
            src = str(e.get("source") or "")
            tgt = str(e.get("target") or "")
            if src in from_id_map and _node_key(from_id_map[src]) in removed_keys:
                e["source"] = f"removed-{src}"
            if tgt in from_id_map and _node_key(from_id_map[tgt]) in removed_keys:
                e["target"] = f"removed-{tgt}"
            e["id"] = f"removed-{e.get('id') or k}"
            e["data"] = {**(e.get("data") if isinstance(e.get("data"), dict) else {}), "diffStatus": "removed"}
            styled_edges.append(e)

        summary = {
            "added": len([c for c in changes if c.get("change_type") == "added"]),
            "removed": len([c for c in changes if c.get("change_type") == "removed"]),
            "modified": len([c for c in changes if c.get("change_type") == "modified"]),
            "unchanged": len([c for c in changes if c.get("change_type") == "unchanged"]),
            "relationships_added": len([r for r in rel_changes if r.get("change_type") == "added"]),
            "relationships_removed": len([r for r in rel_changes if r.get("change_type") == "removed"]),
        }

        return {
            "summary": summary,
            "changes": changes,
            "relationships": rel_changes,
            "styled_graph": {
                "nodes": styled_nodes,
                "edges": styled_edges,
                "meta": {
                    "from_snapshot_id": from_snapshot_id,
                    "to_snapshot_id": to_snapshot_id,
                    "include_system_tables": include_system_tables,
                },
            },
        }
    except Exception as e:
        logger.debug(f"Error comparing snapshots: {str(e)}")
        return {
            "summary": {"added": 0, "removed": 0, "modified": 0, "unchanged": 0},
            "changes": [],
            "relationships": [],
            "styled_graph": {"nodes": [], "edges": []},
        }
