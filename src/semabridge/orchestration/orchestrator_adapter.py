"""
Orchestrator Adapter — bridge between legacy local and distributed modes.

Provides a unified ``run_sync`` function that callers (CLI, GUI, tests) use
regardless of whether the pipeline runs in-process via the legacy
``SyncOrchestrator`` or is dispatched to Temporal.

The mode is controlled by the ``SEMABRIDGE_ORCHESTRATOR`` env var:
    • ``local``    → existing single-process SyncOrchestrator (default)
    • ``temporal`` → dispatch to a running Temporal cluster

Usage:
    from semabridge.orchestration.orchestrator_adapter import run_sync

    result = await run_sync(
        tenant_id="acme",
        model_id="sales",
        database="ANALYTICS",
        schema="PUBLIC",
        ...
    )
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Shared result type (thin wrapper around workflow / legacy output)
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Unified sync result returned by both local and Temporal modes."""

    run_id: str = ""
    tenant_id: str = ""
    model_id: str = ""
    status: str = "pending"  # pending | completed | failed
    changes_detected: int = 0
    deployed: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Mode selection
# ---------------------------------------------------------------------------

ORCHESTRATOR_MODE = os.getenv("SEMABRIDGE_ORCHESTRATOR", "local").lower()


async def run_sync(
    tenant_id: str,
    model_id: str,
    database: str,
    schema: str,
    snowflake_account: str = "",
    fabric_workspace_id: str = "",
    warehouse_size: str = "SMALL",
    version_tag: str = "",
    force_full_sync: bool = False,
    *,
    mode: Optional[str] = None,
    project_id: str = "",
    fabric_identity_id: str = "",
    deploy: bool = True,
) -> SyncResult:
    """
    Dispatch a single model sync to the configured orchestrator.

    Args:
        mode: Override ``SEMABRIDGE_ORCHESTRATOR`` for this call.
              ``"local"`` | ``"temporal"``.
    """
    effective_mode = (mode or ORCHESTRATOR_MODE).lower()
    # Both paths sync a Fabric model: the job's own identity/workspace (explicit,
    # else the project config) -- never the server-wide FABRIC_* defaults.
    from semabridge.sources.fabric.legacy_sync import resolve_sync_fabric_target

    fabric_identity_id, fabric_workspace_id = resolve_sync_fabric_target(
        identity_id=fabric_identity_id, workspace_id=fabric_workspace_id, project_id=project_id, caller="adapter",
    )

    if effective_mode == "temporal":
        return await _run_temporal(
            tenant_id=tenant_id,
            model_id=model_id,
            database=database,
            schema=schema,
            snowflake_account=snowflake_account,
            fabric_workspace_id=fabric_workspace_id,
            warehouse_size=warehouse_size,
            version_tag=version_tag,
            force_full_sync=force_full_sync,
            fabric_identity_id=fabric_identity_id,
        )
    else:
        return await _run_local(
            tenant_id=tenant_id,
            model_id=model_id,
            database=database,
            schema=schema,
            fabric_workspace_id=fabric_workspace_id,
            fabric_identity_id=fabric_identity_id,
            project_id=project_id,
            deploy=deploy,
        )


# ---------------------------------------------------------------------------
# Temporal dispatch
# ---------------------------------------------------------------------------


async def _run_temporal(
    tenant_id: str,
    model_id: str,
    database: str,
    schema: str,
    snowflake_account: str,
    fabric_workspace_id: str,
    warehouse_size: str,
    version_tag: str,
    force_full_sync: bool,
    fabric_identity_id: str = "",
) -> SyncResult:
    """Start a ``SyncModelWorkflow`` on the Temporal cluster and await its result."""
    from temporalio.client import Client

    from semabridge.orchestration.temporal.workflows import (
        SyncWorkflowInput,
        SyncWorkflowResult,
    )

    host = os.getenv("TEMPORAL_HOST", "localhost:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TASK_QUEUE", "semabridge-sync")

    client = await Client.connect(host, namespace=namespace)

    run_id = str(uuid.uuid4())[:12]
    inp = SyncWorkflowInput(
        tenant_id=tenant_id,
        model_id=model_id,
        database=database,
        schema=schema,
        snowflake_account=snowflake_account,
        fabric_workspace_id=fabric_workspace_id,
        warehouse_size=warehouse_size,
        version_tag=version_tag,
        force_full_sync=force_full_sync,
        fabric_identity_id=fabric_identity_id,
        run_id=run_id,
    )

    workflow_result = await client.execute_workflow(
        "SyncModelWorkflow",
        inp,
        id=f"sync-{tenant_id}-{model_id}-{run_id}",
        task_queue=task_queue,
    )

    # Handle both dataclass and dict response (Temporal sometimes returns dict)
    if isinstance(workflow_result, dict):
        return SyncResult(
            run_id=workflow_result.get("run_id", ""),
            tenant_id=workflow_result.get("tenant_id", ""),
            model_id=workflow_result.get("model_id", ""),
            status=str(workflow_result.get("phase", "unknown")),
            changes_detected=workflow_result.get("changes_detected", 0),
            deployed=workflow_result.get("deployed", False),
            error=workflow_result.get("error", ""),
        )
    else:
        return SyncResult(
            run_id=getattr(workflow_result, "run_id", ""),
            tenant_id=getattr(workflow_result, "tenant_id", ""),
            model_id=getattr(workflow_result, "model_id", ""),
            status=str(getattr(workflow_result, "phase", "unknown")),
            changes_detected=getattr(workflow_result, "changes_detected", 0),
            deployed=getattr(workflow_result, "deployed", False),
            error=getattr(workflow_result, "error", ""),
        )


# ---------------------------------------------------------------------------
# Local (legacy) dispatch
# ---------------------------------------------------------------------------


async def _run_local(
    tenant_id: str,
    model_id: str,
    database: str,
    schema: str,
    fabric_workspace_id: str = "",
    fabric_identity_id: str = "",
    project_id: str = "",
    deploy: bool = True,
) -> SyncResult:
    """
    Run the sync in-process with the current ``ExecutionEngine`` -- the same
    constructor and ``execute(...)`` call the main project sync uses
    (api/services/sync_execution_service.py).

    The run config is the project's saved config when ``project_id`` is given
    (so its behavior/options apply, like a project sync), with the job's Fabric
    identity/workspace/model and the Snowflake target layered on top.
    ``deploy=False`` runs the full pipeline without deploying (dry run).
    The engine call runs in an executor so the adapter stays async.
    """
    import asyncio
    import copy

    def _sync() -> SyncResult:
        from semabridge.core.engine import ExecutionEngine
        from semabridge.core.run_summary import RunStatus

        cfg: Dict[str, Any] = {}
        if project_id:
            from semabridge.sources.fabric.dax_target import load_project_config

            cfg = copy.deepcopy(load_project_config(project_id) or {})
        run_project_id = str(cfg.get("project_id") or project_id or f"adapter-{tenant_id}-{model_id}")
        cfg["project_id"] = run_project_id
        source = dict(cfg.get("source") or {}) if isinstance(cfg.get("source"), dict) else {}
        source.update({"type": "fabric", "workspace_id": fabric_workspace_id, "identity_id": fabric_identity_id,
                       "models": [model_id]})
        source.pop("dataset_id", None)
        cfg["source"] = source
        if database or schema:
            cfg["targets"] = [{"type": "snowflake", "database": database, "schema": schema}]

        error = ""
        try:
            summary = ExecutionEngine().execute(
                source="fabric",
                target="snowflake",
                project_id=run_project_id,
                project_name=model_id,
                config_dict=cfg,
                dataset_id=model_id,
                workspace_id=fabric_workspace_id,
                deploy=deploy,
                dry_run=not deploy,
                sync_mode="copy",
            )
            run_status = getattr(summary, "status", None)
            ok = run_status in (RunStatus.SUCCESS, RunStatus.PARTIAL)
            status = "completed" if ok else "failed"
            deployed = bool(deploy and run_status == RunStatus.SUCCESS)
            error = "; ".join(getattr(e, "message", str(e)) for e in (getattr(summary, "errors", None) or []))
        except Exception as exc:  # reported on the result with its reason
            status, deployed, error = "failed", False, f"{type(exc).__name__}: {exc}"

        return SyncResult(
            run_id=str(uuid.uuid4())[:12],
            tenant_id=tenant_id,
            model_id=model_id,
            status=status,
            changes_detected=-1,  # the engine doesn't expose a change count
            deployed=deployed,
            error=error,
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync)
