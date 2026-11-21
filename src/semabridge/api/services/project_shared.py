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
    if _os.environ.get("SEMABRIDGE_DISABLE_DOTENV", "").strip().lower() not in ("1", "true", "yes", "on"):  # tests set SEMABRIDGE_DISABLE_DOTENV
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
import threading
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

try:
    db_manager = ModelRepository()
    engine = ExecutionEngine(db_manager=db_manager)
except Exception:
    db_manager = None
    engine = None

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
    except Exception as exc:
        logger.debug("Could not read global config for models path: %s", exc)

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
    except Exception as exc:
        logger.debug("Could not read project config for models path: %s", exc)

    # 5. Default — project root (no more ./models subdirectory)
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


version_control_service = VersionControlService(
    db_manager=db_manager,
    models_path_resolver=_resolve_models_path,
    hash_tracker=_last_snapshot_hash,
)


# ---------------------------------------------------------------------------
# In-memory write-through cache
#
# These dicts are the primary read path for all project operations.
# They are populated at startup from:
#   1. config/.semabridge_compat_store.json  (fast path)
#   2. ORM / SQLAlchemy                      (fallback)
#   3. Filesystem YAML files                 (last resort)
#
# On write, data is stored to BOTH the dict AND the ORM (write-through).
# This means reads are fast (no DB query), but the process holds the
# authoritative state — two processes cannot share it without a proper
# external store.
#
# Future direction: replace with a proper ProjectCache class that exposes
# typed accessors (get_snapshots, find_snapshot, etc.) and is injected
# via ServiceContainer rather than being a module-level global.
# ---------------------------------------------------------------------------

# -------------------------------------------------------
# Health
# -------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Cache accessor helpers
# ---------------------------------------------------------------------------

def get_project_snapshots(project_id: str) -> List[Dict[str, Any]]:
    """Return all valid snapshot dicts for a project (filters out corrupt entries)."""
    return [r for r in _compat_project_snapshots.get(project_id, []) if isinstance(r, dict)]


def find_project_snapshot(project_id: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
    """Return a single snapshot by ID, or None if not found."""
    return next(
        (r for r in get_project_snapshots(project_id)
         if str(r.get("snapshot_id") or "") == snapshot_id),
        None,
    )


def _compat_now_iso() -> str:
    """Current UTC time as ISO-8601 with an explicit ``Z`` (never naive)."""
    from datetime import timezone as _tz

    return datetime.now(_tz.utc).isoformat().replace("+00:00", "Z")

def _compat_store_write_path() -> Path:
    """Stable, per-user location for the compat write-through cache.

    This file is rewritten on nearly every mutation (see _compat_save_store)
    and is already .gitignore'd — it's local runtime state, not shared
    config. It must never live inside the repo's Config/ directory: in this
    environment (and likely others) the repo sits inside a live-syncing
    OneDrive folder, and Config/.semabridge_compat_store.json is managed by
    OneDrive's cloud-file provider (confirmed via `fsutil reparsepoint
    query` showing reparse tag 0x9000601a — Microsoft's cloud-files tag —
    while OneDrive.exe was actively running). OneDrive can transiently hold
    an exclusive lock on such a file mid-sync, which surfaces here as
    `PermissionError: [Errno 13] Permission denied` on write — not a missing
    directory, wrong relative path, or a file left read-only.

    ~/.semabridge/ mirrors the location already used for the log file
    (resolve_log_file_path) and the DuckDB fallback DB
    (session_factory.fetch_latest_database_url), neither of which is
    typically inside a cloud-sync folder.

    This is real per-user state, not a fixture — a test process must never
    resolve to it. SEMABRIDGE_COMPAT_STORE_PATH gives an explicit override
    (Tests/conftest.py sets one), and as a backstop independent of that,
    any process running under pytest (PYTEST_CURRENT_TEST, set by pytest
    itself for the duration of every test — same convention already used in
    databricks_publisher.py) redirects to a throwaway path under the system
    temp directory instead of Path.home(), so a test run can never load or
    overwrite a real user's compat store even if some future test forgets
    to isolate it.
    """
    override = os.environ.get("SEMABRIDGE_COMPAT_STORE_PATH")
    if override:
        override_path = Path(override)
        override_path.parent.mkdir(parents=True, exist_ok=True)
        return override_path

    if "PYTEST_CURRENT_TEST" in os.environ:
        import tempfile
        test_dir = Path(tempfile.gettempdir()) / "semabridge_test_compat_store"
        test_dir.mkdir(parents=True, exist_ok=True)
        return test_dir / ".semabridge_compat_store.json"

    stable_dir = Path.home() / ".semabridge"
    stable_dir.mkdir(parents=True, exist_ok=True)
    return stable_dir / ".semabridge_compat_store.json"


def _compat_store_path() -> Path:
    """Resolve where to *load* the compat store from.

    Prefers the stable per-user location. Falls back to the old
    repo-relative locations only so a store from before this fix isn't
    silently lost — those are read-only fallbacks; every save goes through
    _compat_store_write_path(), so the data migrates to the stable location
    on the very next write and the legacy path is never touched again.

    Under test isolation (explicit override or PYTEST_CURRENT_TEST) the
    legacy repo-relative scan is skipped entirely — a file that happens to
    exist at one of those repo-relative paths for unrelated reasons must
    never be picked up as a test's compat store.
    """
    stable_path = _compat_store_write_path()
    if stable_path.exists():
        return stable_path

    if os.environ.get("SEMABRIDGE_COMPAT_STORE_PATH") or "PYTEST_CURRENT_TEST" in os.environ:
        return stable_path

    for legacy_candidate in (
        Path("config/.semabridge_compat_store.json"),
        Path("Config/.semabridge_compat_store.json"),
        Path(".semabridge_compat_store.json"),
    ):
        if legacy_candidate.exists():
            return legacy_candidate

    return stable_path


def _compat_repo_root() -> Path:
    return _Path(__file__).resolve().parents[4]


def _compat_config_roots() -> List[Path]:
    """Candidate config roots, deduplicated by filesystem identity.

    (Path.cwd(), repo_root) x ("config", "Config") produces up to four
    candidate paths, but on a case-insensitive filesystem (the macOS
    default) "config" and "Config" are different strings pointing at the
    SAME physical directory -- a plain `root not in roots` check compares
    path components, not filesystem identity, so both survive and every
    project YAML under that one real directory gets scanned twice (once
    per casing), logging a spurious "duplicate project_id" warning for
    every project on every listing. Deduping by (st_dev, st_ino) instead
    collapses same-directory candidates regardless of casing; a candidate
    that doesn't exist yet has no inode to compare, so it's kept as-is
    (existing not-found handling elsewhere already skips it).
    """
    roots: List[Path] = []
    seen_identity: set = set()
    for base in (Path.cwd(), _compat_repo_root()):
        for name in ("config", "Config"):
            root = base / name
            try:
                stat_result = os.stat(root)
                identity = (stat_result.st_dev, stat_result.st_ino)
            except OSError:
                identity = None
            key = identity if identity is not None else root
            if key in seen_identity:
                continue
            seen_identity.add(key)
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
    # Pass through project-level `behavior` (e.g. `behavior.features.demo_mode`)
    # unchanged -- this dict is otherwise rebuilt field-by-field from
    # project_cfg/profile_cfg, so a project author's behavior overrides would
    # silently vanish for any project using a mapping_profile.
    raw_behavior = project_cfg.get("behavior")
    if isinstance(raw_behavior, dict):
        assembled["behavior"] = raw_behavior
    return assembled


def _compat_load_modular_project(project_id: str) -> Optional[Dict[str, Any]]:
    """The project's live config (from the authoritative store -- database,
    cached; see project_config_store.get_project_config_text), assembled with
    its mapping profile when it names one. None when it has no config."""
    from semabridge.api.services.project_config_store import get_project_config_text

    project_file = _compat_project_yaml_path(project_id)
    config_text = get_project_config_text(project_id)
    if not str(config_text or "").strip():
        return None

    try:
        project_cfg = yaml.safe_load(config_text) or {}
    except yaml.YAMLError as exc:
        # A corrupt config is NOT "no config": callers decide sync/run
        # behaviour from this bundle, so fail loudly.
        from semabridge.api.services.project_config_store import ConfigPersistenceError

        logger.warning("Project config for %s is not valid YAML: %s", project_id, exc)
        raise ConfigPersistenceError(f"Project config for {project_id} is not valid YAML: {exc}") from exc
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

    # Self-contained project configs (no mapping_profile) are used as-is.
    if not profile_name:
        assembled = dict(project_cfg)
    else:
        assembled = _compat_assembled_project_config(project_id, project_cfg, profile_cfg)
        # Preserve project-level overrides when present, even with mapping profiles.
        if isinstance(project_cfg.get("mappings_overrides"), list):
            assembled["mappings_overrides"] = project_cfg.get("mappings_overrides") or []
        if isinstance(project_cfg.get("mappings"), list):
            assembled["mappings"] = project_cfg.get("mappings") or []

    config_yaml = (
        config_text if not profile_name
        else yaml.safe_dump(assembled, sort_keys=False, allow_unicode=False)
    )
    return {
        "project_id": project_id,
        "project_file": project_file,
        "profile_file": profile_file,
        "project_cfg": project_cfg,
        "profile_cfg": profile_cfg,
        "assembled": assembled,
        "config_yaml": config_yaml,
    }


# Report of the last startup import of Config/projects/*.yaml into the
# project_configs table (see project_config_store.import_project_config_files).
_compat_config_import_report: Dict[str, Any] = {}
# Real-project configs found in a legacy JSON store: imported into the
# database once (when the project has neither a DB version nor a file), never
# used as a live copy.
_compat_legacy_store_configs: Dict[str, str] = {}


def _compat_bootstrap_projects_from_modular_configs() -> None:
    """Import config files into the database, load every project's live
    config into the cache and register a project entry for each of them."""
    from semabridge.api.services import project_config_store

    try:
        report = project_config_store.import_project_config_files(_compat_legacy_store_configs)
        _compat_config_import_report.clear()
        _compat_config_import_report.update(report)
        if report.get("imported") or report.get("file_edits") or report.get("imported_from_store"):
            logger.info(
                "Project config import: %d imported, %d hand-edited file(s) imported, %d from legacy store, %d error(s)",
                len(report.get("imported") or []), len(report.get("file_edits") or []),
                len(report.get("imported_from_store") or []), len(report.get("errors") or []),
            )
        project_ids = list(project_config_store.load_all_configs_into_cache() or [])
    except Exception as exc:
        logger.error("Project configs could not be loaded from the database: %s", exc)
        project_ids = []
    # A project file whose import failed (errors are reported, not raised)
    # must still be visible: add every project YAML not already listed. Its
    # config is then read from the file by _compat_load_modular_project.
    file_ids = sorted(
        path.stem.strip()
        for projects_dir in _compat_projects_dirs() if projects_dir.exists()
        for path in projects_dir.glob("*.y*ml")
    )
    project_ids = list(dict.fromkeys([*project_ids, *file_ids]))

    seen: set[str] = set()
    if True:
        for project_id in project_ids:
            if (
                not project_id or project_id in seen or project_id in _compat_deleted_project_ids
                or project_id == "preview" or project_id.startswith("preview-")
            ):
                continue
            try:
                bundle = _compat_load_modular_project(project_id)
            except Exception as exc:  # one corrupt project must not hide the others
                logger.error("Skipping project %s while loading project list: %s", project_id, exc)
                continue
            if not bundle:
                continue

            assembled = bundle["assembled"]
            source_cfg = assembled.get("source") if isinstance(assembled.get("source"), dict) else {}
            target_cfg = assembled.get("target") if isinstance(assembled.get("target"), dict) else {}
            targets_cfg = assembled.get("targets") if isinstance(assembled.get("targets"), list) else []
            first_target_cfg = targets_cfg[0] if targets_cfg and isinstance(targets_cfg[0], dict) else {}
            metadata = assembled.get("project_metadata") if isinstance(assembled.get("project_metadata"), dict) else {}
            existing = _compat_projects.get(project_id) if isinstance(_compat_projects.get(project_id), dict) else {}
            owner_user_id = str(
                assembled.get("owner_user_id")
                or metadata.get("owner_user_id")
                or existing.get("owner_user_id")
                or existing.get("user_id")
                or ""
            ).strip() or None
            source_account_id = str(
                source_cfg.get("identity_id")
                or source_cfg.get("account_id")
                or existing.get("source_account_id")
                or ""
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
            project = {
                "id": project_id,
                "project_id": project_id,
                "name": _compat_clean_project_name(metadata.get("name") or assembled.get("project_name"), f"Project {project_id[-6:]}"),
                "description": str(metadata.get("description") or existing.get("description") or ""),
                "source": source_cfg.get("type") or existing.get("source") or "fabric",
                "adapter": source_cfg.get("type") or existing.get("adapter") or "fabric",
                "workspace_id": str(source_cfg.get("workspace_id") or existing.get("workspace_id") or ""),
                "account_id": account_id,
                "source_account_id": source_account_id,
                "target_account_id": target_account_id,
                "owner_user_id": owner_user_id,
                "user_id": owner_user_id,
                "target_type": target_cfg.get("type") or existing.get("target_type") or "snowflake",
                "folder_id": existing.get("folder_id"),
                "status": existing.get("status") or "draft",
                "mapping_profile": assembled.get("mapping_profile") or "",
                "config_source": "modular",
                "created_at": existing.get("created_at") or _compat_now_iso(),
                "updated_at": existing.get("updated_at") or _compat_now_iso(),
            }
            # Keep metadata only the cache carries (sync_mode, notification
            # email, pbix path, ...); config-derived fields win.
            _compat_projects[project_id] = {**existing, **project}
            _compat_project_runs.setdefault(project_id, [])
            _compat_project_snapshots.setdefault(project_id, [])
            _compat_snapshot_groups.setdefault(project_id, [])
            seen.add(project_id)


# Guards every mutation of the compat dicts that can happen off the event-loop
# thread (dry-run job workers run up to MAX_PARALLEL_DRY_RUN_FILES pipelines on
# real OS threads, each writing its preview project + config here) and the
# serialization in _compat_save_store(). Re-entrant so a caller holding it
# (e.g. mutate-then-save) can call _compat_save_store() without deadlocking.
_compat_store_lock = threading.RLock()


def compat_store_lock() -> "threading.RLock":
    """Public accessor for the compat-store lock (use as ``with compat_store_lock():``)."""
    return _compat_store_lock


def _compat_snapshot_container(value: Any) -> Any:
    """Shallow-copy a top-level container (and its first-level dict/list
    values) so json serialization can run outside of any concurrent mutation
    of the live dicts."""
    if isinstance(value, dict):
        return {
            k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
            for k, v in list(value.items())
        }
    if isinstance(value, (list, set, tuple)):
        return list(value)
    return value


def _compat_build_store_payload() -> Dict[str, Any]:
    return {
        "projects": _compat_snapshot_container(_compat_projects),
        # Real projects' configs are persisted in the project_configs table;
        # only transient dry-run preview configs are kept here.
        "project_configs": {
            pid: cfg for pid, cfg in list(_compat_project_configs.items())
            if str(pid).startswith("preview")
        },
        # Strip large run detail blobs (summary/results/logs) — only needed
        # for run detail views, not the list.
        "project_runs": _strip_run_blobs(_compat_snapshot_container(_compat_project_runs)),
        # Strip large state/config/artifact blobs before persisting — they are
        # re-hydrated from the source on demand (snapshot detail view).
        # This keeps the store file small and cold-start parsing fast.
        "project_snapshots": _strip_snapshot_blobs(_compat_snapshot_container(_compat_project_snapshots)),
        "run_snapshots": _compat_snapshot_container(_compat_run_snapshots),
        "folders": _compat_snapshot_container(_compat_folders),
        "mappings": _compat_snapshot_container(_compat_mappings),
        "project_schedules": _compat_snapshot_container(_compat_project_schedules),
        "deleted_project_ids": sorted(list(_compat_deleted_project_ids)),
        "job_config": dict(_compat_job_config),
    }


def _compat_atomic_write_text(path: Path, text: str) -> None:
    """Write via a sibling temp file + os.replace so a reader (or a crash)
    never observes a half-written store. Retries os.replace briefly because on
    Windows the destination can be transiently locked (AV / sync clients)."""
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        last_exc: Optional[BaseException] = None
        for attempt in range(5):
            try:
                os.replace(tmp_name, str(path))
                return
            except PermissionError as exc:
                last_exc = exc
                time.sleep(0.05 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except OSError:
            pass


from semabridge.domain.exceptions import InternalError as _InternalError


class CompatStorePersistenceError(_InternalError):
    """The compat JSON store could not be written (HTTP 500 with the reason)."""


_compat_store_dirty: bool = False
_compat_store_flush_timer: Optional[threading.Timer] = None
_compat_store_last_error: Optional[str] = None


def _compat_raise_if_store_failed() -> None:
    """Raise CompatStorePersistenceError when the latest store save failed.
    Call right after _compat_save_store() in code paths a user action
    triggered, so the request reports the failure."""
    if _compat_store_dirty and _compat_store_last_error:
        raise CompatStorePersistenceError(f"Local state could not be saved: {_compat_store_last_error}")


def _compat_save_store(raise_on_error: bool = False) -> bool:
    """Serialize the compat cache to the JSON store file (atomic replace).

    Holds the store lock for the snapshot + write. A failure is logged at
    ERROR, the store is marked dirty and a background retry is scheduled; it
    is raised (CompatStorePersistenceError) when ``raise_on_error`` -- pass
    that from code paths a user action triggered, so the action reports the
    failure instead of silently "succeeding". Returns True when written.
    """
    global _compat_store_dirty, _compat_store_last_error
    try:
        with _compat_store_lock:
            text = None
            last_exc: Optional[BaseException] = None
            for _attempt in range(8):
                try:
                    payload = _compat_build_store_payload()
                    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
                    break
                except RuntimeError as exc:
                    # "dictionary changed size during iteration": a mutator on
                    # another thread did not hold the lock -- retry, never drop.
                    last_exc = exc
                    time.sleep(0.01 * (_attempt + 1))
            if text is None:
                raise CompatStorePersistenceError(
                    f"store mutated concurrently during serialization: {last_exc}"
                )
            _compat_atomic_write_text(_compat_store_write_path(), text)
            _compat_store_dirty = False
            _compat_store_last_error = None
            return True
    except Exception as exc:
        _compat_store_dirty = True
        _compat_store_last_error = str(exc)
        logger.error("Failed to persist compat store: %s", exc)
        _compat_schedule_store_flush(delay=5.0)
        if raise_on_error:
            if isinstance(exc, CompatStorePersistenceError):
                raise
            raise CompatStorePersistenceError(f"Local state could not be saved: {exc}") from exc
        return False


def _compat_schedule_store_flush(delay: float = 1.0) -> None:
    """Coalesced deferred save: used by read (GET) paths that refresh cached
    state, so a read never rewrites the whole store file synchronously."""
    global _compat_store_dirty, _compat_store_flush_timer
    _compat_store_dirty = True
    with _compat_store_lock:
        timer = _compat_store_flush_timer
        if timer is not None and timer.is_alive():
            return

        def _flush() -> None:
            global _compat_store_flush_timer
            with _compat_store_lock:
                _compat_store_flush_timer = None
            if _compat_store_dirty:
                _compat_save_store()

        timer = threading.Timer(delay, _flush)
        timer.daemon = True
        _compat_store_flush_timer = timer
        timer.start()


def _compat_mark_store_dirty() -> None:
    """Record that the cache changed without writing the file now."""
    _compat_schedule_store_flush()


def _compat_clear_project_schedule(project_id: str) -> None:
    with _compat_store_lock:
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
            # Only transient preview configs live in the store. A real
            # project's config is owned by the project_configs table; one
            # found here (older store) is an import candidate, never a copy.
            for _pid, _cfg in (data.get("project_configs") or {}).items():
                if str(_pid).startswith("preview"):
                    _compat_project_configs[_pid] = _cfg
                elif isinstance(_cfg, str):
                    _compat_legacy_store_configs[str(_pid)] = _cfg
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


def _compat_refresh_project_statuses_from_runs() -> None:
    """Overwrite each project's in-memory status with its most recent Run result.

    Called unconditionally during bootstrap (after the store is loaded) so that
    stale "draft" values from the JSON store file are replaced with the real
    status derived from the Run table.  Also called after every run completes
    to keep the in-memory cache accurate without restarting.
    """
    if not _compat_projects:
        return
    try:
        from semabridge.repository.orm.run_models import Run
        from sqlalchemy import select, func

        project_ids = [
            pid for pid in _compat_projects
            if pid and not pid.startswith("preview")
        ]
        if not project_ids:
            return

        _run_map = {"success": "active", "warning": "warning", "partial": "warning", "failed": "failed"}
        with db_manager.get_session() as session:
            max_ts_sq = (
                select(Run.project_id, func.max(Run.completed_at).label("max_ts"))
                .where(Run.project_id.in_(project_ids))
                .where(Run.status.in_(["success", "failed", "warning", "partial"]))
                .group_by(Run.project_id)
                .subquery()
            )
            run_rows = session.execute(
                select(Run.project_id, Run.status)
                .join(
                    max_ts_sq,
                    (Run.project_id == max_ts_sq.c.project_id)
                    & (Run.completed_at == max_ts_sq.c.max_ts),
                )
            ).all()

        for rr in run_rows:
            pid = str(rr.project_id or "").strip()
            if pid and pid in _compat_projects:
                _compat_projects[pid]["status"] = _run_map.get(
                    str(rr.status).lower(), "draft"
                )
    except Exception as exc:
        logger.debug("Could not refresh project statuses from runs: %s", exc)


def _compat_project_entry_from_orm_row(row: Any) -> Dict[str, Any]:
    pid = str(row.project_id or "").strip()
    return {
        "id": pid,
        "project_id": pid,
        "name": _compat_clean_project_name(row.name, f"Project {pid[-6:]}"),
        "description": "",
        "source": row.adapter or "fabric",
        "adapter": row.adapter or "fabric",
        "workspace_id": row.workspace_id or "",
        "account_id": row.account_id or None,
        "connection_tag": row.connection_tag or None,
        "target_type": "snowflake",
        "folder_id": None,
        "status": "draft",
        "created_at": row.last_updated.isoformat() if row.last_updated else _compat_now_iso(),
        "updated_at": row.last_updated.isoformat() if row.last_updated else _compat_now_iso(),
        "notification_email": row.notification_email or None,
    }


def _compat_db_project_ids() -> Optional[List[str]]:
    """Real (non-preview) project ids known to Postgres: the projects table
    plus every project that has a saved config version. None when the
    database cannot be read."""
    try:
        from semabridge.repository.orm.models import Project
        from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager
        from semabridge.api.services.project_config_store import project_ids_with_config
        from sqlalchemy import select

        with _orm_db_manager.get_session() as session:
            rows = session.execute(select(Project.project_id, Project.deleted_at)).all()
        ids = {str(r[0]).strip() for r in rows if r[0] and r[1] is None}
        deleted = {str(r[0]).strip() for r in rows if r[0] and r[1] is not None}
        ids.update(project_ids_with_config())
    except Exception as exc:
        logger.error("Project list could not be read from the database: %s", exc)
        return None
    # Deletion comes from the database (tombstone rows), never from this
    # process's cached set: another process may have deleted or re-created it.
    return sorted(
        pid for pid in ids
        if pid and pid != "preview" and not pid.startswith("preview-") and pid not in deleted
    )


def _compat_bootstrap_projects_from_orm() -> None:
    """Register every real project of the Postgres projects table in the
    cache (entries already present -- from a config version or the store --
    keep their richer metadata)."""
    try:
        from semabridge.repository.orm.models import Project
        from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager
        from sqlalchemy import select

        with _orm_db_manager.get_session() as session:
            rows = session.execute(
                select(Project).order_by(Project.last_updated.desc().nullslast())
            ).scalars().all()
            entries = [
                _compat_project_entry_from_orm_row(row) for row in rows
                if str(row.project_id or "").strip()
                and not str(row.project_id).startswith("preview")
                and getattr(row, "deleted_at", None) is None
            ]

        for entry in entries:
            pid = entry["id"]
            if pid in _compat_deleted_project_ids:
                continue
            existing = _compat_projects.get(pid)
            if isinstance(existing, dict):
                for key in ("notification_email", "connection_tag", "account_id"):
                    if entry.get(key) and not existing.get(key):
                        existing[key] = entry[key]
                continue
            _compat_projects[pid] = entry
            _compat_project_runs.setdefault(pid, [])
            _compat_project_snapshots.setdefault(pid, [])
            _compat_snapshot_groups.setdefault(pid, [])
    except Exception as exc:
        logger.error("ORM project bootstrap failed: %s", exc)


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
    try:
        from semabridge.api.services.project_config_store import save_project_config

        save_project_config(project_id, yaml_text, reason="import_repo_yaml", export=False)
    except Exception as exc:
        logger.error("Repo YAML project %s could not be saved to the database: %s", project_id, exc)
        _compat_project_configs[project_id] = yaml_text
    _compat_project_runs.setdefault(project_id, [])
    _compat_project_snapshots.setdefault(project_id, [])
    _compat_snapshot_groups.setdefault(project_id, [])


# ---------------------------------------------------------------------------
# Postgres is the authority for projects; the dicts above are only a cache.
#
# With more than one server process (uvicorn --workers N, several hosts) each
# process has its own copy of the dicts and the JSON store is last-writer-wins,
# so nothing that decides behaviour may come from them alone:
#   * existence / deletion: the ``projects`` row (``deleted_at`` = tombstone)
#     plus the project's config versions;
#   * metadata that drives decisions (owner, status, sync_mode, folder): the
#     row's columns, overlaid onto the cached entry on every read-through.
# When the database cannot be read the cache is used as a fallback.
# ---------------------------------------------------------------------------

_PROJECT_DB_META_FIELDS = ("owner_user_id", "status", "sync_mode", "folder_id")
_project_tables_ready: set = set()
_project_tables_lock = threading.Lock()


class ProjectPersistenceError(_InternalError):
    """A project write could not be saved to the database (HTTP 500)."""


def _orm_db():
    from semabridge.repository.orm.session_factory import db_manager as _orm_db_manager

    return _orm_db_manager


def _compat_is_transient_project_id(project_id: Any) -> bool:
    pid = str(project_id or "").strip()
    return not pid or pid.startswith("preview")


def _ensure_project_tables() -> None:
    """Create the projects table on the current engine if missing (fresh
    SQLite test DBs). On PostgreSQL Alembic owns it; a no-op once it exists."""
    from semabridge.repository.orm.base import Base
    from semabridge.repository.orm.models import Project

    engine = _orm_db().get_engine()
    key = id(engine)
    if key in _project_tables_ready:
        return
    with _project_tables_lock:
        if key in _project_tables_ready:
            return
        Base.metadata.create_all(bind=engine, tables=[Project.__table__], checkfirst=True)
        _project_tables_ready.add(key)


def _compat_db_deleted_project_ids() -> Optional[set]:
    """Ids of soft-deleted projects in the database; None when it cannot be read."""
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Project

        _ensure_project_tables()
        with _orm_db().get_session() as session:
            rows = session.execute(select(Project.project_id).where(Project.deleted_at.isnot(None))).all()
        return {str(r[0]).strip() for r in rows if r[0]}
    except Exception as exc:  # noqa: BLE001 -- caller falls back to the cache
        logger.warning("Deleted projects could not be read from the database: %s", exc)
        return None


def _compat_deleted_ids() -> set:
    """The deleted project ids: from the database (refreshing the cached set
    the JSON store keeps), or the cached set when the database is unreadable."""
    db_ids = _compat_db_deleted_project_ids()
    if db_ids is None:
        return set(_compat_deleted_project_ids)
    with _compat_store_lock:
        _compat_deleted_project_ids.clear()
        _compat_deleted_project_ids.update(db_ids)
    return set(db_ids)


def _compat_project_db_row(project_id: str) -> tuple:
    """(state, row) for one project: state is "live", "deleted", "missing" or
    "unavailable" (database unreadable). ``row`` is a detached Project."""
    try:
        from semabridge.repository.orm.models import Project

        _ensure_project_tables()
        with _orm_db().get_session() as session:
            row = session.get(Project, project_id)
            if row is not None:
                session.expunge(row)
    except Exception as exc:  # noqa: BLE001 -- caller falls back to the cache
        logger.warning("Project %s could not be read from the database: %s", project_id, exc)
        return "unavailable", None
    if row is None:
        return "missing", None
    if row.deleted_at is not None:
        return "deleted", row
    return "live", row


def _compat_is_project_deleted(project_id: str) -> bool:
    """Deleted according to the database (the cached set only when the
    database cannot be read)."""
    pid = str(project_id or "").strip()
    state, _ = _compat_project_db_row(pid)
    if state == "unavailable":
        return pid in _compat_deleted_project_ids
    return state == "deleted"


def _compat_apply_db_meta(entry: Dict[str, Any], row: Any) -> Dict[str, Any]:
    """Overlay the authoritative row fields onto a cached project entry."""
    pid = str(row.project_id or "").strip()
    if row.name and str(row.name).strip() and str(row.name).strip() != pid:
        entry["name"] = row.name
    for field in ("workspace_id", "account_id", "connection_tag"):
        value = getattr(row, field, None)
        if value:
            entry[field] = value
    if row.notification_email:
        entry["notification_email"] = row.notification_email
    owner = str(getattr(row, "owner_user_id", None) or "").strip()
    if owner:
        entry["owner_user_id"] = owner
        entry["user_id"] = owner
    status = str(getattr(row, "status", None) or "").strip()
    if status:
        entry["status"] = status
    sync_mode = str(getattr(row, "sync_mode", None) or "").strip().lower()
    if sync_mode:
        entry["sync_mode"] = sync_mode
    folder_id = getattr(row, "folder_id", None)
    if folder_id is not None:  # "" = explicitly no folder
        entry["folder_id"] = folder_id or None
    return entry


def _compat_evict_project(project_id: str, cache: Optional[Dict[str, Any]] = None) -> None:
    """Drop a (deleted) project from every per-process cache."""
    pid = str(project_id or "").strip()
    with _compat_store_lock:
        _compat_deleted_project_ids.add(pid)
        if cache is not None:
            cache.pop(pid, None)
        for container in (
            _compat_projects, _compat_project_configs, _compat_project_runs, _compat_project_snapshots,
            _compat_snapshot_groups, _compat_run_snapshots, _compat_project_schedules,
        ):
            container.pop(pid, None)
        for mapping_id, mapping in list(_compat_mappings.items()):
            if isinstance(mapping, dict) and str(mapping.get("project_id") or "").strip() == pid:
                _compat_mappings.pop(mapping_id, None)


def _compat_entry_from_config(project_id: str, config_text: str) -> Dict[str, Any]:
    """A cache entry for a project known only by its saved config."""
    try:
        parsed = yaml.safe_load(config_text) or {}
    except yaml.YAMLError as exc:
        logger.warning("Project config for %s is not valid YAML: %s", project_id, exc)
        parsed = {}
    parsed = parsed if isinstance(parsed, dict) else {}
    metadata = parsed.get("project_metadata") if isinstance(parsed.get("project_metadata"), dict) else {}
    return _compat_project_payload(project_id, {
        "name": metadata.get("name") or parsed.get("display_name") or parsed.get("project_name") or project_id,
        "source": parsed.get("source") if isinstance(parsed.get("source"), dict) else {},
        "target": parsed.get("target") if isinstance(parsed.get("target"), dict) else {},
        "targets": parsed.get("targets") if isinstance(parsed.get("targets"), list) else [],
        "user_id": parsed.get("owner_user_id") or metadata.get("owner_user_id"),
    })


def _compat_get_project(project_id: str, cache: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Read-through project lookup: the database row decides whether the
    project exists (another process may have created or deleted it) and
    supplies its authoritative metadata; ``cache`` (default: the shared
    ``_compat_projects``) is refreshed in place. None when it does not exist
    or is deleted."""
    pid = str(project_id or "").strip()
    cache = _compat_projects if cache is None else cache
    if not pid:
        return None
    if _compat_is_transient_project_id(pid):
        return cache.get(pid)
    state, row = _compat_project_db_row(pid)
    if state == "unavailable":
        return None if pid in _compat_deleted_project_ids else cache.get(pid)
    if state == "deleted":
        _compat_evict_project(pid, cache)
        return None
    with _compat_store_lock:
        entry = cache.get(pid)
        if state == "live":
            if not isinstance(entry, dict):
                entry = _compat_project_entry_from_orm_row(row)
            _compat_apply_db_meta(entry, row)
            cache[pid] = entry
            if cache is not _compat_projects:
                _compat_projects.setdefault(pid, entry)
            _compat_project_runs.setdefault(pid, [])
            _compat_deleted_project_ids.discard(pid)
            return entry
        if isinstance(entry, dict):
            # No row yet (e.g. registered from a project file); the config
            # store owns its existence.
            return entry
    # Not cached and no row: a project known only by its saved config.
    from semabridge.api.services.project_config_store import get_project_config_text

    config_text = get_project_config_text(pid)
    if not str(config_text or "").strip():
        return None
    entry = _compat_entry_from_config(pid, config_text)
    with _compat_store_lock:
        cache[pid] = entry
        if cache is not _compat_projects:
            _compat_projects.setdefault(pid, entry)
        _compat_project_runs.setdefault(pid, [])
    return entry


def resolve_canonical_project_id(project_id: Optional[str]) -> Optional[str]:
    """Resolve a project name, slug, or id to its canonical project_id."""
    if not project_id or not str(project_id).strip():
        return None
    raw = str(project_id).strip()
    if _compat_get_project(raw):
        return raw

    # 1. DB search by project_id or name (case-insensitive)
    try:
        from semabridge.repository.orm.session_factory import db_manager
        from semabridge.repository.orm.models import Project
        from sqlalchemy import func

        with db_manager.get_session() as s:
            match = s.query(Project.project_id).filter(
                func.lower(Project.project_id) == raw.lower(),
                Project.deleted_at.is_(None),
            ).first()
            if match and match[0]:
                return str(match[0])

            match_name = s.query(Project.project_id).filter(
                func.lower(Project.name) == raw.lower(),
                Project.deleted_at.is_(None),
            ).first()
            if match_name and match_name[0]:
                return str(match_name[0])
    except Exception as exc:
        logger.debug("Canonical project id DB lookup error: %s", exc)

    # 2. Check _compat_projects cache by id or name
    with _compat_store_lock:
        for k, v in _compat_projects.items():
            if not isinstance(v, dict):
                continue
            if k.lower() == raw.lower():
                return k
            name = str(v.get("name") or v.get("semantic_name") or "").strip()
            if name.lower() == raw.lower():
                return k

    # 3. Check modular project YAMLs on disk
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    for candidate in [raw, f"proj-{slug}", slug, f"proj-{raw}"]:
        if _compat_project_yaml_path(candidate).is_file():
            return candidate

    if slug and not (raw.startswith("proj-") or raw.startswith("preview-")):
        return f"proj-{slug}"
    return raw



def _compat_refresh_projects_from_db() -> Optional[List[str]]:
    """Bulk read-through for project lists: one query of the projects table.
    Deleted projects are evicted, missing live ones added, and every cached
    entry gets its authoritative metadata. Returns the live (non-preview)
    project ids, most recently updated first; None when the database cannot
    be read."""
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.models import Project

        _ensure_project_tables()
        with _orm_db().get_session() as session:
            rows = session.execute(
                select(Project).order_by(Project.last_updated.desc().nullslast())
            ).scalars().all()
            for row in rows:
                session.expunge(row)
    except Exception as exc:  # noqa: BLE001 -- caller falls back to the cache
        logger.error("Project list could not be read from the database: %s", exc)
        return None
    live: List[str] = []
    deleted: set = set()
    with _compat_store_lock:
        for row in rows:
            pid = str(row.project_id or "").strip()
            if _compat_is_transient_project_id(pid):
                continue
            if row.deleted_at is not None:
                deleted.add(pid)
                continue
            entry = _compat_projects.get(pid)
            if not isinstance(entry, dict):
                entry = _compat_project_entry_from_orm_row(row)
                _compat_projects[pid] = entry
                _compat_project_runs.setdefault(pid, [])
                _compat_project_snapshots.setdefault(pid, [])
                _compat_snapshot_groups.setdefault(pid, [])
            _compat_apply_db_meta(entry, row)
            live.append(pid)
        _compat_deleted_project_ids.clear()
        _compat_deleted_project_ids.update(deleted)
    for pid in deleted:
        if pid in _compat_projects:
            _compat_evict_project(pid)
    return live


def _compat_write_project_meta(
    project_id: str, *, strict: bool = False, create: bool = True, **fields: Any
) -> bool:
    """Write project metadata columns (owner_user_id, status, sync_mode,
    folder_id) to the database row; a missing row is created only with
    ``create`` (never for a deleted project). Returns True when saved; with
    ``strict`` a failure raises ProjectPersistenceError."""
    pid = str(project_id or "").strip()
    if not pid or _compat_is_transient_project_id(pid):
        return False
    values = {k: v for k, v in fields.items() if k in _PROJECT_DB_META_FIELDS}
    if not values:
        return False
    try:
        from datetime import timezone as _tz
        from semabridge.repository.orm.models import Project

        _ensure_project_tables()
        with _orm_db().get_session() as session:
            row = session.get(Project, pid)
            if row is not None and row.deleted_at is not None:
                return False
            if row is None:
                if not create:
                    return False
                cached = _compat_projects.get(pid) or {}
                row = Project(project_id=pid, name=str(cached.get("name") or pid))
                session.add(row)
            for key, value in values.items():
                setattr(row, key, value)
            row.last_updated = datetime.now(_tz.utc)
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise ProjectPersistenceError(f"Project {pid} could not be saved to the database: {exc}") from exc
        logger.warning("Project %s metadata %s not saved to the database: %s", pid, sorted(values), exc)
        return False


def _compat_project_sync_mode(project_id: str) -> Optional[str]:
    """The project's stored sync mode ("copy"/"upsert") from its database row
    (the cached value only when the database cannot be read)."""
    pid = str(project_id or "").strip()
    state, row = _compat_project_db_row(pid)
    if state == "unavailable":
        value = (_compat_projects.get(pid) or {}).get("sync_mode")
    elif state == "live":
        value = row.sync_mode
    else:
        value = None
    token = str(value or "").strip().lower()
    return token if token in {"copy", "upsert"} else None


def _compat_soft_delete_project_in_db(project_id: str) -> None:
    """Persist a project delete: tombstone the row (creating it when the
    project only had a config) and drop its runs/snapshots as the former
    hard delete's cascade did. Raises ProjectPersistenceError on failure."""
    pid = str(project_id or "").strip()
    if not pid:
        return
    try:
        from datetime import timezone as _tz
        from semabridge.repository.orm.models import Project

        _ensure_project_tables()
        now = datetime.now(_tz.utc)
        with _orm_db().get_session() as session:
            row = session.get(Project, pid)
            if row is None:
                row = Project(project_id=pid, name=pid)
                session.add(row)
            else:
                for child in list(row.snapshots or []) + list(row.runs or []):
                    session.delete(child)
            row.deleted_at = now
            row.last_updated = now
            session.commit()
    except Exception as exc:  # noqa: BLE001
        raise ProjectPersistenceError(f"Delete of project {pid} could not be saved to the database: {exc}") from exc


def _compat_sync_store_to_db() -> None:
    """Move what only the JSON store knew into the database (idempotent):
    deleted project ids become tombstones, and cached metadata (owner,
    status, sync_mode, folder) fills columns the row does not have yet. The
    database values win from then on."""
    try:
        from datetime import timezone as _tz
        from semabridge.repository.orm.models import Project

        _ensure_project_tables()
        now = datetime.now(_tz.utc)
        with _compat_store_lock:
            deleted = sorted(p for p in _compat_deleted_project_ids if not _compat_is_transient_project_id(p))
            cached = {
                pid: dict(entry) for pid, entry in _compat_projects.items()
                if isinstance(entry, dict) and not _compat_is_transient_project_id(pid)
            }
        changed = 0
        with _orm_db().get_session() as session:
            for pid in deleted:
                row = session.get(Project, pid)
                if row is None:
                    session.add(Project(project_id=pid, name=pid, deleted_at=now, last_updated=now))
                    changed += 1
                elif row.deleted_at is None and pid not in cached:
                    row.deleted_at = now
                    changed += 1
            for pid, entry in cached.items():
                if pid in deleted:
                    continue
                row = session.get(Project, pid)
                if row is None or row.deleted_at is not None:
                    continue
                for field in _PROJECT_DB_META_FIELDS:
                    if getattr(row, field, None) is not None:
                        continue
                    value = entry.get(field)
                    if field == "owner_user_id":
                        value = value or entry.get("user_id")
                    if field == "sync_mode" and str(value or "").lower() not in {"copy", "upsert"}:
                        value = None
                    if value is None or value == "":
                        continue
                    setattr(row, field, str(value))
                    changed += 1
            session.commit()
        if changed:
            logger.info("Moved %d project metadata value(s) / tombstone(s) from the compat store to the database", changed)
    except Exception as exc:  # noqa: BLE001 -- retried on the next start
        logger.warning("Compat store metadata could not be moved to the database: %s", exc)


_compat_bootstrapped: bool = False


def _compat_ensure_loaded() -> None:
    global _compat_bootstrapped
    if _compat_bootstrapped:
        return
    with _compat_store_lock:
        if _compat_bootstrapped:
            return
        _compat_ensure_loaded_locked()


def _compat_ensure_loaded_locked() -> None:
    global _compat_bootstrapped
    _compat_load_store()
    # Deleted ids the JSON store knew become database tombstones before
    # anything below decides visibility from the database.
    _compat_sync_store_to_db()
    _compat_deleted_ids()
    _compat_bootstrap_projects_from_modular_configs()
    _compat_bootstrap_projects_from_orm()
    _compat_bootstrap_project_from_repo_yaml()
    # Always refresh statuses from the Run table regardless of how _compat_projects
    # was populated (store file, ORM bootstrap, or YAML discovery).  The store file
    # may hold stale "draft" values for projects that have had successful runs.
    _compat_refresh_project_statuses_from_runs()
    # Cached metadata fills database columns not set yet; then the database
    # values (owner, status, sync_mode, folder) win in the cache.
    _compat_sync_store_to_db()
    _compat_refresh_projects_from_db()
    _compat_bootstrapped = True


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
    except Exception as exc:
        logger.debug("Could not read repo YAML text: %s", exc)
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
