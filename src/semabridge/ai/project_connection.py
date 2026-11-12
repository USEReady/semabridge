"""Resolve a project's own warehouse connection and deployed semantic view.

AI Studio, verified queries and reconciliation must run against the
connection and deployment target of the *selected project* — never a
hard-coded view name and never another user's credentials.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.ai.project_connection")

_ACCOUNT_KEYS = ("target_account_id", "account_id", "source_account_id")


@dataclass
class ProjectScope:
    """Everything the AI layer needs to stay inside one project."""

    project_id: Optional[str]
    semantic_view: Optional[str] = None
    model_name: Optional[str] = None
    deploy_error: Optional[str] = None
    allowed_domains: Optional[List[str]] = None
    domain_names: List[str] = field(default_factory=list)


def _project_meta(project_id: str) -> Dict[str, Any]:
    try:
        from semabridge.api.services.project_shared import _compat_ensure_loaded, _compat_projects

        _compat_ensure_loaded()
        meta = _compat_projects.get(project_id)
        return meta if isinstance(meta, dict) else {}
    except Exception as exc:
        logger.debug("Project metadata unavailable for %s: %s", project_id, exc)
        return {}


def _project_target_fields(meta: Dict[str, Any]) -> Dict[str, str]:
    from semabridge.auth.credential_builder import project_snowflake_target

    target = meta.get("target")
    if not isinstance(target, dict):
        targets = meta.get("targets")
        target = targets[0] if isinstance(targets, list) and targets and isinstance(targets[0], dict) else None
    if not target:
        return {}

    class _Cfg:  # minimal adapter for project_snowflake_target
        pass

    cfg = _Cfg()
    cfg.target = target  # type: ignore[attr-defined]
    return project_snowflake_target(cfg)


def _candidate_account_ids(meta: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key in _ACCOUNT_KEYS:
        val = str(meta.get(key) or "").strip()
        if val and val not in out:
            out.append(val)
    return out


def _has_credentials(cfg, connector: str) -> bool:
    if cfg is None:
        return False
    if connector == "SNOWFLAKE":
        return bool(
            getattr(cfg, "password", None)
            or getattr(cfg, "private_key", None)
            or getattr(cfg, "oauth_client_id", None)
            or str(getattr(cfg, "auth_type", "") or "").lower().strip() == "externalbrowser"
        )
    if connector == "DATABRICKS":
        return bool(getattr(cfg, "token", None) or getattr(cfg, "client_id", None))
    return False


def _build_from_owner(owner_id: str, connector: str, base, meta: Dict[str, Any]):
    try:
        uid = int(str(owner_id).strip())
    except (TypeError, ValueError):
        return None
    try:
        from sqlalchemy import select
        from semabridge.repository.orm.account_models import Account
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            rows = (
                session.execute(
                    select(Account).where(
                        Account.owner_id == uid,
                        Account.connector_type == connector,
                    )
                )
                .scalars()
                .all()
            )
            active = [a for a in rows if str(getattr(a, "status", "") or "").lower() == "active"]
            candidates = active or rows
            for account in candidates:
                built = _build_from_account(account.id, connector, base, meta)
                if built is not None and _has_credentials(built, connector):
                    return built
    except Exception as exc:
        logger.warning("Could not resolve %s account for owner %s: %s", connector, owner_id, exc)
    return None


def resolve_project_warehouse_config(
    project_id: Optional[str],
    connector: str = "SNOWFLAKE",
    owner_id: Optional[str] = None,
):
    """Return the warehouse config for the project (or global config when no project).

    Order: the project's linked account of the requested connector type, with
    the project's target database/schema/warehouse/role applied; otherwise the
    global (system-scope) configuration, or the authenticated owner's own
    connected account of that type when the global config has no credentials.
    Never borrows other users' secrets.
    """
    from semabridge.core.settings import reload_settings

    connector = connector.upper()
    cfg = reload_settings()
    base = cfg.snowflake if connector == "SNOWFLAKE" else cfg.databricks
    meta = _project_meta(project_id) if project_id else {}

    for account_id in _candidate_account_ids(meta):
        built = _build_from_account(account_id, connector, base, meta)
        if built is not None:
            return built

    if owner_id and not _has_credentials(base, connector):
        built = _build_from_owner(owner_id, connector, base, meta)
        if built is not None:
            return built

    if connector == "SNOWFLAKE" and base is not None and meta:
        from semabridge.auth.credential_builder import apply_project_snowflake_target

        return apply_project_snowflake_target(base, _project_target_fields(meta))
    return base


def _build_from_account(account_id: str, connector: str, base, meta: Dict[str, Any]):
    try:
        from semabridge.repository.account_repository import AccountRepository
        from semabridge.repository.orm.session_factory import db_manager

        with db_manager.get_session() as session:
            account = AccountRepository(session).get_account_by_id(account_id)
            if not account or str(account.connector_type or "").upper() != connector:
                return None
            if connector == "SNOWFLAKE":
                from semabridge.auth.credential_builder import build_snowflake_config

                return build_snowflake_config(account, session, base, project_target=_project_target_fields(meta))
            if connector == "DATABRICKS":
                from semabridge.auth.credential_builder import build_databricks_config

                return build_databricks_config(account, session, base)
    except Exception as exc:
        logger.warning("Could not build %s config from account %s: %s", connector, account_id, exc)
    return None


def resolve_project_scope(project_id: Optional[str], explicit_view: Optional[str] = None) -> ProjectScope:
    """Resolve the deployed view and the attached domains for a project.

    Without a project id the scope is unrestricted (``allowed_domains=None``).
    With a project id only that project's attached domains — or, if none are
    attached, the domains generated from it — are allowed; an empty list means
    the project has no ontology yet.
    """
    pid = (project_id or "").strip() or None
    scope = ProjectScope(project_id=pid, semantic_view=(explicit_view or "").strip() or None)
    if not pid:
        return scope

    try:
        from semabridge.repository.model_repository import ModelRepository

        info = ModelRepository().get_project_deployment_info(pid)
        scope.model_name = info.get("model_name")
        scope.deploy_error = info.get("deploy_error")
        scope.semantic_view = scope.semantic_view or info.get("semantic_view")
    except Exception as exc:
        logger.debug("Deployment info unavailable for project %s: %s", pid, exc)

    from semabridge.ontology.store import ontology_store

    domains = list(ontology_store.get_project_domains(pid) or [])
    if not domains:
        fed = ontology_store.get_federated_ontology()
        domains = [d for d in fed.domains.values() if getattr(d, "source_project_id", None) == pid]
    scope.domain_names = [d.domain for d in domains]
    scope.allowed_domains = [d.domain for d in domains] + [d.id for d in domains if getattr(d, "id", None)]
    if not scope.semantic_view:
        scope.semantic_view = next(
            (d.semantic_view_name for d in domains if getattr(d, "semantic_view_name", None)),
            None,
        )
    return scope

