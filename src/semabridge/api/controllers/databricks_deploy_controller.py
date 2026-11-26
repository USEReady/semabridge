"""Databricks deploy API (Phase 3.8b).

Endpoints (all under deploy authority: project owner, admin, or a designated
deployer of the project -- checked server-side on every call):

* POST /api/projects/{id}/databricks/deploy/preview   -- read-only; stores an intent
* POST /api/projects/{id}/databricks/deploy           -- executes a stored intent (at most once)
* POST /api/projects/{id}/databricks/deploy/reconcile -- settle unknown outcomes; clean confirmed staging
* POST /api/projects/{id}/databricks/deploy/reverify  -- read-only re-check of applied_unverified objects
* GET  /api/projects/{id}/databricks/deploy/runs/{run_id}

Two separate authorisation layers: the APP decides who may deploy (this module);
DATABRICKS decides what the connection's identity may create or read (enforced
by Databricks, and pre-checked by ``prepare``). The deployer list and the
staging settings live in the project's target settings, which only owners and
admins can change (project access).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from semabridge.api.services.project_ownership_service import (
    _is_admin_user,
    auth_is_enabled,
    is_project_owned_by_user,
    require_request_user_id,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# --- authority ------------------------------------------------------------------------------------

def deployers_of(target: Dict[str, Any]) -> List[str]:
    raw = target.get("deployers") or []
    if isinstance(raw, str):
        raw = [x for x in raw.split(",")]
    return [str(x).strip() for x in raw if str(x).strip()]


def assert_deploy_authority(project_id: str, user_id: Optional[str], target: Dict[str, Any]) -> None:
    """Project owner, admin, or a designated deployer. No-op with auth disabled
    (single-user development mode, as everywhere else)."""
    if not auth_is_enabled():
        return
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if _is_admin_user(user_id) or is_project_owned_by_user(project_id, user_id, log_denied=False,
                                                           log_prefix="DatabricksDeploy"):
        return
    if str(user_id) in deployers_of(target):
        return
    raise HTTPException(status_code=403, detail="Deploying requires the project owner, an admin or a designated deployer")


# --- project context ------------------------------------------------------------------------------

def _project(project_id: str, request: Request) -> Dict[str, Any]:
    from semabridge.api.controllers.mappings_controller import (
        _effective_target, _is_preview_project_id, _require_databricks, _stored_config,
    )

    if _is_preview_project_id(project_id):
        raise HTTPException(status_code=400, detail="deploy a saved Databricks project (not the wizard preview)")
    user_id = require_request_user_id(request)
    cfg = _stored_config(project_id)
    _require_databricks(project_id, cfg)
    target = dict(_effective_target(cfg))
    assert_deploy_authority(project_id, user_id, target)
    if not str(target.get("catalog") or "").strip() or not str(target.get("schema") or target.get("schema_name") or "").strip():
        raise HTTPException(status_code=400, detail={
            "code": "project_settings_incomplete",
            "message": "The project's Databricks target has no catalog or schema. Set them in the project settings "
                       "(deploy never falls back to global defaults)."})
    return {"user_id": user_id, "target": target}


def _global_staging_default() -> str:
    """DATABRICKS_STAGING_SCHEMA (global default; the project's own value wins).
    The deploy pre-check still requires it to exist and be restricted."""
    try:
        from semabridge.core.settings import get_settings

        return str(getattr(get_settings().databricks, "staging_schema", None) or "").strip()
    except Exception:
        return ""


def _staging(target: Dict[str, Any]) -> Optional[tuple]:
    raw = str(target.get("staging_schema") or "").strip() or _global_staging_default()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(".") if p.strip()]
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) == 1 and target.get("catalog"):
        return str(target["catalog"]), parts[0]
    raise HTTPException(status_code=400, detail="staging_schema must be 'catalog.schema'")


def _connection(target: Dict[str, Any]):
    from semabridge.core.engine.targets.databricks import _databricks_connection_for_run
    from semabridge.core.settings import get_settings

    try:
        global_cfg = get_settings().databricks
    except Exception:
        global_cfg = None
    ctx = SimpleNamespace(config=SimpleNamespace(target=SimpleNamespace(**target), _databricks=None,
                                                 databricks=global_cfg))
    info = _databricks_connection_for_run(ctx)
    if info is None:
        raise HTTPException(status_code=400, detail="no Databricks connection is configured for this project")
    return info


def _clients(info, *, run_id: Optional[str] = None):
    from semabridge.target_backends.databricks.client import DatabricksSqlClient
    from semabridge.target_backends.databricks.deploy.live import DatabricksLive
    from semabridge.target_backends.databricks.guard import WRITE, DatabricksSqlGuard
    from semabridge.target_backends.databricks.warehouses import resolve_warehouse

    tags = {"step": "deploy", **({"deploy_run": run_id} if run_id else {})}
    reader = DatabricksSqlClient(info, deadline_seconds=180, query_tags=tags)
    if not reader.warehouse_id:
        choice = resolve_warehouse(reader)
        if choice["warehouse"] is None:
            raise HTTPException(status_code=400, detail=str(choice["message"]))
        reader.warehouse_id = choice["warehouse"].id

    def write_client(write_plan):
        return DatabricksSqlClient(info, guard=DatabricksSqlGuard(WRITE, write_plan), warehouse_id=reader.warehouse_id,
                                   deadline_seconds=300, query_tags=tags)

    return DatabricksLive(reader), write_client, reader.warehouse_id


def _allow_list() -> list:
    from semabridge.core.engine.targets.databricks import capability_allow_list

    try:
        from semabridge.core.settings import get_settings

        db = getattr(get_settings().behavior, "databricks", None)
    except Exception:
        db = None
    return capability_allow_list(db)


def _plan(project_id: str, model_subdir: Optional[str], cache_project_id: Optional[str] = None):
    """The plan to deploy. Dry runs run as a preview project and store their plan
    under that preview id; such a plan is accepted only when it records THIS
    project as its owner (cache_ref.binding_project_id)."""
    from semabridge.api.controllers.mappings_controller import _is_preview_project_id
    from semabridge.target_backends.databricks import bundle, replan

    cache = str(cache_project_id or project_id).strip()
    if cache != project_id and (not _is_preview_project_id(cache) or cache == "preview"):
        raise HTTPException(status_code=400, detail="cache_project_id must be this project or its dry-run preview")
    out_dir = replan.output_dir(cache, model_subdir)
    plan, state = replan.recorded_plan(out_dir)
    if plan is None:
        raise HTTPException(status_code=404, detail="No Databricks plan for this project: run a dry run in this project.")
    if cache != project_id:
        owner = str(((plan.get("meta") or {}).get("cache_ref") or {}).get("binding_project_id") or "")
        if owner != project_id:
            raise HTTPException(status_code=403, detail={
                "code": "plan_not_for_this_project",
                "message": "that dry-run plan does not belong to this project: run the dry run from this project"})
    current = bundle.read_current(out_dir).get("plan_revision")
    return out_dir, plan, (int(current) if current is not None else None)


# --- endpoints ------------------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    create_stubs: bool = False
    publish_incomplete: bool = False
    model_subdir: Optional[str] = None
    cache_project_id: Optional[str] = None      # the dry run's preview id (its plan must name this project)


@router.post('/api/projects/{project_id}/databricks/deploy/preview')
async def preview_deploy(project_id: str, request: Request, body: PreviewRequest):
    from semabridge.core.engine.targets.databricks import stub_form_for_run
    from semabridge.target_backends.databricks.deploy.intent import create_intent
    from semabridge.target_backends.databricks.deploy.prepare import DeployRequest

    ctx = _project(project_id, request)
    target = ctx["target"]
    out_dir, plan, _ = _plan(project_id, body.model_subdir, body.cache_project_id)
    info = _connection(target)

    def work():
        from semabridge.target_backends.databricks.deploy import store

        store.sweep_stale_runs(project_id, str(body.model_subdir or ""))
        live, _, warehouse_id = _clients(info)
        req = DeployRequest(
            project_id=project_id, model_key=str(body.model_subdir or ""), out_dir=out_dir, plan=plan,
            workspace_host=info.host, warehouse_id=warehouse_id, allow_list=_allow_list(),
            create_stubs=body.create_stubs, publish_incomplete=body.publish_incomplete,
            stub_form=stub_form_for_run(SimpleNamespace(config=SimpleNamespace(target=SimpleNamespace(**target)))),
            staging=_staging(target), deploy_principals=[str(p) for p in target.get("deploy_principals") or []])
        return create_intent(req, live, created_by=ctx["user_id"])

    return await asyncio.to_thread(work)


class ConfirmRequest(BaseModel):
    intent_id: str
    acknowledge_destination_access: bool = False
    model_subdir: Optional[str] = None
    cache_project_id: Optional[str] = None


@router.post('/api/projects/{project_id}/databricks/deploy')
async def confirm_deploy(project_id: str, request: Request, body: ConfirmRequest):
    """Executes the stored intent in the background; returns its run id at once.
    A repeated confirmation returns the same run."""
    from semabridge.target_backends.databricks.deploy import store
    from semabridge.target_backends.databricks.deploy.execute import IntentError, confirm_intent

    ctx = _project(project_id, request)
    out_dir, plan, current = _plan(project_id, body.model_subdir, body.cache_project_id)
    intent = await asyncio.to_thread(store.get_intent, body.intent_id)
    if intent is None or intent["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="no such deployment intent for this project")
    if intent["status"] == "used":
        return {"run_id": intent["run_id"], "status": "existing"}
    target = ctx["target"]
    info = _connection(target)
    live, write_client, _ = await asyncio.to_thread(lambda: _clients(info, run_id=intent["run_id"]))
    await asyncio.to_thread(store.sweep_stale_runs, project_id, str(body.model_subdir or ""))
    from semabridge.core.engine.targets.databricks import stub_form_for_run

    overrides = {   # the CURRENT settings: a tightened one supersedes the preview
        "allow_list": _allow_list(), "staging": list(_staging(target)) if _staging(target) else None,
        "deploy_principals": [str(p) for p in target.get("deploy_principals") or []],
        "stub_form": stub_form_for_run(SimpleNamespace(config=SimpleNamespace(target=SimpleNamespace(**target)))),
    }
    state: Dict[str, Any] = {}
    decided = threading.Event()

    def work():
        try:
            confirm_intent(body.intent_id, project_id=project_id, model_key=str(body.model_subdir or ""),
                           user_id=ctx["user_id"], acknowledge_destination_access=body.acknowledge_destination_access,
                           plan=plan, current_revision=current, live=live, write_client=write_client,
                           store=_StartSignal(store, decided, state), overrides=overrides)
        except IntentError as exc:
            state["error"] = exc
        except Exception as exc:  # recorded on the run when it exists; logged here
            state["error"] = exc
            logger.exception("Databricks deploy for %s failed", project_id)
        finally:
            decided.set()            # after state is filled: the request thread never reads a half state

    threading.Thread(target=work, name=f"dbx-deploy-{intent['run_id'][:8]}", daemon=True).start()
    if not await asyncio.to_thread(decided.wait, 60):
        raise HTTPException(status_code=504, detail={"code": "start_unknown", "run_id": intent["run_id"],
                                                     "message": "the deploy did not confirm its start in time; "
                                                                "check the run status before trying again"})
    exc = state.get("error")
    if isinstance(exc, IntentError):
        raise HTTPException(status_code=409, detail={"code": exc.code, "message": str(exc)})
    outcome = state.get("outcome")
    if outcome in ("started", "existing"):
        return {"run_id": intent["run_id"], "status": "running" if outcome == "started" else "existing"}
    if exc is not None:
        raise HTTPException(status_code=500, detail="the deploy could not start; see the server log")
    return {"run_id": intent["run_id"], "status": "existing"}


class _StartSignal:
    """Store wrapper: signals the request thread once the intent is consumed (the
    run row exists), so the API can answer while the deploy continues."""

    def __init__(self, store: Any, event: threading.Event, state: Dict[str, Any]) -> None:
        self._store, self._event, self._state = store, event, state

    def consume_intent(self, *a, **k):
        res = self._store.consume_intent(*a, **k)
        if res.get("outcome") in ("started", "existing"):   # only then does a run exist
            self._state["outcome"] = res["outcome"]
            self._event.set()
        return res

    def __getattr__(self, name: str) -> Any:
        return getattr(self._store, name)


class ScopeRequest(BaseModel):
    model_subdir: Optional[str] = None
    cache_project_id: Optional[str] = None


@router.post('/api/projects/{project_id}/databricks/deploy/reconcile')
async def reconcile_deploy(project_id: str, request: Request, body: ScopeRequest):
    from semabridge.target_backends.databricks.deploy import store
    from semabridge.target_backends.databricks.deploy.execute import reconcile_and_clean

    ctx = _project(project_id, request)
    target = ctx["target"]
    info = _connection(target)

    def work():
        live, write_client, _ = _clients(info)
        ms = live.metastore()
        keys = []
        cat, sch = str(target.get("catalog") or ""), str(target.get("schema") or target.get("schema_name") or "")
        if cat and sch:
            keys.append(store.lease_key(ms, cat, sch))
        stg = _staging(target)
        if stg:
            keys.append(store.lease_key(ms, *stg))
        return reconcile_and_clean(keys, live, write_client)

    return await asyncio.to_thread(work)


@router.post('/api/projects/{project_id}/databricks/deploy/reverify')
async def reverify_deploy(project_id: str, request: Request, body: ScopeRequest):
    from semabridge.target_backends.databricks.deploy.execute import reverify

    ctx = _project(project_id, request)
    info = _connection(ctx["target"])

    def work():
        live, _, _ = _clients(info)
        return reverify(project_id, str(body.model_subdir or ""), live)

    return await asyncio.to_thread(work)


@router.get('/api/projects/{project_id}/databricks/deploy/runs/{run_id}')
async def deploy_run_status(project_id: str, run_id: str, request: Request):
    from semabridge.target_backends.databricks.deploy import store

    _project(project_id, request)
    run = await asyncio.to_thread(store.run_info, run_id)
    if run is None or run["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="no such deploy run for this project")
    run["journal"] = await asyncio.to_thread(store.journal, run_id)
    return run
