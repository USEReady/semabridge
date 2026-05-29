from __future__ import annotations

import os
from typing import Any, Dict, Iterable, Optional

import yaml
from fastapi import HTTPException, Request
from sqlalchemy import select

from semabridge.api.services.project_shared import (
    _compat_ensure_loaded,
    _compat_load_project_yaml_text,
    _compat_project_configs,
    _compat_projects,
    _compat_save_project_yaml_text,
    _compat_save_store,
    logger,
)
from semabridge.repository.orm.session_factory import db_manager


def auth_is_enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "true").lower() == "true"


def get_request_user_id(request: Request) -> str | None:
    if not auth_is_enabled():
        return None
    value = getattr(request.state, "user_id", None)
    token = str(value or "").strip()
    return token or None


def require_request_user_id(request: Request) -> str | None:
    user_id = get_request_user_id(request)
    if auth_is_enabled() and not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def _normalize_owner_user_id(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _parse_project_yaml_dict(project_id: str) -> Dict[str, Any]:
    raw_yaml = str(
        _compat_project_configs.get(project_id)
        or _compat_load_project_yaml_text(project_id)
        or ""
    ).strip()
    if not raw_yaml:
        return {}
    try:
        parsed = yaml.safe_load(raw_yaml) or {}
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _owner_from_project_yaml(parsed: Dict[str, Any]) -> str | None:
    metadata = parsed.get("project_metadata") if isinstance(parsed.get("project_metadata"), dict) else {}
    return (
        _normalize_owner_user_id(parsed.get("owner_user_id"))
        or _normalize_owner_user_id(parsed.get("user_id"))
        or _normalize_owner_user_id(metadata.get("owner_user_id"))
        or _normalize_owner_user_id(metadata.get("user_id"))
    )


def _iter_project_account_ids(project: Dict[str, Any], parsed_yaml: Dict[str, Any]) -> Iterable[str]:
    seen: set[str] = set()

    def _add(value: Any) -> None:
        token = str(value or "").strip()
        if token and token not in seen:
            seen.add(token)

    def _scan_dict(value: Any) -> None:
        if not isinstance(value, dict):
            return
        _add(value.get("identity_id"))
        _add(value.get("account_id"))

    for key in ("account_id", "source_account_id", "target_account_id"):
        _add(project.get(key))

    _scan_dict(project.get("source"))
    _scan_dict(project.get("source_config"))
    _scan_dict(project.get("target"))
    targets = project.get("targets")
    if isinstance(targets, list):
        for target_cfg in targets:
            _scan_dict(target_cfg)

    _scan_dict(parsed_yaml.get("source"))
    _scan_dict(parsed_yaml.get("target"))
    yaml_targets = parsed_yaml.get("targets")
    if isinstance(yaml_targets, list):
        for target_cfg in yaml_targets:
            _scan_dict(target_cfg)

    for account_id in seen:
        yield account_id


def _recover_owner_from_accounts(account_ids: Iterable[str]) -> str | None:
    account_tokens = [str(account_id).strip() for account_id in account_ids if str(account_id).strip()]
    if not account_tokens:
        return None

    from semabridge.repository.orm.models import Account

    with db_manager.get_session() as session:
        rows = session.execute(
            select(Account.id, Account.owner_id).where(Account.id.in_(account_tokens))
        ).all()

    owner_ids = {
        str(owner_id).strip()
        for _, owner_id in rows
        if owner_id is not None and str(owner_id).strip()
    }
    if len(owner_ids) == 1:
        return next(iter(owner_ids))
    if len(owner_ids) > 1:
        logger.warning(
            "[ProjectOwnership] conflicting account owners account_ids=%s owner_ids=%s",
            sorted(account_tokens),
            sorted(owner_ids),
        )
    return None


def _persist_project_owner(project_id: str, project: Dict[str, Any], owner_user_id: str, source: str) -> None:
    normalized_owner_user_id = _normalize_owner_user_id(owner_user_id)
    if not normalized_owner_user_id:
        return

    changed = False
    if project.get("owner_user_id") != normalized_owner_user_id:
        project["owner_user_id"] = normalized_owner_user_id
        changed = True
    if project.get("user_id") != normalized_owner_user_id:
        project["user_id"] = normalized_owner_user_id
        changed = True
    if changed:
        _compat_projects[project_id] = project
        _compat_save_store()

    parsed_yaml = _parse_project_yaml_dict(project_id)
    if parsed_yaml:
        metadata = parsed_yaml.get("project_metadata") if isinstance(parsed_yaml.get("project_metadata"), dict) else {}
        yaml_changed = False
        if parsed_yaml.get("owner_user_id") != normalized_owner_user_id:
            parsed_yaml["owner_user_id"] = normalized_owner_user_id
            yaml_changed = True
        if metadata.get("owner_user_id") != normalized_owner_user_id:
            metadata["owner_user_id"] = normalized_owner_user_id
            parsed_yaml["project_metadata"] = metadata
            yaml_changed = True
        if yaml_changed:
            try:
                _compat_save_project_yaml_text(
                    project_id,
                    yaml.safe_dump(parsed_yaml, sort_keys=False, allow_unicode=False),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to persist owner_user_id for %s after %s backfill: %s",
                    project_id,
                    source,
                    exc,
                )


def ensure_project_owner(project_id: str) -> Dict[str, Any]:
    _compat_ensure_loaded()
    pid = str(project_id or "").strip()
    project = _compat_projects.get(pid)
    if not isinstance(project, dict):
        parsed_yaml = _parse_project_yaml_dict(pid)
        if parsed_yaml:
            from semabridge.api.services.project_shared import _compat_project_payload, _compat_save_store
            project = _compat_project_payload(pid, parsed_yaml)
            _compat_projects[pid] = project
            try:
                _compat_save_store()
            except Exception as exc:
                logger.warning("Failed to save store after modular project dynamic registration: %s", exc)
            logger.info("[ProjectOwnership] Dynamically constructed and registered in-memory project payload for modular project %s", pid)
        else:
            return {
                "project": None,
                "owner_user_id": None,
                "ownership_source": "missing_project",
                "recovered": False,
            }

    owner_user_id = (
        _normalize_owner_user_id(project.get("owner_user_id"))
        or _normalize_owner_user_id(project.get("user_id"))
    )
    if owner_user_id:
        if project.get("owner_user_id") != owner_user_id:
            _persist_project_owner(pid, project, owner_user_id, "project_payload")
        return {
            "project": project,
            "owner_user_id": owner_user_id,
            "ownership_source": "project_payload",
            "recovered": False,
        }

    parsed_yaml = _parse_project_yaml_dict(pid)
    owner_user_id = _owner_from_project_yaml(parsed_yaml)
    if owner_user_id:
        _persist_project_owner(pid, project, owner_user_id, "project_yaml")
        logger.info(
            "[ProjectOwnership] backfilled owner_user_id=%s for project_id=%s from project_yaml",
            owner_user_id,
            pid,
        )
        return {
            "project": project,
            "owner_user_id": owner_user_id,
            "ownership_source": "project_yaml",
            "recovered": True,
        }

    owner_user_id = _recover_owner_from_accounts(_iter_project_account_ids(project, parsed_yaml))
    if owner_user_id:
        _persist_project_owner(pid, project, owner_user_id, "legacy_account_owner")
        logger.info(
            "[ProjectOwnership] backfilled owner_user_id=%s for project_id=%s from legacy_account_owner",
            owner_user_id,
            pid,
        )
        return {
            "project": project,
            "owner_user_id": owner_user_id,
            "ownership_source": "legacy_account_owner",
            "recovered": True,
        }

    return {
        "project": project,
        "owner_user_id": None,
        "ownership_source": "unresolved",
        "recovered": False,
    }


def _is_admin_user(user_id: str | None) -> bool:
    """Return True if the given user_id belongs to an admin role user."""
    from semabridge.repository.orm.models import User
    normalized = _normalize_owner_user_id(user_id)
    if not normalized:
        return False
    try:
        with db_manager.get_session() as session:
            user = session.execute(
                select(User).where(User.id == int(normalized))
            ).scalar_one_or_none()
            return bool(user and user.role == "admin")
    except Exception:
        return False


def _user_exists(user_id: str | None) -> bool:
    """Return True if the given user_id still exists in the users table."""
    from semabridge.repository.orm.models import User
    normalized = _normalize_owner_user_id(user_id)
    if not normalized:
        return False
    try:
        with db_manager.get_session() as session:
            user = session.execute(
                select(User).where(User.id == int(normalized))
            ).scalar_one_or_none()
            return user is not None
    except Exception:
        return False


def is_project_owned_by_user(project_id: str, user_id: str | None, *, log_denied: bool = True, log_prefix: str = "ProjectAuth") -> bool:
    if not auth_is_enabled():
        return True

    normalized_user_id = _normalize_owner_user_id(user_id)
    if not normalized_user_id:
        if log_denied:
            logger.warning("[%s] deny project_id=%s reason=missing_authenticated_user", log_prefix, project_id)
        return False

    # Admin users can access all projects (single-admin / superuser scenario)
    if _is_admin_user(normalized_user_id):
        return True

    context = ensure_project_owner(project_id)
    owner_user_id = context["owner_user_id"]
    if owner_user_id and owner_user_id == normalized_user_id:
        return True

    # Backfill: unowned or default-sentinel projects → claim for the current user
    if context["project"] and (not owner_user_id or owner_user_id == "1"):
        logger.info(
            "[ProjectOwnership] dynamically backfilling owner_user_id=%s for unowned or default project_id=%s",
            normalized_user_id,
            project_id,
        )
        _persist_project_owner(project_id, context["project"], normalized_user_id, "dynamic_backfill")
        return True

    # Backfill: stored owner no longer exists in DB (e.g. after re-registration)
    # Re-assign to the current user so they're not permanently locked out.
    if context["project"] and owner_user_id and not _user_exists(owner_user_id):
        logger.info(
            "[ProjectOwnership] stored owner_user_id=%s no longer exists in DB; "
            "re-assigning project_id=%s to current user_id=%s",
            owner_user_id,
            project_id,
            normalized_user_id,
        )
        _persist_project_owner(project_id, context["project"], normalized_user_id, "stale_owner_reassign")
        return True

    if log_denied:
        logger.warning(
            "[%s] deny project_id=%s user_id=%s owner_user_id=%s ownership_source=%s project_found=%s",
            log_prefix,
            project_id,
            normalized_user_id,
            owner_user_id,
            context["ownership_source"],
            bool(context["project"]),
        )
    return False


def validate_project_connector_accounts_belong_to_user(user_id: str | None, payload: Dict[str, Any]) -> None:
    normalized_user_id = _normalize_owner_user_id(user_id)
    if not auth_is_enabled() or not normalized_user_id:
        return

    account_ids: set[str] = set()

    def _add(value: Any) -> None:
        token = str(value or "").strip()
        if token:
            account_ids.add(token)

    def _scan_dict(value: Any) -> None:
        if not isinstance(value, dict):
            return
        _add(value.get("identity_id"))
        _add(value.get("account_id"))

    _add(payload.get("account_id"))
    _scan_dict(payload.get("source"))
    _scan_dict(payload.get("target"))
    targets = payload.get("targets")
    if isinstance(targets, list):
        for target_cfg in targets:
            _scan_dict(target_cfg)

    if not account_ids:
        return

    from semabridge.repository.orm.models import Account

    with db_manager.get_session() as session:
        rows = session.execute(
            select(Account.id, Account.owner_id).where(Account.id.in_(sorted(account_ids)))
        ).all()

    mismatched = [
        str(account_id)
        for account_id, owner_id in rows
        if owner_id is not None and str(owner_id).strip() != normalized_user_id
    ]
    if mismatched:
        raise HTTPException(
            status_code=403,
            detail=f"Forbidden: connector identities belong to another user: {', '.join(sorted(mismatched))}",
        )
