"""
SemaBridge FastAPI Service
Production-ready backend for React + PyQt parity

- Real Fabric discovery
- Real YAML generation
- Real validation
- No mocked data
"""
# Load .env FIRST so SEMABRIDGE_DATABASE_URL, FABRIC_* and all other
# env-vars are resolved before any module-level code reads os.environ.
import os as _os
from pathlib import Path as _Path

_dotenv_path = _Path(__file__).resolve().parents[3] / ".env"
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=_dotenv_path, override=False)
except ImportError:
    pass  # python-dotenv optional — env vars already set by the OS are used as-is

from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Any, List, Optional
import asyncio
from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import hashlib
import json
import os
import re
import yaml
import logging
import time

# Windows-specific asyncio stability:
# use the selector loop instead of Proactor to avoid intermittent
# `_ProactorBaseWritePipeTransport._loop_writing` assertion failures
# during heavy logging / websocket / pipe writes.
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

# Core imports
from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.utils.logger import setup_logging
from semabridge.connectors.fabric_extractor import FabricExtractor
from semabridge.auth.fabric_validator import fabric_validator
from semabridge.api.repo_router import router as repo_router
from semabridge.api.sync_router import router as sync_router
from semabridge.api.account_router import router as account_router
from semabridge.api.websocket_alerts import alert_router, install_websocket_alert_handler
from semabridge.api.semantic_models import SemanticSyncRequest, SemanticRefreshRequest
from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.api.services.version_control_service import VersionControlService
from sqlalchemy.orm import Session
from semabridge.api.deps import get_db

try:
    from semabridge.api.auth_router import router as auth_router
    from semabridge.auth.middleware import AuthMiddleware
    _AUTH_AVAILABLE = True
except ImportError:
    auth_router = None  # type: ignore[assignment]
    AuthMiddleware = None  # type: ignore[assignment,misc]
    _AUTH_AVAILABLE = False

# -------------------------------------------------------
# Initialize Core Services (module-level, before app creation)
# -------------------------------------------------------

# setup_logging(level="INFO")  # Centralized in app_setup.py

# Install WebSocket alert handler so warnings/errors auto-dispatch to UI
install_websocket_alert_handler()

logger = logging.getLogger("semabridge.api")

db_manager = ModelRepository()
engine = ExecutionEngine(db_manager=db_manager)

settings = get_settings()
scheduler_service = SchedulerService()
version_control_service: Optional[VersionControlService] = None

# Content hash tracker -- avoids duplicate versions for unchanged files
_last_snapshot_hash: dict[str, str] = {}



# Project/versioning modules need the shared discovery cache for invalidation.
import time as _time
_discovery_cache: Dict[str, Any] = {}
_DISCOVERY_CACHE_TTL = 60

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
    """Synchronize compat project cache from persisted ORM projects.

    The ORM project table is the source of truth. Any compat-store project
    that does not exist in the backend table is removed, and matching rows
    are refreshed from the database on every load.
    """
    try:
        from semabridge.repository.orm.models import Project
        from sqlalchemy import select

        with db_manager._session() as session:
            rows = session.execute(
                select(Project).order_by(Project.last_updated.desc().nullslast()).limit(500)
            ).scalars().all()

        orm_project_ids: set[str] = set()
        dirty = False
        had_rows = bool(rows)

        for row in rows:
            pid = str(row.project_id or "").strip()
            if not pid or pid in _compat_deleted_project_ids:
                continue

            orm_project_ids.add(pid)
            current = _compat_projects.get(pid) if isinstance(_compat_projects.get(pid), dict) else {}
            project_name = _compat_clean_project_name(row.name, f"Project {pid[-6:]}")
            project = {
                "id": pid,
                "project_id": pid,
                "semantic_name": project_name,
                "display_name": project_name,
                "name": project_name,
                "description": str(current.get("description") or ""),
                "source": row.adapter or current.get("source") or "fabric",
                "source_config": current.get("source_config") if isinstance(current.get("source_config"), dict) else {},
                "adapter": row.adapter or current.get("adapter") or "fabric",
                "workspace_id": row.workspace_id or current.get("workspace_id") or "",
                "account_id": current.get("account_id"),
                "source_account_id": current.get("source_account_id"),
                "target_account_id": current.get("target_account_id"),
                "owner_user_id": current.get("owner_user_id"),
                "user_id": current.get("user_id"),
                "connection_tag": row.connection_tag or current.get("connection_tag"),
                "target": current.get("target") if isinstance(current.get("target"), dict) else {},
                "targets": current.get("targets") if isinstance(current.get("targets"), list) else [],
                "target_type": str(current.get("target_type") or "snowflake"),
                "folder_id": current.get("folder_id"),
                "status": str(current.get("status") or "draft"),
                "created_at": current.get("created_at") or _compat_now_iso(),
                "updated_at": _compat_now_iso(),
                "mapping_profile": str(current.get("mapping_profile") or ""),
                "config_source": str(current.get("config_source") or "orm"),
            }

            if current != project:
                _compat_projects[pid] = project
                dirty = True

            _compat_project_configs.setdefault(pid, _compat_project_configs.get(pid) or _compat_default_project_yaml(project))
            _compat_project_runs.setdefault(pid, [])
            _compat_project_snapshots.setdefault(pid, [])
            _compat_snapshot_groups.setdefault(pid, [])

        stale_ids = [
            project_id
            for project_id in list(_compat_projects.keys())
            if project_id not in orm_project_ids and project_id not in _compat_deleted_project_ids
        ]
        for project_id in stale_ids:
            _compat_projects.pop(project_id, None)
            _compat_project_configs.pop(project_id, None)
            _compat_project_runs.pop(project_id, None)
            _compat_project_snapshots.pop(project_id, None)
            _compat_snapshot_groups.pop(project_id, None)
            _compat_run_snapshots.pop(project_id, None)
            for mapping_id, mapping in list(_compat_mappings.items()):
                if isinstance(mapping, dict) and str(mapping.get("project_id") or "").strip() == project_id:
                    _compat_mappings.pop(mapping_id, None)
            dirty = True

        if dirty or had_rows:
            _compat_save_store()
    except Exception as exc:
        logger.debug("ORM project bootstrap skipped: %s", exc)

_compat_projects: Dict[str, Dict[str, Any]] = {}
_compat_project_configs: Dict[str, str] = {}
_compat_project_runs: Dict[str, List[Dict[str, Any]]] = {}
_compat_project_snapshots: Dict[str, List[Dict[str, Any]]] = {}
_compat_snapshot_groups: Dict[str, List[Dict[str, Any]]] = {}
_compat_run_snapshots: Dict[str, List[Dict[str, Any]]] = {}
_compat_folders: Dict[str, Dict[str, Any]] = {}
_compat_mappings: Dict[str, Dict[str, Any]] = {}
_compat_project_schedules: Dict[str, Dict[str, Any]] = {}
_compat_deleted_project_ids: set[str] = set()
_compat_job_config: Dict[str, Any] = {
    "schedule_type": "Manual Trigger Only",
    "cron": "0 0 * * *",
    "timezone": "UTC",
    "enabled": False,
    "mode": "local",
}
_compat_store_loaded: bool = False
_compat_modular_bootstrapped: bool = False


def _compat_now_iso() -> str:
    return datetime.utcnow().isoformat()

def _compat_store_path() -> Path:
    # Prefer an existing compat store inside any recognized config root
    for root in _compat_config_roots():
        candidate = root / ".semabridge_compat_store.json"
        if candidate.exists():
            return candidate

    # Legacy location at repository root
    legacy = Path(".semabridge_compat_store.json")
    if legacy.exists():
        return legacy

    # If no file exists yet, prefer writing into an existing config root
    for root in _compat_config_roots():
        if root.exists():
            return root / ".semabridge_compat_store.json"

    # Fallback to legacy path in current directory
    return legacy


def _compat_repo_root() -> Path:
    return _Path(__file__).resolve().parents[4]


def _compat_config_roots() -> List[Path]:
    roots: List[Path] = []
    for base in (Path.cwd(), _compat_repo_root()):
        for name in ("config", "Config"):
            root = base / name
            if root not in roots:
                roots.append(root)
    return roots


def _compat_config_root() -> Path:
    """
    Resolve repository config root while staying compatible with both
    `config/` and `Config/` casing used across environments.
    """
    for root in _compat_config_roots():
        if root.exists():
            return root
    return _compat_repo_root() / "Config"


def _compat_projects_dirs() -> List[Path]:
    dirs: List[Path] = []
    for root in _compat_config_roots():
        projects_dir = root / "projects"
        if projects_dir not in dirs:
            dirs.append(projects_dir)
    return dirs


def _compat_projects_dir() -> Path:
    return _compat_config_root() / "projects"


def _compat_profiles_dir() -> Path:
    return _compat_config_root() / "profiles"


def _compat_profile_to_mappings(profile_cfg: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    mappings: List[Dict[str, Any]] = []
    overrides: List[Dict[str, str]] = []
    tables = profile_cfg.get("tables") if isinstance(profile_cfg.get("tables"), list) else []
    for table in tables:
        if not isinstance(table, dict):
            continue
        source_name = str(table.get("source_name") or table.get("src") or "").strip()
        target_name = str(table.get("target_name") or table.get("tgt") or "").strip()
        if not source_name:
            continue
        row = {
            "source": source_name,
            "target": target_name or source_name,
            "type": "table",
            "columns": [],
        }
        row_columns: List[Dict[str, Any]] = []
        for col in table.get("columns") if isinstance(table.get("columns"), list) else []:
            if not isinstance(col, dict):
                continue
            src = str(col.get("source") or col.get("src") or "").strip()
            if not src:
                continue
            mapped_col: Dict[str, Any] = {
                "source": src,
                "target": str(col.get("target") or col.get("tgt") or src).strip() or src,
            }
            col_type = str(col.get("type") or "").strip()
            if col_type:
                mapped_col["type"] = col_type
            if bool(col.get("primary_key") or col.get("pk")):
                mapped_col["primary_key"] = True
            row_columns.append(mapped_col)
            overrides.append({
                "source_path": f"datasets.{source_name}.columns.{src}",
                "target_name": mapped_col["target"],
                "entity_kind": "column",
                "source_name": src,
            })
        row["columns"] = row_columns
        mappings.append(row)
        overrides.append({
            "source_path": f"datasets.{source_name}",
            "target_name": row["target"],
            "entity_kind": "table",
            "source_name": source_name,
        })
    return mappings, overrides


def _compat_assembled_project_config(project_id: str, project_cfg: Dict[str, Any], profile_cfg: Dict[str, Any]) -> Dict[str, Any]:
    source_cfg = project_cfg.get("source") if isinstance(project_cfg.get("source"), dict) else {}
    target_cfg = project_cfg.get("target") if isinstance(project_cfg.get("target"), dict) else {}
    metadata = project_cfg.get("project_metadata") if isinstance(project_cfg.get("project_metadata"), dict) else {}
    mappings, overrides = _compat_profile_to_mappings(profile_cfg)
    model_names = source_cfg.get("models") if isinstance(source_cfg.get("models"), list) else []

    assembled: Dict[str, Any] = {
        "project_name": str(metadata.get("name") or project_cfg.get("project_name") or project_id),
        "source": source_cfg,
        "target": target_cfg,
        "targets": [target_cfg] if target_cfg else [],
        "mappings": mappings,
        "mappings_overrides": overrides,
        "project_metadata": metadata,
        "mapping_profile": str(project_cfg.get("mapping_profile") or profile_cfg.get("profile_name") or ""),
        "ui": {
            "intermediate_format": "osi",
            "editor_mode": "form",
            "output_format": "osi",
        },
        "options": {
            "auto_relationships": True,
            "generate_descriptions": True,
        },
    }
    if model_names:
        assembled["source"]["models"] = model_names
    return assembled


def _compat_load_modular_project(project_id: str) -> Optional[Dict[str, Any]]:
    project_file = _compat_projects_dir() / f"{project_id}.yaml"
    if not project_file.exists():
        yml_variant = _compat_projects_dir() / f"{project_id}.yml"
        if yml_variant.exists():
            project_file = yml_variant
        else:
            return None

    try:
        project_cfg = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        logger.warning("Failed to parse modular project %s: %s", project_file, exc)
        return None
    if not isinstance(project_cfg, dict):
        return None

    profile_name = str(project_cfg.get("mapping_profile") or "").strip()
    profile_cfg: Dict[str, Any] = {"profile_name": profile_name, "tables": []}
    profile_file: Optional[Path] = None
    if profile_name:
        candidate_yaml = _compat_profiles_dir() / f"{profile_name}.yaml"
        candidate_yml = _compat_profiles_dir() / f"{profile_name}.yml"
        profile_file = candidate_yaml if candidate_yaml.exists() else candidate_yml if candidate_yml.exists() else None
        if profile_file and profile_file.exists():
            try:
                parsed_profile = yaml.safe_load(profile_file.read_text(encoding="utf-8")) or {}
                if isinstance(parsed_profile, dict):
                    profile_cfg = parsed_profile
            except Exception as exc:
                logger.warning("Failed to parse mapping profile %s: %s", profile_file, exc)

    # Self-contained project files (no mapping_profile) should be used as-is.
    # This preserves mappings_overrides authored directly in Config/projects/<id>.yaml.
    if not profile_name:
        assembled = dict(project_cfg)
    else:
        assembled = _compat_assembled_project_config(project_id, project_cfg, profile_cfg)
        # Preserve project-level overrides when present, even with mapping profiles.
        if isinstance(project_cfg.get("mappings_overrides"), list):
            assembled["mappings_overrides"] = project_cfg.get("mappings_overrides") or []
        if isinstance(project_cfg.get("mappings"), list):
            assembled["mappings"] = project_cfg.get("mappings") or []

    config_yaml = yaml.safe_dump(assembled, sort_keys=False, allow_unicode=False)
    return {
        "project_id": project_id,
        "project_file": project_file,
        "profile_file": profile_file,
        "project_cfg": project_cfg,
        "profile_cfg": profile_cfg,
        "assembled": assembled,
        "config_yaml": config_yaml,
    }


def _compat_modular_project_record(
    project_id: str,
    bundle: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    assembled = bundle.get("assembled") if isinstance(bundle.get("assembled"), dict) else {}
    project_cfg = bundle.get("project_cfg") if isinstance(bundle.get("project_cfg"), dict) else {}
    metadata = assembled.get("project_metadata") if isinstance(assembled.get("project_metadata"), dict) else {}
    source_cfg = assembled.get("source") if isinstance(assembled.get("source"), dict) else {}
    target_cfg = assembled.get("target") if isinstance(assembled.get("target"), dict) else {}
    targets_cfg = assembled.get("targets") if isinstance(assembled.get("targets"), list) else []
    first_target_cfg = targets_cfg[0] if targets_cfg and isinstance(targets_cfg[0], dict) else {}
    existing = existing if isinstance(existing, dict) else {}

    project_name = _compat_clean_project_name(
        metadata.get("name") or assembled.get("project_name") or project_cfg.get("project_name") or project_cfg.get("display_name"),
        f"Project {project_id[-6:]}",
    )
    source_account_id = str(
        source_cfg.get("identity_id") or source_cfg.get("account_id") or existing.get("source_account_id") or ""
    ).strip() or None
    target_account_id = str(
        target_cfg.get("identity_id")
        or target_cfg.get("account_id")
        or first_target_cfg.get("identity_id")
        or first_target_cfg.get("account_id")
        or existing.get("target_account_id")
        or ""
    ).strip() or None
    account_id = str(
        existing.get("account_id")
        or source_account_id
        or target_account_id
        or ""
    ).strip() or None
    owner_user_id = str(
        assembled.get("owner_user_id")
        or metadata.get("owner_user_id")
        or existing.get("owner_user_id")
        or existing.get("user_id")
        or ""
    ).strip() or None

    payload = {
        "name": project_name,
        "display_name": project_name,
        "description": str(metadata.get("description") or existing.get("description") or ""),
        "source": {
            **source_cfg,
            "type": source_cfg.get("type") or existing.get("source") or "fabric",
        },
        "target": {
            **target_cfg,
            "type": target_cfg.get("type") or first_target_cfg.get("type") or existing.get("target_type") or "snowflake",
        } if (target_cfg or first_target_cfg or existing.get("target_type")) else {},
        "targets": targets_cfg,
        "account_id": account_id,
        "user_id": owner_user_id,
        "owner_user_id": owner_user_id,
        "folder_id": existing.get("folder_id"),
        "source_type": source_cfg.get("type") or existing.get("source") or "fabric",
        "target_type": target_cfg.get("type") or first_target_cfg.get("type") or existing.get("target_type") or "snowflake",
        "connection_tag": source_cfg.get("connection_tag") or target_cfg.get("connection_tag") or first_target_cfg.get("connection_tag"),
    }

    project = _compat_project_payload(project_id, payload)
    project["semantic_name"] = project_name
    project["display_name"] = project_name
    project["name"] = project_name
    project["description"] = str(metadata.get("description") or existing.get("description") or "")
    project["folder_id"] = existing.get("folder_id")
    project["status"] = existing.get("status") or "draft"
    project["mapping_profile"] = str(assembled.get("mapping_profile") or existing.get("mapping_profile") or "")
    project["config_source"] = "modular"
    project["created_at"] = existing.get("created_at") or _compat_now_iso()
    project["updated_at"] = existing.get("updated_at") or project["created_at"]
    project["source_config"] = source_cfg
    if target_cfg or first_target_cfg:
        project["target"] = target_cfg or first_target_cfg
    if targets_cfg:
        project["targets"] = targets_cfg
    return project


def _compat_modular_project_matches(existing: Dict[str, Any], candidate: Dict[str, Any]) -> bool:
    keys = set(existing.keys()) | set(candidate.keys())
    for key in keys:
        if key in {"created_at", "updated_at"}:
            continue
        if existing.get(key) != candidate.get(key):
            return False
    return True


def _compat_save_store() -> None:
    payload = {
        "projects": _compat_projects,
        "project_configs": _compat_project_configs,
        # Strip large run detail blobs (summary/results/logs) — only needed
        # for run detail views, not the list.
        "project_runs": _strip_run_blobs(_compat_project_runs),
        # Strip large state/config/artifact blobs before persisting — they are
        # re-hydrated from the source on demand (snapshot detail view).
        # This keeps the store file small and cold-start parsing fast.
        "project_snapshots": _strip_snapshot_blobs(_compat_project_snapshots),
        "run_snapshots": _compat_run_snapshots,
        "folders": _compat_folders,
        "mappings": _compat_mappings,
        "project_schedules": _compat_project_schedules,
        "deleted_project_ids": sorted(_compat_deleted_project_ids),
        "job_config": _compat_job_config,
    }
    try:
        _compat_store_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to persist compat store: %s", exc)


def _compat_clear_project_schedule(project_id: str) -> None:
    _compat_project_schedules.pop(str(project_id), None)
    _compat_save_store()


_SNAPSHOT_BLOB_KEYS = frozenset({"state", "project_config_yaml", "artifact"})
_RUN_BLOB_KEYS = frozenset({"summary", "results", "logs", "stage_states"})


def _strip_snapshot_blobs(snapshots_by_project: dict) -> dict:
    """Remove large state/config/artifact blobs from snapshot list entries.

    These fields are only needed for snapshot detail/compare views and are
    fetched on-demand via get_snapshot_content_compat / compare_project_snapshots_compat.
    Keeping them in the in-memory list inflates the compat store to 6+ MB and
    causes slow cold-start JSON parsing on every process restart.
    """
    stripped: dict = {}
    for pid, snaps in snapshots_by_project.items():
        if not isinstance(snaps, list):
            stripped[pid] = snaps
            continue
        stripped[pid] = [
            {k: v for k, v in snap.items() if k not in _SNAPSHOT_BLOB_KEYS}
            if isinstance(snap, dict) else snap
            for snap in snaps
        ]
    return stripped


def _strip_run_blobs(runs_by_project: dict) -> dict:
    """Remove large summary/results/logs blobs from run list entries.

    These fields are only needed for run detail views. Stripping them from
    the in-memory list keeps the compat store small and cold-start fast.
    """
    stripped: dict = {}
    for pid, runs in runs_by_project.items():
        if not isinstance(runs, list):
            stripped[pid] = runs
            continue
        stripped[pid] = [
            {k: v for k, v in run.items() if k not in _RUN_BLOB_KEYS}
            if isinstance(run, dict) else run
            for run in runs
        ]
    return stripped


def _compat_load_store() -> None:
    global _compat_store_loaded
    if _compat_store_loaded:
        return

    p = _compat_store_path()
    if not p.exists():
        _compat_store_loaded = True
        return

    try:
        import time as _time
        _t0 = _time.perf_counter()
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        logger.debug(
            "Compat store loaded in %.3fs (%.1f KB)",
            _time.perf_counter() - _t0,
            p.stat().st_size / 1024,
        )
        if isinstance(data.get("projects"), dict):
            _compat_projects.update(data.get("projects") or {})
        if isinstance(data.get("project_configs"), dict):
            _compat_project_configs.update(data.get("project_configs") or {})
        if isinstance(data.get("project_runs"), dict):
            _compat_project_runs.update(
                _strip_run_blobs(data.get("project_runs") or {})
            )
        if isinstance(data.get("project_snapshots"), dict):
            _compat_project_snapshots.update(
                _strip_snapshot_blobs(data.get("project_snapshots") or {})
            )
        if isinstance(data.get("snapshot_groups"), dict):
            _compat_snapshot_groups.update(data.get("snapshot_groups") or {})
        if isinstance(data.get("run_snapshots"), dict):
            _compat_run_snapshots.update(data.get("run_snapshots") or {})
        if isinstance(data.get("folders"), dict):
            _compat_folders.update(data.get("folders") or {})
        if isinstance(data.get("mappings"), dict):
            _compat_mappings.update(data.get("mappings") or {})
        if isinstance(data.get("project_schedules"), dict):
            _compat_project_schedules.update(data.get("project_schedules") or {})
        if isinstance(data.get("deleted_project_ids"), list):
            _compat_deleted_project_ids.update(
                str(project_id).strip()
                for project_id in data.get("deleted_project_ids") or []
                if str(project_id).strip()
            )
        if isinstance(data.get("job_config"), dict):
            _compat_job_config.update(data.get("job_config") or {})

        for project_id in list(_compat_deleted_project_ids):
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
    except Exception as exc:
        logger.warning("Failed to load compat store: %s", exc)
    finally:
        _compat_store_loaded = True


def _compat_bootstrap_projects_from_orm() -> None:
    """Synchronize compat project cache from persisted ORM projects.

    The ORM project table is the source of truth. Any compat-store project
    that does not exist in the backend table is removed, and matching rows
    are refreshed from the database on every load.
    """
    try:
        from semabridge.repository.orm.models import Project
        from sqlalchemy import select

        with db_manager._session() as session:
            rows = session.execute(
                select(Project).order_by(Project.last_updated.desc().nullslast()).limit(500)
            ).scalars().all()

        orm_project_ids: set[str] = set()
        dirty = False

        for row in rows:
            pid = str(row.project_id or "").strip()
            if not pid:
                continue

            if pid in _compat_deleted_project_ids:
                _compat_deleted_project_ids.discard(pid)
                dirty = True

            orm_project_ids.add(pid)
            current = _compat_projects.get(pid) if isinstance(_compat_projects.get(pid), dict) else {}
            project_name = _compat_clean_project_name(row.name, f"Project {pid[-6:]}")
            project = {
                "id": pid,
                "project_id": pid,
                "semantic_name": project_name,
                "display_name": project_name,
                "name": project_name,
                "description": str(current.get("description") or ""),
                "source": row.adapter or current.get("source") or "fabric",
                "source_config": current.get("source_config") if isinstance(current.get("source_config"), dict) else {},
                "adapter": row.adapter or current.get("adapter") or "fabric",
                "workspace_id": row.workspace_id or current.get("workspace_id") or "",
                "account_id": current.get("account_id"),
                "source_account_id": current.get("source_account_id"),
                "target_account_id": current.get("target_account_id"),
                "owner_user_id": current.get("owner_user_id"),
                "user_id": current.get("user_id"),
                "connection_tag": row.connection_tag or current.get("connection_tag"),
                "target": current.get("target") if isinstance(current.get("target"), dict) else {},
                "targets": current.get("targets") if isinstance(current.get("targets"), list) else [],
                "target_type": str(current.get("target_type") or "snowflake"),
                "folder_id": current.get("folder_id"),
                "status": str(current.get("status") or "draft"),
                "created_at": current.get("created_at") or _compat_now_iso(),
                "updated_at": _compat_now_iso(),
                "mapping_profile": str(current.get("mapping_profile") or ""),
                "config_source": str(current.get("config_source") or "orm"),
            }

            if current != project:
                _compat_projects[pid] = project
                dirty = True

            _compat_project_configs.setdefault(pid, _compat_project_configs.get(pid) or _compat_default_project_yaml(project))
            _compat_project_runs.setdefault(pid, [])
            _compat_project_snapshots.setdefault(pid, [])
            _compat_snapshot_groups.setdefault(pid, [])

        stale_ids = [
            project_id
            for project_id in list(_compat_projects.keys())
            if project_id not in orm_project_ids and project_id not in _compat_deleted_project_ids
        ]
        for project_id in stale_ids:
            _compat_deleted_project_ids.add(project_id)
            _compat_projects.pop(project_id, None)
            _compat_project_configs.pop(project_id, None)
            _compat_project_runs.pop(project_id, None)
            _compat_project_snapshots.pop(project_id, None)
            _compat_snapshot_groups.pop(project_id, None)
            _compat_run_snapshots.pop(project_id, None)
            for path in _compat_project_yaml_paths(project_id):
                try:
                    if path.exists() and path.is_file():
                        path.unlink()
                except Exception as exc:
                    logger.warning("Failed to delete stale project config file %s: %s", path, exc)
            for mapping_id, mapping in list(_compat_mappings.items()):
                if isinstance(mapping, dict) and str(mapping.get("project_id") or "").strip() == project_id:
                    _compat_mappings.pop(mapping_id, None)
            dirty = True

        if dirty:
            _compat_save_store()
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
    if project_id in _compat_deleted_project_ids:
        return

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
    _compat_project_snapshots.setdefault(project_id, [])
    _compat_snapshot_groups.setdefault(project_id, [])


def _compat_ensure_loaded() -> None:
    global _compat_modular_bootstrapped
    _compat_load_store()
    _compat_bootstrap_projects_from_orm()
    _compat_modular_bootstrapped = True


def _compat_repo_yaml_path() -> Path:
    from semabridge.core.config_loader import get_project_file_path
    return get_project_file_path("semabridge.yaml")


def _compat_project_yaml_path(project_id: str) -> Path:
    file_name = f"{project_id}.yaml"
    for p in _compat_project_yaml_paths(project_id):
        if p.exists():
            return p
    return _compat_projects_dir() / file_name


def _compat_project_yaml_paths(project_id: str) -> List[Path]:
    paths: List[Path] = []
    for projects_dir in _compat_projects_dirs():
        for suffix in ("yaml", "yml"):
            path = projects_dir / f"{project_id}.{suffix}"
            if path not in paths:
                paths.append(path)
    return paths


def _compat_load_project_yaml_text(project_id: str) -> str:
    for p in _compat_project_yaml_paths(project_id):
        try:
            if p.exists():
                text = p.read_text(encoding="utf-8")
                if text.strip():
                    return text
        except Exception:
            continue
    return ""


def _compat_save_project_yaml_text(project_id: str, yaml_text: str) -> Path:
    p = _compat_project_yaml_path(project_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml_text, encoding="utf-8")
    return p


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
    project_name = _compat_clean_project_name(payload.get("display_name") or payload.get("name"), f"Project {project_id[-6:]}")
    source_account_id = str(source_obj.get("identity_id") or source_obj.get("account_id") or "").strip() or None
    target_account_id = str(
        target_obj.get("identity_id")
        or target_obj.get("account_id")
        or first_target_obj.get("identity_id")
        or first_target_obj.get("account_id")
        or ""
    ).strip() or None
    account_id = str(payload.get("account_id") or source_account_id or target_account_id or "").strip() or None
    owner_user_id = str(payload.get("user_id") or payload.get("owner_user_id") or "").strip() or None
    return {
        "id": project_id,
        "project_id": project_id,
        "semantic_name": project_name,
        "display_name": project_name,
        "name": project_name,
        "description": payload.get("description") or "",
        "source": source_obj.get("type") or payload.get("source_type") or "fabric",
        "source_config": source_obj,
        "adapter": source_obj.get("type") or payload.get("source_type") or "fabric",
        "workspace_id": source_obj.get("workspace_id") or "",
        "account_id": account_id,
        "source_account_id": source_account_id,
        "target_account_id": target_account_id,
        "owner_user_id": owner_user_id,
        "user_id": owner_user_id,
        "connection_tag": str(payload.get("connection_tag") or source_obj.get("connection_tag") or target_obj.get("connection_tag") or "").strip() or None,
        "target": target_obj if target_obj else first_target_obj,
        "targets": targets_list,
        "target_type": target_obj.get("type") or first_target_obj.get("type") or payload.get("target_type") or "snowflake",
        "folder_id": payload.get("folder_id"),
        "status": "draft",
        "created_at": _compat_now_iso(),
        "updated_at": _compat_now_iso(),
    }


def _compat_default_project_yaml(project: Dict[str, Any]) -> str:
    name = _compat_clean_project_name(project.get("display_name") or project.get("name"), "Untitled Project").replace('"', '\\"')
    project_id = str(project.get("project_id") or project.get("id") or f"proj-{int(_time.time() * 1000)}").strip()
    owner_user_id = str(project.get("owner_user_id") or project.get("user_id") or "").strip()
    src = project.get("source") or "fabric"
    target = project.get("target_type") or "snowflake"
    lines = [
        f'project_id: "{project_id}"',
        f'display_name: "{name}"',
        f'project_name: "{name}"',
        "source:",
        f"  type: {src}",
    ]
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
    if owner_user_id:
        owner_escaped = owner_user_id.replace('"', '\\"')
        lines.extend([
            f'owner_user_id: "{owner_escaped}"',
            "project_metadata:",
            f'  owner_user_id: "{owner_escaped}"',
        ])
    return "\n".join(lines)


# The split project implementation modules intentionally use star-imports from
# this shared module so they can reuse the same compat state and helper
# surface. Python skips underscore-prefixed names during `import *` unless
# `__all__` is defined, so publish the shared compat helpers explicitly.
__all__ = [name for name in globals() if not name.startswith("__")]
