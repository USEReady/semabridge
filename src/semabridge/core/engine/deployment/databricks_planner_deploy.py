"""Step 9 on the Databricks planner path: the sync is the approval.

The planner path deploys through the guarded deployer
(target_backends/databricks/deploy): a preview ("intent") of exactly what will
run, then its execution -- staging copy in the project's restricted staging
schema, validation, ownership re-check, CREATE/ALTER, read-back, journal.

From the plan panel a person previews and confirms. A sync does both: whoever
may start the sync (project owner, admin, shared access; or the schedule they
set up) has approved the deploy by starting it. Nothing else is relaxed:
a missing staging schema, a blocker or a failed check stops the deploy with
the planner's own reason.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from semabridge.core.engine.exceptions import DeploymentError

logger = logging.getLogger(__name__)

_STAGING_HELP = (
    "Deploying on the Databricks planner path needs a restricted staging schema for the test copy "
    "of each view. Ask a Databricks admin to create one (e.g. workspace.semabridge_staging) that only "
    "deployers and admins can access, then set it as the project's Staging Schema (project settings) "
    "or as DATABRICKS_STAGING_SCHEMA for all projects. SemaBridge never creates schemas."
)


def _http_detail(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail or exc)


def _blocker_message(preview: Dict[str, Any]) -> str:
    blockers: List[Dict[str, Any]] = list(preview.get("model_blockers") or [])
    if any(b.get("code") == "staging_schema_missing" for b in blockers):
        return _STAGING_HELP
    parts = [str(b.get("message") or b.get("code")) for b in blockers]
    for item in preview.get("views") or []:
        for r in item.get("reasons") or []:
            if item.get("action") == "blocked":
                parts.append(f"{item.get('name')}: {r.get('message') or r.get('code')}")
    return "; ".join(parts[:8]) or f"the deploy preview is {preview.get('status')}"


def deploy_planned_views(context: Any) -> Dict[str, Any]:
    """Preview and execute the planner's deploy for this run's plan. Returns the
    deploy summary; raises DeploymentError when nothing could be deployed."""
    from fastapi import HTTPException

    from semabridge.api.controllers import databricks_deploy_controller as ctl
    from semabridge.api.controllers.mappings_controller import _effective_target, _stored_config
    from semabridge.core.engine.targets.databricks import stub_form_for_run
    from semabridge.target_backends.databricks.deploy import store
    from semabridge.target_backends.databricks.deploy.execute import IntentError, confirm_intent
    from semabridge.target_backends.databricks.deploy.intent import create_intent
    from semabridge.target_backends.databricks.deploy.prepare import DeployRequest

    project_id = str(getattr(context, "project_id", "") or "")
    model_key = str(getattr(context, "physical_namespace", None) or "")
    approved_by = f"sync:{getattr(context, 'run_id', '') or 'run'}"
    if getattr(context, "databricks_view_plan", None) is None:
        raise DeploymentError("No Databricks plan was made for this run, so there is nothing to deploy.")
    try:
        target = dict(_effective_target(_stored_config(project_id)))
        if not str(target.get("catalog") or "").strip() or not str(
            target.get("schema") or target.get("schema_name") or ""
        ).strip():
            raise DeploymentError("The project's Databricks target has no catalog or schema: set them in the project settings.")
        out_dir, plan, current = ctl._plan(project_id, model_key)
        from semabridge.core.engine.targets.databricks import _databricks_connection_for_run

        try:
            info = _databricks_connection_for_run(context)
        except Exception:  # noqa: BLE001 -- a context without engine config
            info = None
        info = info or ctl._connection({**target, "_project_id": project_id})
        live, write_client, warehouse_id = ctl._clients(info)
        staging = ctl._staging(target)
    except HTTPException as exc:
        raise DeploymentError(f"Databricks deploy could not start: {_http_detail(exc)}") from None
    if staging is None:
        raise DeploymentError(_STAGING_HELP)

    stub_form = stub_form_for_run(SimpleNamespace(config=SimpleNamespace(target=SimpleNamespace(**target))))
    principals = [str(p) for p in target.get("deploy_principals") or []]
    on_missing = str(target.get("on_missing_source") or "stub").strip().lower()
    store.sweep_stale_runs(project_id, model_key)
    req = DeployRequest(
        project_id=project_id, model_key=model_key, out_dir=out_dir, plan=plan,
        workspace_host=info.host, warehouse_id=warehouse_id, allow_list=ctl._allow_list(),
        # The project's missing-table choice: "stub" creates the empty
        # placeholder tables and publishes those views (marked incomplete).
        create_stubs=on_missing == "stub", publish_incomplete=on_missing == "stub",
        stub_form=stub_form, staging=staging, deploy_principals=principals,
    )
    made = create_intent(req, live, created_by=approved_by)
    preview = made.get("preview") or {}
    if made.get("intent") is None:
        if preview.get("status") == "no_op":
            return {"status": "no_op", "results": [], "preview": preview}
        raise DeploymentError(f"Databricks deploy blocked: {_blocker_message(preview)}")

    overrides = {"allow_list": ctl._allow_list(), "staging": list(staging),
                 "deploy_principals": principals, "stub_form": stub_form}
    try:
        summary = confirm_intent(
            made["intent"]["intent_id"], project_id=project_id, model_key=model_key, user_id=approved_by,
            # Starting the sync is the approval, including who can read the destination.
            acknowledge_destination_access=True,
            plan=plan, current_revision=current, live=live, write_client=write_client, overrides=overrides,
        )
    except IntentError as exc:
        raise DeploymentError(f"Databricks deploy did not start: {exc}") from None
    return summary


def apply_deploy_result(context: Any, summary: Dict[str, Any]) -> None:
    """Report the deploy on the run: warnings for partial results, an error when
    nothing was deployed; the main deployed view for the snapshot marker."""
    status = str(summary.get("status") or "")
    results = [r for r in summary.get("results") or [] if r.get("kind") == "view"]
    ok = [r for r in results if r.get("outcome") in ("created", "updated", "unchanged", "verified")]
    bad = [r for r in results if r.get("outcome") in ("failed", "applied_unverified", "skipped")]
    plan = getattr(context, "databricks_view_plan", None) or {}
    views = plan.get("views") or []
    if views:
        main = max(views, key=lambda v: len(v.get("measures") or []))
        context.deployed_view_name = main.get("name")
        settings = plan.get("settings") or {}
        cat, sch = settings.get("output_catalog"), settings.get("output_schema")
        context.target_artifact_path = (
            f"databricks://{cat}/{sch}/{main.get('name')}" if cat and sch else f"databricks://{main.get('name')}"
        )
    warnings = getattr(context, "target_warnings", None)
    if not isinstance(warnings, list):
        warnings = []
        try:
            context.target_warnings = warnings
        except Exception:  # noqa: BLE001
            pass
    for r in bad:
        warnings.append(f"Databricks view {r.get('fqn') or r.get('name')}: {r.get('outcome')} {r.get('detail') or ''}".strip())
    if status in ("failed", "uncertain", "superseded") or (results and not ok):
        reason = summary.get("reason") or summary.get("error") or summary.get("stopped") or status
        raise DeploymentError(
            f"Databricks deploy {status}: {reason}. "
            "Open the project's Databricks plan panel to Reconcile, then sync again."
        )
    if status == "partial":
        warnings.append(f"Databricks deploy partial: {len(ok)} of {len(results)} view(s) deployed.")
    logger.info("Databricks planner deploy %s: %d view(s) ok, %d not", status, len(ok), len(bad))
