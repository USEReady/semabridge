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
) -> SyncResult:
    """
    Dispatch a single model sync to the configured orchestrator.

    Args:
        mode: Override ``SEMABRIDGE_ORCHESTRATOR`` for this call.
              ``"local"`` | ``"temporal"``.
    """
    effective_mode = (mode or ORCHESTRATOR_MODE).lower()

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
        )
    else:
        return await _run_local(
            tenant_id=tenant_id,
            model_id=model_id,
            database=database,
            schema=schema,
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
) -> SyncResult:
    """
    Run the sync using the existing in-process ``SyncOrchestrator``.

    This shim wraps the synchronous orchestrator in an asyncio executor
    so the adapter interface stays consistently async.
    """
    import asyncio
    from semabridge.core.sync_orchestrator import (
        SyncReport,
        ModelStatus,
    )

    def _sync() -> SyncResult:
        # The legacy orchestrator is invoked through the existing CLI plumbing.
        # We import lazily to keep the adapter lightweight when Temporal is used.
        try:
            from semabridge.core.engine import SemaBridgeEngine
            from semabridge.core.project import ProjectConfig, SourceConfig, TargetType, TargetConfig

            config = ProjectConfig(
                name=f"Sync {model_id}",
                source=SourceConfig(type="fabric", model=model_id),
                targets=[TargetConfig(type=TargetType.SNOWFLAKE, database=database, schema_name=schema)]
            )

            # v4.3: Legacy path uses default COPY mode
            engine = SemaBridgeEngine(config, sync_mode="copy")
            result = engine.execute()

            deployed = result.targets_succeeded > 0
            status = "completed" if result.success else "failed"
            error = "; ".join(result.errors) if result.errors else ""
        except Exception as exc:
            deployed = False
            status = "failed"
            error = str(exc)
            result = None

        return SyncResult(
            run_id=str(uuid.uuid4())[:12],
            tenant_id=tenant_id,
            model_id=model_id,
            status=status,
            changes_detected=-1,  # legacy doesn't expose change count
            deployed=deployed,
            error=error,
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _sync)
