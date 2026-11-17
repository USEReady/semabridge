"""Semantic / sync API controller.

Routes:
    POST /api/semantic/sync      — semantic-aware sync
    POST /api/semantic/refresh    — refresh semantic metadata
    POST /api/sync               — full sync pipeline
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterable, Optional

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


class TriggerSyncRequest(BaseModel):
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    sync_mode: Optional[str] = None

    class Config:
        extra = "allow"

from semabridge.api.services.core_domain_service import semantic_refresh, semantic_sync, sync_models
from semabridge.api.services.project_ownership_service import (
    assert_project_access,
    auth_is_enabled,
    require_request_user_id,
    validate_project_connector_accounts_belong_to_user,
)
from semabridge.domain.exceptions import ValidationError

router = APIRouter()
router.post('/api/semantic/sync', response_model=None)(semantic_sync)
router.post('/api/semantic/refresh', response_model=None)(semantic_refresh)


# Options a caller of POST /api/sync may set when auth is enabled.
_ALLOWED_SYNC_KEYS = frozenset({
    "project_id",
    "content",
    "sync_mode",
    "account_id",
    "dry_run",
    "force",
    "max_parallel_models",
    "max_workers",
    "user_id",  # always overwritten with the authenticated user
})

# Server-side execution tuning / internal plumbing: never user-controlled
# when auth is enabled (executor choice, process pool sizing, and internal
# cross-project references used by dry-run/run services).
_SERVER_CONTROLLED_SYNC_KEYS = frozenset({
    "executor",
    "concurrency_executor",
    "process_max_workers",
    "max_parallel_fabric_jobs",
    "binding_project_id",
    "delta_baseline_project_id",
    "delta_preview_overrides",
    "project_run_id",
})

# Config keys that carry a local source-file path.
_SOURCE_FILE_KEYS = frozenset({
    "pbix_path",
    "pbix_file_path",
    "pbix_paths",
    "pbix_files",
    "file_path",
    "twb_path",
    "twbx_path",
    "tds_path",
    "tdsx_path",
})


def _iter_source_file_paths(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in _SOURCE_FILE_KEYS:
                if isinstance(value, str) and value.strip():
                    yield value
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            yield item
                        elif isinstance(item, dict):
                            yield from _iter_source_file_paths(item)
                elif isinstance(value, dict):
                    yield from _iter_source_file_paths(value)
            else:
                yield from _iter_source_file_paths(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_source_file_paths(item)


def _sanitize_sync_payload_for_user(payload: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """Enforce per-user boundaries on a /api/sync request (auth enabled).

    - unknown options are rejected; executor/tuning/internal keys are dropped;
    - ``user_id`` is always the authenticated user;
    - ``project_id`` (and any project_id inside ``content``) must be owned;
    - connector accounts referenced in ``content`` must belong to the user
      (``account_id`` itself is validated in ``core_sync_impl.sync_models``);
    - local source-file paths in ``content`` must be under an allowed root.
    """
    from semabridge.auth.file_access import allowed_source_roots, resolve_allowed_path

    payload = dict(payload)
    dropped = sorted(k for k in payload if k in _SERVER_CONTROLLED_SYNC_KEYS)
    if dropped:
        logger.warning("[SyncAuth] ignoring server-controlled sync options from user_id=%s: %s", user_id, dropped)
        for key in dropped:
            payload.pop(key, None)
    unknown = sorted(k for k in payload if k not in _ALLOWED_SYNC_KEYS)
    if unknown:
        raise ValidationError(f"Unsupported sync options: {', '.join(unknown)}")

    payload["user_id"] = user_id

    project_id = str(payload.get("project_id") or "").strip()
    if project_id:
        assert_project_access(project_id, user_id, log_prefix="SyncAuth")

    content = payload.get("content")
    if content:
        if not isinstance(content, str):
            raise ValidationError("content must be a YAML string")
        try:
            config = yaml.safe_load(content) or {}
        except yaml.YAMLError as exc:
            raise ValidationError(f"Invalid YAML content: {exc}") from exc
        if not isinstance(config, dict):
            raise ValidationError("content must be a YAML mapping")
        config_project_id = str(config.get("project_id") or "").strip()
        if config_project_id and config_project_id != project_id:
            assert_project_access(config_project_id, user_id, log_prefix="SyncAuth")
        validate_project_connector_accounts_belong_to_user(user_id, config)
        roots = None
        for raw_path in _iter_source_file_paths(config):
            if roots is None:
                roots = allowed_source_roots()
            resolve_allowed_path(raw_path, expect="any", roots=roots)
    elif not project_id:
        raise ValidationError("project_id or content is required")

    return payload


@router.post("/api/sync")
async def trigger_sync(request: Request, payload: TriggerSyncRequest) -> Any:
    """Trigger a full sync with user-scoped credential isolation.

    When auth is enabled, the authenticated ``user_id`` from the JWT always
    replaces any client-supplied one, project ownership and account
    ownership are enforced, and execution options (executor, process pool
    sizing, internal cross-project references) are not user-controlled.
    With auth disabled the payload is forwarded unchanged (single-user mode).
    """
    payload_dict = payload.model_dump(exclude_none=False)
    if auth_is_enabled():
        user_id = require_request_user_id(request)
        payload_dict = await asyncio.to_thread(_sanitize_sync_payload_for_user, payload_dict, str(user_id))

    return await sync_models(payload_dict)
