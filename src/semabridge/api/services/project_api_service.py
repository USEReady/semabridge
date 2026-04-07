from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
import time as _time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import BackgroundTasks, File, HTTPException, Query, UploadFile
from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.api.services.version_control_service import VersionControlService
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.utils.logger import setup_logging

from semabridge.api.services.core_api_service import (
    _discovery_cache,
    _last_snapshot_hash,
    _normalize_yaml_windows_path_fields,
    _resolve_models_path,
    sync_models,
)

setup_logging(level="INFO")
logger = logging.getLogger("semabridge.api")
db_manager = ModelRepository()
engine = ExecutionEngine(db_manager=db_manager)
settings = get_settings()
scheduler_service = SchedulerService()
version_control_service = VersionControlService(
    db_manager=db_manager,
    models_path_resolver=_resolve_models_path,
    hash_tracker=_last_snapshot_hash,
)

_compat_projects: Dict[str, Dict[str, Any]] = {}
_compat_project_configs: Dict[str, str] = {}
_compat_project_runs: Dict[str, List[Dict[str, Any]]] = {}
_compat_folders: Dict[str, Dict[str, Any]] = {}
_compat_mappings: Dict[str, Dict[str, Any]] = {}
_compat_project_schedules: Dict[str, Dict[str, Any]] = {}
_compat_job_config: Dict[str, Any] = {
    "schedule_type": "Manual Trigger Only",
    "cron": "0 0 * * *",
    "timezone": "UTC",
    "enabled": False,
    "mode": "local",
}
_compat_store_loaded: bool = False
def _compat_now_iso() -> str:
    return datetime.utcnow().isoformat()

def _compat_store_path() -> Path:
    p = Path("config/.semabridge_compat_store.json")
    if p.exists():
        return p
    legacy = Path(".semabridge_compat_store.json")
    if legacy.exists():
        return legacy
    return p

def _compat_save_store() -> None:
    payload = {
        "projects": _compat_projects,
        "project_configs": _compat_project_configs,
        "project_runs": _compat_project_runs,
        "folders": _compat_folders,
        "mappings": _compat_mappings,
        "project_schedules": _compat_project_schedules,
        "job_config": _compat_job_config,
    }
    try:
        _compat_store_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist compat store: %s", exc)

def _compat_clear_project_schedule(project_id: str) -> None:
    _compat_project_schedules.pop(str(project_id), None)
    _compat_save_store()

def _compat_load_store() -> None:
    global _compat_store_loaded
    if _compat_store_loaded:
        return

    p = _compat_store_path()
    if not p.exists():
        _compat_store_loaded = True
        return

    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        if isinstance(data.get("projects"), dict):
            _compat_projects.update(data.get("projects") or {})
        if isinstance(data.get("project_configs"), dict):
            _compat_project_configs.update(data.get("project_configs") or {})
        if isinstance(data.get("project_runs"), dict):
            _compat_project_runs.update(data.get("project_runs") or {})
        if isinstance(data.get("folders"), dict):
            _compat_folders.update(data.get("folders") or {})
        if isinstance(data.get("mappings"), dict):
            _compat_mappings.update(data.get("mappings") or {})
        if isinstance(data.get("project_schedules"), dict):
            _compat_project_schedules.update(data.get("project_schedules") or {})
        if isinstance(data.get("job_config"), dict):
            _compat_job_config.update(data.get("job_config") or {})
    except Exception as exc:
        logger.warning("Failed to load compat store: %s", exc)
    finally:
        _compat_store_loaded = True

def _compat_bootstrap_projects_from_orm() -> None:
    """Seed compatibility project cache from persisted ORM projects when memory is empty."""
    if _compat_projects:
        return

    try:
        from semabridge.repository.orm.models import Project
        from sqlalchemy import select

        session = db_manager._session()
        try:
            rows = session.execute(
                select(Project).order_by(Project.last_updated.desc().nullslast()).limit(500)
            ).scalars().all()
        finally:
            session.close()

        for row in rows:
            pid = str(row.project_id or "").strip()
            if not pid:
                continue
            project = {
                "id": pid,
                "project_id": pid,
                "name": _compat_clean_project_name(row.name, f"Project {pid[-6:]}"),
                "description": "",
                "source": row.adapter or "fabric",
                "adapter": row.adapter or "fabric",
                "workspace_id": row.workspace_id or "",
                "target_type": "snowflake",
                "folder_id": None,
                "status": "draft",
                "created_at": _compat_now_iso(),
                "updated_at": _compat_now_iso(),
            }
            _compat_projects[pid] = project
            _compat_project_configs.setdefault(pid, _compat_load_repo_yaml_text() or _compat_default_project_yaml(project))
            _compat_project_runs.setdefault(pid, [])
    except Exception as exc:
        logger.debug("ORM project bootstrap skipped: %s", exc)

def _compat_bootstrap_project_from_repo_yaml() -> None:
    """Ensure at least one visible project from root semabridge.yaml if present."""
    if _compat_projects:
        return

    yaml_text = _compat_load_repo_yaml_text()
    if not yaml_text:
        return

    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception:
        parsed = {}

    pname = _compat_clean_project_name(parsed.get("project_name"), "SemaBridge Project")
    pid_hash = hashlib.sha1(pname.encode("utf-8")).hexdigest()[:12]
    project_id = f"proj-{pid_hash}"

    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    target_cfg = parsed.get("target") if isinstance(parsed.get("target"), dict) else {}

    project = {
        "id": project_id,
        "project_id": project_id,
        "name": pname,
        "description": str(parsed.get("description") or ""),
        "source": source_cfg.get("type") or "fabric",
        "adapter": source_cfg.get("type") or "fabric",
        "workspace_id": str(source_cfg.get("workspace_id") or ""),
        "target_type": target_cfg.get("type") or "snowflake",
        "folder_id": None,
        "status": "draft",
        "created_at": _compat_now_iso(),
        "updated_at": _compat_now_iso(),
    }
    _compat_projects[project_id] = project
    _compat_project_configs[project_id] = yaml_text
    _compat_project_runs.setdefault(project_id, [])

def _compat_ensure_loaded() -> None:
    _compat_load_store()
    _compat_bootstrap_projects_from_orm()
    _compat_bootstrap_project_from_repo_yaml()

def _compat_repo_yaml_path() -> Path:
    from semabridge.core.config_loader import get_project_file_path
    return get_project_file_path("semabridge.yaml")

def _compat_load_repo_yaml_text() -> str:
    try:
        p = _compat_repo_yaml_path()
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if text.strip():
                return text
    except Exception:
        pass
    return ""

def _compat_save_repo_yaml_text(yaml_text: str) -> None:
    p = _compat_repo_yaml_path()
    p.write_text(yaml_text, encoding="utf-8")

def _compat_clean_project_name(raw: Any, fallback: str = "Untitled Project") -> str:
    if isinstance(raw, str):
        text = raw.strip()
        if text and text.lower() != "[object object]":
            return text
    return fallback

def _compat_project_payload(project_id: str, payload: dict) -> Dict[str, Any]:
    source_obj = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    target_obj = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    targets_list = payload.get("targets") if isinstance(payload.get("targets"), list) else []
    first_target_obj = targets_list[0] if targets_list and isinstance(targets_list[0], dict) else {}
    project_name = _compat_clean_project_name(payload.get("name"), f"Project {project_id[-6:]}")
    account_id = (
        payload.get("account_id")
        or payload.get("selectedAccountId")
        or source_obj.get("identity_id")
        or payload.get("identity_id")
        or ""
    )
    return {
        "id": project_id,
        "project_id": project_id,
        "name": project_name,
        "description": payload.get("description") or "",
        "source": source_obj.get("type") or payload.get("source_type") or "fabric",
        "adapter": source_obj.get("type") or payload.get("source_type") or "fabric",
        "account_id": str(account_id) if account_id else None,
        "workspace_id": source_obj.get("workspace_id") or "",
        "target_type": target_obj.get("type") or first_target_obj.get("type") or payload.get("target_type") or "snowflake",
        "folder_id": payload.get("folder_id"),
        "status": "draft",
        "created_at": _compat_now_iso(),
        "updated_at": _compat_now_iso(),
    }

def _compat_default_project_yaml(project: Dict[str, Any]) -> str:
    name = _compat_clean_project_name(project.get("name"), "Untitled Project").replace('"', '\\"')
    src = project.get("source") or "fabric"
    target = project.get("target_type") or "snowflake"
    account_id = str(project.get("account_id") or "").strip()
    lines = [
        f'project_name: "{name}"',
        "source:",
        f"  type: {src}",
    ]
    if account_id and src == "fabric":
        account_id_escaped = account_id.replace('"', '\\"')
        lines.append(f'  identity_id: "{account_id_escaped}"')
    workspace_id = project.get("workspace_id")
    if workspace_id:
        workspace_id_escaped = str(workspace_id).replace('"', '\\"')
        lines.append(f'  workspace_id: "{workspace_id_escaped}"')
    lines.extend([
        '  model: "*"',
        "target:",
        f"  type: {target}",
        "ui:",
        '  output_format: "osi"',
    ])
    return "\n".join(lines)

async def list_projects_compat():
    """Compatibility: newfrontend expects a projects collection."""
    _compat_ensure_loaded()
    deduped: Dict[str, Dict[str, Any]] = {}
    for p in _compat_projects.values():
        pid = str(p.get("id") or p.get("project_id") or "").strip()
        if not pid:
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
    try:
        _compat_ensure_loaded()
        payload = request or {}
        payload_name = _compat_clean_project_name(payload.get("name"), "")
        src = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        src_type = (src.get("type") or payload.get("source_type") or "fabric").strip().lower()
        ws_id = str(src.get("workspace_id") or payload.get("workspace_id") or "").strip()

        logger.info(
            "create_project_compat called: name=%s source=%s workspace_id=%s keys=%s",
            payload_name or "<empty>",
            src_type,
            ws_id or "<empty>",
            sorted(payload.keys()) if isinstance(payload, dict) else "<non-dict>",
        )

        # Idempotency guard: if a project with same name/source/workspace already exists,
        # return it instead of creating a duplicate entry.
        if payload_name:
            for existing in _compat_projects.values():
                ex_name = _compat_clean_project_name(existing.get("name"), "")
                ex_src = str(existing.get("source") or existing.get("adapter") or "").strip().lower()
                ex_ws = str(existing.get("workspace_id") or "").strip()
                if ex_name == payload_name and ex_src == src_type and ex_ws == ws_id:
                    existing["updated_at"] = _compat_now_iso()
                    _compat_projects[str(existing.get("id") or existing.get("project_id"))] = existing
                    logger.info("create_project_compat reused existing project_id=%s", existing.get("id") or existing.get("project_id"))
                    return existing

        project_id = str(payload.get("id") or payload.get("project_id") or f"proj-{int(_time.time() * 1000)}")
        project = _compat_project_payload(project_id, payload)
        _compat_projects[project_id] = project

        config_yaml = payload.get("config_yaml")
        if isinstance(config_yaml, str) and config_yaml.strip():
            _compat_project_configs[project_id] = config_yaml
            try:
                _compat_save_repo_yaml_text(config_yaml)
            except Exception as exc:
                logger.warning("Failed to persist project config to semabridge.yaml: %s", exc)
        else:
            repo_yaml = _compat_load_repo_yaml_text()
            _compat_project_configs.setdefault(project_id, repo_yaml or _compat_default_project_yaml(project))

        _compat_project_runs.setdefault(project_id, [])
        _compat_save_store()
        logger.info("create_project_compat created project_id=%s", project_id)
        return project
    except Exception:
        logger.exception("create_project_compat failed")
        raise

async def get_project_compat(project_id: str):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert for stale in-memory cache after reload.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
        _compat_save_store()
    return project

async def patch_project_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    for key in ("name", "description", "folder_id", "status"):
        if key in (payload or {}):
            project[key] = payload.get(key)

    if isinstance(payload.get("source"), dict):
        project["source"] = payload["source"].get("type") or project.get("source")
        project["adapter"] = project["source"]
        if "workspace_id" in payload["source"]:
            project["workspace_id"] = payload["source"].get("workspace_id")

    if isinstance(payload.get("target"), dict):
        project["target_type"] = payload["target"].get("type") or project.get("target_type")

    if isinstance(payload.get("targets"), list) and payload.get("targets"):
        first_target = payload["targets"][0] if isinstance(payload["targets"][0], dict) else {}
        if first_target.get("type"):
            project["target_type"] = first_target.get("type")

    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    _compat_save_store()
    return project

async def delete_project_compat(project_id: str):
    _compat_ensure_loaded()
    _compat_projects.pop(project_id, None)
    _compat_project_configs.pop(project_id, None)
    _compat_project_runs.pop(project_id, None)
    _compat_save_store()
    return Response(status_code=204)

async def get_project_config_compat(project_id: str):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert: keep UI editable even if project cache was reset.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
    # IMPORTANT: prefer per-project config first so "Copy Presets" can load
    # different YAMLs for different projects. Fall back to repository file only
    # when the project has no stored config.
    yaml_text = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(project)
    _compat_project_configs[project_id] = yaml_text
    return {"project_id": project_id, "config_yaml": yaml_text, "yaml_path": str(_compat_repo_yaml_path().resolve()).replace('\\\\', '/')}

async def save_project_config_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        # Compatibility upsert: allow saving config even when only project_id is known.
        project = _compat_project_payload(project_id, {
            "id": project_id,
            "name": f"Recovered {project_id}",
            "source": {"type": "fabric"},
            "target": {"type": "snowflake"},
        })
        _compat_projects[project_id] = project
    yaml_text = str((payload or {}).get("config_yaml") or "").strip()
    if not yaml_text:
        raise HTTPException(status_code=400, detail="config_yaml is required")
    _compat_project_configs[project_id] = yaml_text
    try:
        parsed = yaml.safe_load(yaml_text) or {}
    except Exception:
        parsed = {}
    if isinstance(parsed, dict):
        source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        account_id = str(source_cfg.get("identity_id") or "").strip()
        if account_id and project_id in _compat_projects:
            _compat_projects[project_id]["account_id"] = account_id
    try:
        _compat_save_repo_yaml_text(yaml_text)
    except Exception as exc:
        logger.warning("Failed to persist project config to semabridge.yaml: %s", exc)
    project["updated_at"] = _compat_now_iso()
    _compat_save_store()
    return {
        "status": "saved",
        "project_id": project_id,
        "yaml_path": str(_compat_repo_yaml_path().resolve()).replace('\\\\', '/'),
        "warnings": [],
    }

def _compat_set_project_pbix_path(project_id: str, pbix_path: str) -> None:
    """Persist PBIX path in project metadata + project semabridge.yaml blob."""
    _compat_ensure_loaded()
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    normalized_pbix_path = str(Path(pbix_path).resolve()).replace('\\\\', '/')
    project["pbix_file_path"] = normalized_pbix_path
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project

    yaml_text = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(project)
    parsed = yaml.safe_load(yaml_text) if yaml_text else {}
    if not isinstance(parsed, dict):
        parsed = {}
    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
    source_cfg["type"] = "pbix"
    source_cfg["pbix_path"] = normalized_pbix_path
    source_cfg["pbix_file_path"] = normalized_pbix_path
    parsed["source"] = source_cfg

    rebuilt = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)
    _compat_project_configs[project_id] = rebuilt
    _compat_save_store()

def _save_uploaded_pbix_file(upload: UploadFile, target_dir: Path) -> Path:
    """Store an uploaded PBIX file in the target directory and return absolute path."""
    filename = str(upload.filename or "").strip()
    if not filename.lower().endswith(".pbix"):
        raise HTTPException(status_code=400, detail="Only .pbix files are supported")

    safe_name = Path(filename).name
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{uuid.uuid4().hex}_{safe_name}"

    content = upload.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    destination.write_bytes(content)
    return destination.resolve()

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
                "summary": {"added": 0, "removed": 0, "modified": 0},
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
    except Exception as exc:
        logger.debug("Failed to compare snapshots %s -> %s: %s", from_snapshot_id, to_snapshot_id, exc)
        return {
            "summary": {"added": 0, "removed": 0, "modified": 0},
            "changes": [],
            "relationships": [],
            "styled_graph": {"nodes": [], "edges": []},
        }

async def get_project_runs_compat(project_id: str):
    _compat_ensure_loaded()
    return _compat_project_runs.get(project_id, [])

def _extract_source_type_from_project_cfg(project_cfg: str, fallback: str = "fabric") -> str:
    try:
        parsed = yaml.safe_load(project_cfg) or {}
        source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
        source_type = str(source_cfg.get("type") or fallback).strip().lower()
        return source_type or fallback
    except Exception:
        return fallback

def _collect_step_logs(summary: Dict[str, Any], prefix: str = "") -> List[str]:
    lines: List[str] = []
    steps = summary.get("steps_completed") if isinstance(summary, dict) else []
    errors = summary.get("errors") if isinstance(summary, dict) else []

    for step in steps or []:
        step_no = step.get("step_number", "?")
        step_name = step.get("step_name") or "Unknown"
        step_status = str(step.get("status") or "info").upper()
        detail = f" - {step.get('message')}" if step.get("message") else ""
        prefix_text = f"{prefix} " if prefix else ""
        lines.append(f"{step_status} {prefix_text}Stage {step_no}: {step_name}{detail}")

    for err in errors or []:
        step_no = err.get("step_number", "?")
        step_name = err.get("step_name") or "Execution"
        msg = err.get("message") or "Unknown error"
        prefix_text = f"{prefix} " if prefix else ""
        lines.append(f"ERROR {prefix_text}Stage {step_no} ({step_name}) - {msg}")

    return lines

def _build_run_logs(sync_result: Dict[str, Any]) -> List[str]:
    logs: List[str] = []
    summary = sync_result.get("summary") if isinstance(sync_result, dict) else {}
    logs.extend(_collect_step_logs(summary if isinstance(summary, dict) else {}))

    for result in (sync_result.get("results") or []):
        if not isinstance(result, dict):
            continue
        model_name = str(result.get("model") or "Model")
        model_summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        logs.extend(_collect_step_logs(model_summary, f"[{model_name}]"))

    return logs

def _build_stage_states(sync_result: Dict[str, Any]) -> List[Dict[str, str]]:
    stage_defaults: List[Dict[str, str]] = [
        {"id": "extraction", "label": "Extraction", "status": "pending"},
        {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
        {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
        {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
    ]

    summary = sync_result.get("summary") if isinstance(sync_result, dict) else {}
    steps = summary.get("steps_completed") if isinstance(summary, dict) else []
    if not isinstance(steps, list):
        return stage_defaults

    status_by_stage = {item["id"]: item["status"] for item in stage_defaults}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_number = int(step.get("step_number") or 0)
        normalized = str(step.get("status") or "pending").lower()
        if step_number == 4:
            status_by_stage["extraction"] = normalized
        elif step_number == 6:
            status_by_stage["osi_conversion"] = normalized
            status_by_stage["sml_generation"] = normalized
        elif step_number in (8, 9):
            status_by_stage["snowflake_deployment"] = normalized

    return [
        {"id": item["id"], "label": item["label"], "status": status_by_stage.get(item["id"], item["status"])}
        for item in stage_defaults
    ]

def _create_project_run(project_id: str, schedule_label: str = "Manual") -> tuple[dict, str, float]:
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    started = _time.time()
    run_id = f"run-{int(_time.time() * 1000)}"
    project_cfg = _compat_project_configs.get(project_id) or _compat_load_repo_yaml_text() or _compat_default_project_yaml(_compat_projects[project_id])
    account_id = str(_compat_projects[project_id].get("account_id") or "").strip()
    if account_id:
        try:
            parsed_cfg = yaml.safe_load(project_cfg) or {}
        except Exception:
            parsed_cfg = {}
        if isinstance(parsed_cfg, dict):
            source_cfg = parsed_cfg.get("source") if isinstance(parsed_cfg.get("source"), dict) else {}
            if source_cfg.get("type", "fabric").lower() == "fabric" and not source_cfg.get("identity_id"):
                source_cfg["identity_id"] = account_id
                parsed_cfg["source"] = source_cfg
                project_cfg = yaml.safe_dump(parsed_cfg, sort_keys=False, allow_unicode=False)
    source_type = _extract_source_type_from_project_cfg(
        project_cfg,
        fallback=str(_compat_projects[project_id].get("source") or "fabric").lower(),
    )

    run = {
        "run_id": run_id,
        "id": run_id,
        "project_id": project_id,
        "project_name": _compat_projects[project_id].get("name", project_id),
        "schedule": schedule_label,
        "status": "running",
        "source_type": source_type,
        "message": "Execution started.",
        "logs": ["LIVE Run queued. Waiting for execution engine..."],
        "stage_states": [
            {"id": "extraction", "label": "Extraction", "status": "running"},
            {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
            {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
            {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
        ],
        "duration_ms": 0,
        "started_at": _compat_now_iso(),
    }
    _compat_project_runs.setdefault(project_id, []).insert(0, run)
    _compat_save_store()
    return run, project_cfg, started

async def _perform_project_run(run: dict, project_cfg: str, started: float) -> dict:
    try:
        sync_result = await sync_models({"content": project_cfg})
        elapsed_ms = int((_time.time() - started) * 1000)
        run["duration_ms"] = elapsed_ms
        overall = str((sync_result or {}).get("status") or "").lower()
        if overall == "success":
            run["status"] = "success"
        elif overall == "partial":
            run["status"] = "warning"
        else:
            run["status"] = "failed"
        run["summary"] = (sync_result or {}).get("summary") or {}
        run["results"] = (sync_result or {}).get("results") or []
        run["models_synced"] = int((sync_result or {}).get("models_synced") or 0)
        run["total_models"] = int((sync_result or {}).get("total_models") or 0)
        run["logs"] = _build_run_logs(sync_result or {})
        run["stage_states"] = _build_stage_states(sync_result or {})
        if run["status"] == "success":
            run["message"] = "Execution completed successfully."
        elif run["status"] == "warning":
            run["message"] = "Execution completed with warnings."
        else:
            first_error = ""
            if run["summary"].get("errors"):
                first_error = str((run["summary"].get("errors") or [{}])[0].get("message") or "")
            run["message"] = first_error or "Execution failed."
    except Exception as exc:
        run["status"] = "failed"
        run["error"] = str(exc)
        run["message"] = str(exc)
        run["logs"] = [f"ERROR Execution failed: {exc}"]
        run["stage_states"] = [
            {"id": "extraction", "label": "Extraction", "status": "failed"},
            {"id": "osi_conversion", "label": "OSI Conversion", "status": "pending"},
            {"id": "sml_generation", "label": "SML Generation", "status": "pending"},
            {"id": "snowflake_deployment", "label": "Snowflake Deployment", "status": "pending"},
        ]
    _compat_save_store()
    return run

async def _execute_project_run(project_id: str, schedule_label: str = "Manual") -> dict:
    run, project_cfg, started = _create_project_run(project_id, schedule_label)
    return await _perform_project_run(run, project_cfg, started)

def _run_project_background(run: dict, project_cfg: str, started: float) -> None:
    import asyncio

    asyncio.run(_perform_project_run(run, project_cfg, started))

async def run_project_now_compat(project_id: str, background_tasks: BackgroundTasks):
    run, project_cfg, started = _create_project_run(project_id, "Manual")
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return {"run_id": run["id"], "status": "running", "message": "Sync started in background"}

async def list_folders_compat():
    """Compatibility: newfrontend expects a folders collection."""
    return list(_compat_folders.values())

async def create_folder_compat(payload: dict):
    folder_id = str((payload or {}).get("id") or (payload or {}).get("folder_id") or f"folder-{int(_time.time() * 1000)}")
    folder = {
        "id": folder_id,
        "folder_id": folder_id,
        "name": (payload or {}).get("name") or f"Folder {folder_id[-4:]}",
        "color": (payload or {}).get("color") or "#6366f1",
    }
    _compat_folders[folder_id] = folder
    return folder

async def rename_folder_compat(folder_id: str, payload: dict):
    folder = _compat_folders.get(folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if "name" in (payload or {}):
        folder["name"] = (payload or {}).get("name")
    if "color" in (payload or {}):
        folder["color"] = (payload or {}).get("color")
    _compat_folders[folder_id] = folder
    return folder

async def delete_folder_compat(folder_id: str):
    _compat_folders.pop(folder_id, None)
    for project in _compat_projects.values():
        if project.get("folder_id") == folder_id:
            project["folder_id"] = None
            project["updated_at"] = _compat_now_iso()
    return Response(status_code=204)

async def move_project_to_folder_compat(project_id: str, payload: dict):
    project = _compat_projects.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    folder_id = (payload or {}).get("folder_id")
    if folder_id is not None and folder_id not in _compat_folders:
        raise HTTPException(status_code=404, detail="Folder not found")
    project["folder_id"] = folder_id
    project["updated_at"] = _compat_now_iso()
    _compat_projects[project_id] = project
    return project

async def list_job_runs_compat():
    """Compatibility: return empty runs when scheduler APIs are absent."""
    all_runs: List[Dict[str, Any]] = []
    for runs in _compat_project_runs.values():
        all_runs.extend(runs)
    all_runs.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return all_runs

async def get_jobs_config_compat():
    """Compatibility: return non-failing default scheduler config."""
    return _compat_job_config

async def list_job_schedules_compat():
    _compat_ensure_loaded()
    items: List[Dict[str, Any]] = []
    for project_id, schedule in _compat_project_schedules.items():
        project = _compat_projects.get(project_id) or {}
        items.append({
            **schedule,
            "project_id": project_id,
            "project_name": project.get("name") or project_id,
        })
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items

async def get_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    schedule = scheduler_service.get_project_schedule(project_id) or _compat_project_schedules.get(project_id)
    if schedule:
        return schedule
    return {
        "project_id": project_id,
        "schedule_type": "manual",
        "enabled": False,
    }

async def save_project_schedule_compat(project_id: str, payload: dict):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        schedule = scheduler_service.save_project_schedule(project_id, payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if str(schedule.get("schedule_type") or "manual") == "manual":
        _compat_project_schedules.pop(project_id, None)
    else:
        _compat_project_schedules[project_id] = {
            "project_id": project_id,
            "schedule_type": schedule.get("schedule_type"),
            "cron": schedule.get("cron") or "",
            "date": schedule.get("date") or "",
            "time": schedule.get("time") or "",
            "timezone": schedule.get("timezone") or "UTC",
            "enabled": bool(schedule.get("enabled")),
            "scheduled_time": schedule.get("scheduled_time") or "",
            "next_run_at": schedule.get("next_run_at"),
            "created_at": schedule.get("created_at") or _compat_now_iso(),
        }

    _compat_job_config.update({
        "project_id": project_id,
        "schedule_type": str(schedule.get("schedule_type") or "manual"),
        "cron": schedule.get("cron") or "",
        "timezone": schedule.get("timezone") or "UTC",
        "enabled": bool(schedule.get("enabled")),
        "date": schedule.get("date") or "",
        "time": schedule.get("time") or "",
        "scheduled_time": schedule.get("scheduled_time") or "",
    })
    _compat_save_store()
    return schedule

async def delete_project_schedule_compat(project_id: str):
    _compat_ensure_loaded()
    if project_id not in _compat_projects:
        raise HTTPException(status_code=404, detail="Project not found")
    removed = scheduler_service.delete_project_schedule(project_id)
    _compat_project_schedules.pop(project_id, None)
    if not removed:
        _compat_save_store()
        return {"status": "deleted", "project_id": project_id, "schedule_type": "manual", "enabled": False}
    _compat_save_store()
    return {"status": "deleted", **removed}

async def update_jobs_config_compat(payload: dict):
    """Compatibility: accept schedule config updates without failing."""
    merged = {**_compat_job_config, **(payload or {})}
    _compat_job_config.update(merged)
    project_id = str((payload or {}).get("project_id") or "").strip()
    schedule_type = str((payload or {}).get("schedule_type") or "").strip()
    if project_id and schedule_type and project_id in _compat_projects:
        try:
            scheduler_service.save_project_schedule(project_id, payload or {})
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    _compat_save_store()
    return {"status": "saved", **merged}

async def trigger_job_compat(payload: dict, background_tasks: BackgroundTasks):
    """Compatibility: trigger a project job using the shared execution flow."""
    _compat_ensure_loaded()
    project_id = str((payload or {}).get("project_id") or "").strip()
    if not project_id and _compat_projects:
        project_id = next(iter(_compat_projects.keys()))
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    run, project_cfg, started = _create_project_run(project_id, "Manual")
    run["message"] = "Job trigger accepted."
    background_tasks.add_task(_run_project_background, run, project_cfg, started)
    return run

async def list_mappings_compat(project_id: Optional[str] = None):
    pid = str(project_id or "").strip()

    def _seed_for_project(seed_project_id: str) -> Dict[str, Any]:
        source_fields = [
            {"name": "transaction_id", "type": "uuid"},
            {"name": "amount", "type": "decimal"},
            {"name": "customer_ref", "type": "string"},
            {"name": "created_at", "type": "timestamp"},
            {"name": "status_code", "type": "integer"},
            {"name": "contact_email", "type": "string"},
            {"name": "region_id", "type": "integer"},
        ]
        target_fields = [
            {"name": "txn_id", "type": "varchar"},
            {"name": "sale_amount", "type": "float"},
            {"name": "customer_key", "type": "varchar"},
            {"name": "sale_date", "type": "date"},
            {"name": "order_status", "type": "integer"},
            {"name": "email_address", "type": "varchar"},
            {"name": "region_key", "type": "integer"},
        ]

        seeded = []
        for idx, src in enumerate(source_fields):
            tgt = target_fields[idx] if idx < len(target_fields) else None
            mapping_id = f"{seed_project_id}-map-{idx + 1}"
            existing = _compat_mappings.get(mapping_id, {})
            mapping = {
                "id": mapping_id,
                "project_id": seed_project_id,
                "source_field": src["name"],
                "source_type": src["type"],
                "target_field": tgt["name"] if tgt else None,
                "target_type": tgt["type"] if tgt else None,
                "status": existing.get("status") or "auto",
                "transform": existing.get("transform") or "",
                "validation": existing.get("validation") or "None",
            }
            _compat_mappings[mapping_id] = mapping
            seeded.append(mapping)

        return {
            "source_fields": source_fields,
            "target_fields": target_fields,
            "mappings": seeded,
        }

    if pid:
        mappings = [
            m for m in _compat_mappings.values()
            if str(m.get("project_id") or "") == pid
        ]
        if not mappings:
            return _seed_for_project(pid)
        return {
            "source_fields": [],
            "target_fields": [],
            "mappings": mappings,
        }

    # No project filter: ensure at least one dataset exists for UI usability.
    if not _compat_mappings:
        return _seed_for_project("default")

    return {
        "source_fields": [],
        "target_fields": [],
        "mappings": list(_compat_mappings.values()),
    }

async def auto_map_compat(payload: dict):
    project_id = str((payload or {}).get("project_id") or "default")
    data = await list_mappings_compat(project_id=project_id)
    mappings = data.get("mappings", []) if isinstance(data, dict) else []

    # Auto-map marks all available mappings as auto when triggered.
    for mapping in mappings:
        mapping_id = str(mapping.get("id") or "")
        if not mapping_id:
            continue
        mapping["status"] = "auto"
        _compat_mappings[mapping_id] = mapping

    return {
        "source_fields": data.get("source_fields", []) if isinstance(data, dict) else [],
        "target_fields": data.get("target_fields", []) if isinstance(data, dict) else [],
        "mappings": mappings,
        "status": "ok",
    }

async def update_mapping_compat(mapping_id: str, payload: dict):
    existing = _compat_mappings.get(mapping_id, {"id": mapping_id, "project_id": (payload or {}).get("project_id")})
    existing.update(payload or {})
    existing["id"] = mapping_id
    _compat_mappings[mapping_id] = existing
    return existing

async def delete_mappings_compat(project_id: Optional[str] = None):
    if project_id:
        for mapping_id in [k for k, v in _compat_mappings.items() if str(v.get("project_id") or "") == str(project_id)]:
            _compat_mappings.pop(mapping_id, None)
    else:
        _compat_mappings.clear()
    return Response(status_code=204)

async def list_model_versions(
    model_id: str = "",
    workspace_id: str = "",
    limit: int = 50,
):
    """
    List version history.  If model_id is blank, return versions
    for ALL models found in DuckDB.
    """
    try:
        all_versions = version_control_service.list_versions(
            model_id=model_id,
            workspace_id=workspace_id,
            limit=limit,
        )

        # Mark capability flags for DuckDB-backed entries
        for item in all_versions:
            item.setdefault("source", "model_versions")
            item.setdefault("can_compare", True)
            item.setdefault("can_rollback", True)

        # Add ORM-backed config version history (ModelVersionHistory)
        try:
            from semabridge.repository.orm.models import ModelVersionHistory
            from semabridge.repository.orm.session_factory import get_session_factory

            SessionLocal = get_session_factory()
            with SessionLocal() as session:
                query = session.query(ModelVersionHistory)
                if model_id:
                    query = query.filter(ModelVersionHistory.model_name == model_id)
                rows = query.order_by(ModelVersionHistory.applied_at.desc()).limit(limit).all()

            for row in rows:
                all_versions.append(
                    {
                        "version_id": f"cfgdb-{row.id}",
                        "model_id": row.model_name,
                        "workspace_id": workspace_id or "",
                        "author": "ui",
                        "timestamp": row.applied_at.isoformat() if row.applied_at else "",
                        "description": f"Config version {row.version_tag}",
                        "version_tag": row.version_tag,
                        "is_rollback": False,
                        "rollback_from_version": None,
                        "source": "config_db",
                        "can_compare": False,
                        "can_rollback": False,
                    }
                )
        except Exception as orm_exc:
            logger.debug(f"ORM model version history unavailable: {orm_exc}")

        # Sort by timestamp descending, cap at limit
        all_versions.sort(key=lambda v: v.get("timestamp", ""), reverse=True)
        return all_versions[:limit]
    except Exception as e:
        logger.error(f"list_model_versions failed: {e}", exc_info=True)
        from fastapi import HTTPException as _HTTPException
        raise _HTTPException(status_code=500, detail=f"Failed to load version history: {e}")

async def compare_model_versions(v1: str = "", v2: str = ""):
    """Compare two model versions and return tabular diff."""
    try:
        diffs = version_control_service.compare_versions(v1, v2)
        return {"changes": diffs}
    except Exception as e:
        logger.warning(f"compare_model_versions failed: {e}")
        return {"changes": []}

async def delete_model_versions(
    model_id: str,
    workspace_id: str = "",
):
    """Delete all version history rows for a model."""
    if not model_id:
        raise HTTPException(status_code=400, detail="model_id is required")
    try:
        deleted = version_control_service.delete_versions(
            model_id=model_id,
            workspace_id=workspace_id,
        )
        # Invalidate discovery cache entries for this model so stale data isn't served
        keys_to_clear = [k for k in _discovery_cache if model_id in k]
        for k in keys_to_clear:
            _discovery_cache.pop(k, None)
        logger.info(f"Deleted {deleted} version(s) for model={model_id} via API")
        return {"deleted": deleted, "model_id": model_id}
    except Exception as e:
        logger.warning(f"delete_model_versions failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def get_version_snapshot(version_id: str = ""):
    """Get the full snapshot JSON for a specific version."""
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    try:
        snapshot = version_control_service.get_snapshot(version_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
        return {"version_id": version_id, "snapshot": snapshot}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"get_version_snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def rollback_model_version(payload: Dict[str, Any]):
    """
    Rollback a model to a previous version (non-destructive).

    Creates a new DuckDB version (flagged as rollback) AND writes the
    snapshot back to the local repository YAML file so the file system
    stays in sync.
    """
    version_id = payload.get("version_id")
    model_id = payload.get("model_id", "default")
    workspace_id = payload.get("workspace_id", "default")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    try:
        return version_control_service.rollback_version(
            version_id=version_id,
            model_id=model_id,
            workspace_id=workspace_id,
            author="ui",
        )
    except Exception as e:
        logger.exception(f"rollback_model_version failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def compare_versions(version_from: str = "", version_to: str = ""):
    """Compare two version snapshots and return a list of changes."""
    try:
        changes = db_manager.compare_versions(version_from, version_to)
        return {"changes": changes}
    except Exception as e:
        logger.warning(f"Compare failed: {e}")
        return {"changes": []}

async def rollback_version(payload: Dict[str, Any]):
    """Rollback to a specific version."""
    version_id = payload.get("version_id")
    if not version_id:
        raise HTTPException(status_code=400, detail="version_id is required")
    try:
        result = db_manager.rollback_to_version(version_id)
        return {"status": "success", "result": result}
    except Exception as e:
        logger.exception(f"Rollback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def upload_pbix_temp(file: UploadFile = File(...)):
    """Upload a PBIX file to a temporary local folder and return absolute path."""
    temp_root = Path(tempfile.gettempdir()) / "semabridge" / "uploads"
    saved = _save_uploaded_pbix_file(file, temp_root)
    return {"path": str(saved).replace('\\\\', '/')}

async def upload_project_pbix(project_id: str, file: UploadFile = File(...)):
    """Upload PBIX for a specific project and persist path in project metadata/config."""
    project_root = Path(tempfile.gettempdir()) / "semabridge" / "projects" / project_id
    saved = _save_uploaded_pbix_file(file, project_root)
    _compat_set_project_pbix_path(project_id, str(saved))
    return {
        "project_id": project_id,
        "path": str(saved).replace('\\\\', '/'),
    }

async def import_pbix(payload: Dict[str, Any]):
    """Import a local .pbix file and extract its semantic model.

    Request body:
        {"pbix_path": "C:/path/to/model.pbix"}

    Returns:
        Extracted metadata: tables, measures, relationships, connections.
    """
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.core.exceptions import PBIXParsingError

    pbix_path = payload.get("pbix_path", "")
    if not pbix_path:
        raise HTTPException(status_code=400, detail="pbix_path is required")

    try:
        connector = LocalPBIXConnector({"pbix_path": pbix_path})
        connector.authenticate()
        result = connector.discover()

        logger.info(f"PBIX import: {len(result.get('tables', []))} tables extracted from {pbix_path}")
        return {
            "status": "success",
            "source": pbix_path,
            "tables": result.get("tables", []),
            "measures": result.get("measures", []),
            "relationships": result.get("relationships", []),
            "connections": result.get("connections", []),
            "m_code": result.get("m_code", []),
            "models": result.get("models", []),
            "metadata": result.get("metadata", {}),
        }
    except PBIXParsingError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception(f"PBIX import failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def browse_pbix_files(directory: str = ""):
    """List .pbix files in a directory for the file picker.

    Args:
        directory: Directory to scan. Defaults to configured local_models_path.

    Returns:
        List of .pbix file paths found.
    """
    scan_dir = Path(directory) if directory else _resolve_models_path()
    if not scan_dir.exists():
        return {"files": [], "directory": str(scan_dir)}

    pbix_files = [
        {
            "name": f.name,
            "path": str(f.resolve()),
            "size_bytes": f.stat().st_size,
            "modified": f.stat().st_mtime,
        }
        for f in scan_dir.rglob("*.pbix")
    ]

    return {"files": pbix_files, "directory": str(scan_dir)}

async def register_composite_report(payload: Dict[str, Any]):
    """Register a report and its composite model dependencies.

    Request body:
        {
            "report_name": "Sales Dashboard",
            "source_path": "C:/path/to/report.pbix",
            "workspace_id": "local",
            "connections": [...from PBIX extraction...]
        }
    """
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        report_id = resolver.register_report(
            report_name=payload.get("report_name", ""),
            source_path=payload.get("source_path", ""),
            workspace_id=payload.get("workspace_id", "local"),
            connections=payload.get("connections", []),
        )
        return {"status": "registered", "report_id": report_id}
    except Exception as e:
        logger.exception(f"Composite registration failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

async def get_impact_analysis(model_guid: str):
    """Impact analysis: which reports depend on this semantic model?

    Args:
        model_guid: The upstream semantic model GUID to check.

    Returns:
        List of reports that reference this model.
    """
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        impacted = resolver.get_impacted_reports(model_guid)
        return {"model_guid": model_guid, "impacted_reports": impacted}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_all_composite_links():
    """List all report â†’ model dependency links."""
    from semabridge.formats.composite_models import CompositeModelResolver

    try:
        resolver = CompositeModelResolver(db_manager)
        links = resolver.list_all_links()
        return {"links": links, "total": len(links)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


