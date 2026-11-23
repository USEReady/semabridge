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
    # domain display name -> fully-qualified semantic view built from it
    domain_views: Dict[str, str] = field(default_factory=dict)
    # True when the caller named the view (never re-routed)
    explicit_view: bool = False
    # Set by route_scope_to_view: the domain whose metric/concept picked the view
    answered_by_domain: Optional[str] = None
    # Set by route_scope_to_view when the question matches several views
    candidate_views: List[str] = field(default_factory=list)
    routing_message: Optional[str] = None
    routing_matches: Dict[str, List[str]] = field(default_factory=dict)


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


def _view_tail(view: Optional[str]) -> str:
    return str(view or "").strip().split(".")[-1].strip('"').casefold()


def resolve_project_scope(project_id: Optional[str], explicit_view: Optional[str] = None) -> ProjectScope:
    """Resolve the deployed view(s) and the domains for a project.

    Without a project id the scope is unrestricted (``allowed_domains=None``).
    With a project id only that project's domains (``ontology_store.domains_for``)
    are allowed; an empty list means the project has no ontology yet.

    A multi-model project has one domain (and one semantic view) per model:
    when its domains name more than one view, ``semantic_view`` is left unset
    here and :func:`route_scope_to_view` picks the view that owns the metric
    asked about. A caller-supplied ``explicit_view`` always wins.
    """
    raw_pid = (project_id or "").strip() or None
    scope = ProjectScope(project_id=raw_pid, semantic_view=(explicit_view or "").strip() or None)
    scope.explicit_view = bool(scope.semantic_view)
    if not raw_pid:
        return scope

    pid = raw_pid
    try:
        from semabridge.api.services.project_shared import resolve_canonical_project_id
        c_pid = resolve_canonical_project_id(raw_pid)
        if c_pid:
            pid = c_pid
            scope.project_id = pid
    except Exception as exc:
        logger.debug("Resolving canonical project id failed for %s: %s", raw_pid, exc)

    info: Dict[str, Any] = {}
    try:
        from semabridge.repository.model_repository import ModelRepository

        info = ModelRepository().get_project_deployment_info(pid) or {}
        scope.model_name = info.get("model_name")
        scope.deploy_error = info.get("deploy_error")
    except Exception as exc:
        logger.debug("Deployment info unavailable for project %s: %s", pid, exc)

    from semabridge.ontology.store import ontology_store

    domains = list(ontology_store.domains_for(pid))
    if not domains and raw_pid != pid:
        domains = list(ontology_store.domains_for(raw_pid))
    if not domains:
        try:
            from semabridge.ontology.per_model import sync_project_model_domains
            sync_project_model_domains(pid, ontology_store)
            domains = list(ontology_store.domains_for(pid))
        except Exception as exc:
            logger.debug("Syncing project model domains failed for %s: %s", pid, exc)

    scope.domain_names = [d.domain for d in domains]
    scope.allowed_domains = [d.domain for d in domains] + [d.id for d in domains if getattr(d, "id", None)]
    scope.domain_views = {
        d.domain: d.semantic_view_name for d in domains if getattr(d, "semantic_view_name", None)
    }
    distinct = _distinct_views(scope.domain_views.values())

    if scope.explicit_view:
        from semabridge.repository.model_repository import ModelRepository
        repo = ModelRepository()
        if hasattr(repo, "validate_project_view"):
            try:
                scope.semantic_view = repo.validate_project_view(pid, scope.semantic_view)
            except ValueError:
                raise
            except Exception as exc:
                logger.debug("Validating project view failed for %s: %s", pid, exc)
        # Restrict the ontology to the named view's domains (+ view-less shared ones).
        tail = _view_tail(scope.semantic_view)
        owners = [dn for dn, v in scope.domain_views.items() if _view_tail(v) == tail]
        if owners:
            _restrict_to(scope, domains, owners)
        return scope

    # Default when omitted = latest successful deploy
    if not scope.semantic_view:
        try:
            from semabridge.repository.model_repository import ModelRepository
            repo = ModelRepository()
            if hasattr(repo, "list_project_deployments"):
                deployments = repo.list_project_deployments(pid, include_unmanaged=True)
                latest = next((d for d in deployments if d.get("is_latest")), None)
                if latest:
                    scope.semantic_view = latest["semantic_view"]
        except Exception as exc:
            logger.debug("Listing project deployments failed for %s: %s", pid, exc)

    if len(distinct) >= 2 and not scope.semantic_view:
        # Several models -> several views: pick per question (route_scope_to_view).
        scope.semantic_view = None
        return scope
    if not scope.semantic_view:
        scope.semantic_view = info.get("semantic_view") or (distinct[0] if distinct else None)

    # If the project has a deployed semantic view or DDL, ensure allowed_domains is not an empty trap
    if not scope.allowed_domains and (scope.semantic_view or info.get("ddl")):
        scope.allowed_domains = [pid, raw_pid]
        scope.domain_names = [info.get("model_name") or pid]

    return scope


def _distinct_views(views: Any) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for v in views:
        tail = _view_tail(v)
        if tail and tail not in seen:
            seen.add(tail)
            out.append(str(v))
    return out


def _restrict_to(scope: ProjectScope, domains: List[Any], owner_names: List[str]) -> None:
    """Keep the owners' domains plus project-wide domains that have no view of their own."""
    keep = [d for d in domains if d.domain in owner_names or not getattr(d, "semantic_view_name", None)]
    scope.allowed_domains = [d.domain for d in keep] + [d.id for d in keep if getattr(d, "id", None)]
    scope.domain_names = [d.domain for d in keep]


def _norm_text(text: str) -> str:
    import re

    return " " + " ".join(re.findall(r"[a-z0-9%]+", str(text or "").replace("_", " ").casefold())) + " "


def _domain_terms(dom: Any) -> Dict[str, List[str]]:
    """Normalised metric and concept terms (names, labels, synonyms) of one domain."""
    metric_terms: List[str] = []
    concept_terms: List[str] = []
    for m in getattr(dom, "metrics", None) or []:
        for t in [m.name, getattr(m, "label", None), getattr(m, "logical_name", None), *(m.synonyms or [])]:
            nt = _norm_text(t or "").strip()
            if nt and nt not in metric_terms:
                metric_terms.append(nt)
    for c in getattr(dom, "concepts", None) or []:
        for t in [c.name, getattr(c, "label", None), *(c.synonyms or [])]:
            nt = _norm_text(t or "").strip()
            if nt and nt not in concept_terms:
                concept_terms.append(nt)
    return {"metric": metric_terms, "concept": concept_terms}


def route_scope_to_view(prompt: str, scope: ProjectScope) -> ProjectScope:
    """Point ``scope`` at the semantic view of the domain that owns the question's metric.

    Only acts on multi-view projects without an explicit view. The longest
    metric term (name / label / synonym) found in the question decides; when no
    metric matches, concept terms decide. A tie across views (or no match at
    all) does NOT guess: ``candidate_views`` and ``routing_message`` are set and
    ``semantic_view`` stays ``None``.
    """
    if scope.explicit_view or not scope.project_id:
        return scope
    distinct = _distinct_views(scope.domain_views.values())
    if len(distinct) < 2:
        return scope

    from semabridge.ontology.store import ontology_store

    domains = [d for d in ontology_store.domains_for(scope.project_id) if d.domain in scope.domain_views]
    all_domains = list(ontology_store.domains_for(scope.project_id))
    text = _norm_text(prompt)

    best: Dict[str, tuple] = {}  # view tail -> (rank, length, domain, term)
    matches: Dict[str, List[str]] = {}
    for dom in domains:
        view = scope.domain_views[dom.domain]
        tail = _view_tail(view)
        terms = _domain_terms(dom)
        for rank, kind in ((2, "metric"), (1, "concept")):
            for term in terms[kind]:
                if f" {term} " in text:
                    matches.setdefault(view, [])
                    if term not in matches[view]:
                        matches[view].append(term)
                    cand = (rank, len(term.split()), dom.domain, term)
                    if tail not in best or cand[:2] > best[tail][:2]:
                        best[tail] = cand
    scope.routing_matches = matches

    if best:
        top = max(v[:2] for v in best.values())
        winners = [t for t, v in best.items() if v[:2] == top]
        if len(winners) == 1:
            rank, _n, dom_name, term = best[winners[0]]
            scope.semantic_view = scope.domain_views[dom_name]
            scope.answered_by_domain = dom_name
            _restrict_to(scope, all_domains, [n for n, v in scope.domain_views.items() if _view_tail(v) == winners[0]])
            return scope
        scope.candidate_views = [v for v in distinct if _view_tail(v) in winners]
        found = ", ".join(sorted({best[t][3] for t in winners}))
        scope.routing_message = (
            f"'{found}' exists in {len(scope.candidate_views)} semantic views of this project: "
            + "; ".join(scope.candidate_views)
            + ". Choose the view (model) to ask, or name the metric more precisely."
        )
        return scope

    scope.candidate_views = distinct
    scope.routing_message = (
        "This project has one semantic view per model and the question did not name a metric or "
        "concept of any of them. Choose the view (model) to ask: " + "; ".join(distinct) + "."
    )
    return scope
