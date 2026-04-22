"""
Shared core-service bootstrap and helpers.

This module owns the common imports, app-level singletons, and helper
functions that the split core service implementation modules reuse.
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
    pass  # python-dotenv optional; OS environment vars are used as-is.

from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import asyncio
from fastapi import HTTPException, Depends, Query
from starlette.responses import Response
import hashlib
import json
import os
import re
import yaml
import logging

# Windows-specific asyncio stability:
# use the selector loop instead of Proactor to avoid intermittent
# `_ProactorBaseWritePipeTransport._loop_writing` assertion failures.
if os.name == "nt":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

from semabridge.core.settings import get_settings, reload_settings
from semabridge.repository.model_repository import ModelRepository
from semabridge.core.execution_engine import ExecutionEngine
from semabridge.utils.logger import setup_logging
from semabridge.auth.fabric_validator import fabric_validator
from semabridge.api.semantic_models import SemanticSyncRequest, SemanticRefreshRequest
from semabridge.api.services.connection_domain_service import (
    _extract_bearer_token,
    _resolve_fabric_access_token,
)
from semabridge.api.services.scheduler_service import SchedulerService
from semabridge.api.services.sync_execution_service import execute_sync_request
from semabridge.api.services.version_control_service import VersionControlService
import time as _time

# setup_logging(level="INFO")  # Centralized in app_setup.py

logger = logging.getLogger("semabridge.api")

db_manager = ModelRepository()
engine = ExecutionEngine(db_manager=db_manager)

settings = get_settings()
scheduler_service = SchedulerService()
version_control_service: Optional[VersionControlService] = None

# Content hash tracker -- avoids duplicate versions for unchanged files
_last_snapshot_hash: dict[str, str] = {}

# Shared discovery cache for the core endpoints. Versioning code also uses
# this to invalidate stale discovery results after rollback/delete actions.
_discovery_cache: Dict[str, Any] = {}
_DISCOVERY_CACHE_TTL = 60


def _normalize_yaml_windows_path_fields(yaml_text: str) -> str:
    """Normalize backslashes in known YAML path fields to forward slashes."""
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
    """Resolve the local models directory using a strict precedence chain."""
    env_val = os.environ.get("SEMABRIDGE_LOCAL_MODELS_PATH")
    if env_val:
        return Path(env_val).expanduser().resolve()

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

    return Path.cwd()


def _snapshot_model_if_changed(
    model_id: str, content: str, author: str = "discovery",
) -> str | None:
    """Create a version row only when model content actually changed."""
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if _last_snapshot_hash.get(model_id) == content_hash:
        return None

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError:
        parsed = {"raw": content}

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


__all__ = [name for name in globals() if not name.startswith("__")]
