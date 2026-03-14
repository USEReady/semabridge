"""
Temporal Workflow Definitions for Semabridge.

Each workflow models the durable, resumable lifecycle of a single
semantic-model synchronisation pipeline:

    1. Extract  – pull metadata from Snowflake
    2. Diff     – compute incremental delta (via Ray actors)
    3. Emit     – deploy TMSL to Microsoft Fabric

Workflows can yield on external signals (e.g. Fabric 429 back-off)
and resume without holding worker memory.

Usage (programmatic start):
    from temporalio.client import Client
    client = await Client.connect("localhost:7233")
    handle = await client.start_workflow(
        SyncModelWorkflow.run,
        SyncWorkflowInput(tenant_id="acme", model_id="sales_model", ...),
        id="sync-acme-sales_model",
        task_queue="semabridge-sync",
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from semabridge.orchestration.temporal.activities import (
        ExtractInput,
        ExtractOutput,
        DiffInput,
        DiffOutput,
        EmitInput,
        EmitOutput,
        SnapshotInput,
        extract_metadata,
        compute_diff,
        emit_to_fabric,
        persist_snapshot,
    )


# ---------------------------------------------------------------------------
# Data classes shared between workflows and callers
# ---------------------------------------------------------------------------


class SyncPhase(str, Enum):
    """Tracks which phase the pipeline has reached."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    DIFFING = "diffing"
    EMITTING = "emitting"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SyncWorkflowInput:
    """Input payload for a single model sync workflow."""

    tenant_id: str
    model_id: str
    database: str
    schema: str
    snowflake_account: str
    fabric_workspace_id: str
    # Optional overrides
    warehouse_size: str = "SMALL"
    version_tag: str = ""
    force_full_sync: bool = False
    # Correlation
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])


@dataclass
class SyncWorkflowResult:
    """Returned when the workflow completes (success or permanent failure)."""

    run_id: str = ""
    tenant_id: str = ""
    model_id: str = ""
    phase: str = SyncPhase.PENDING.value
    changes_detected: int = 0
    deployed: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Retry / timeout policies
# ---------------------------------------------------------------------------

_EXTRACT_TIMEOUT = timedelta(minutes=10)
_DIFF_TIMEOUT = timedelta(minutes=5)
_EMIT_TIMEOUT = timedelta(minutes=15)

_DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=5),
    maximum_attempts=5,
)

_EMIT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
    maximum_attempts=8,
    non_retryable_error_types=["PermanentDeploymentError"],
)


# ---------------------------------------------------------------------------
# Main sync workflow
# ---------------------------------------------------------------------------


@workflow.defn(name="SyncModelWorkflow", sandboxed=False)
class SyncModelWorkflow:
    """
    Durable, resumable pipeline for a single semantic model.

    Phases:
        extract  → pull metadata from Snowflake (activity)
        diff     → compute incremental delta via Ray (activity)
        emit     → deploy TMSL to Fabric (activity, yields on 429)
        snapshot → persist diff result to repository (activity)
    """

    def __init__(self) -> None:
        self._phase = SyncPhase.PENDING
        self._cancel_requested = False

    # -- signals & queries ------------------------------------------------

    @workflow.signal
    async def request_cancel(self) -> None:
        """External signal to gracefully cancel this workflow."""
        self._cancel_requested = True

    @workflow.query
    def current_phase(self) -> str:
        return self._phase.value

    # -- main entrypoint --------------------------------------------------

    @workflow.run
    async def run(self, inp: SyncWorkflowInput) -> SyncWorkflowResult:
        result = SyncWorkflowResult(
            run_id=inp.run_id,
            tenant_id=inp.tenant_id,
            model_id=inp.model_id,
        )

        # ---- Phase 1: Extract -------------------------------------------
        self._phase = SyncPhase.EXTRACTING
        if self._cancel_requested:
            result.phase = SyncPhase.FAILED.value
            result.error = "Cancelled before extraction"
            return result

        extract_out: ExtractOutput = await workflow.execute_activity(
            extract_metadata,
            ExtractInput(
                tenant_id=inp.tenant_id,
                model_id=inp.model_id,
                database=inp.database,
                schema=inp.schema,
                snowflake_account=inp.snowflake_account,
                warehouse_size=inp.warehouse_size,
            ),
            start_to_close_timeout=_EXTRACT_TIMEOUT,
            retry_policy=_DEFAULT_RETRY,
        )

        if extract_out.error:
            result.phase = SyncPhase.FAILED.value
            result.error = f"Extraction failed: {extract_out.error}"
            return result

        # ---- Phase 2: Diff ----------------------------------------------
        self._phase = SyncPhase.DIFFING
        diff_out: DiffOutput = await workflow.execute_activity(
            compute_diff,
            DiffInput(
                tenant_id=inp.tenant_id,
                model_id=inp.model_id,
                current_sml_json=extract_out.sml_json,
                force_full=inp.force_full_sync,
            ),
            start_to_close_timeout=_DIFF_TIMEOUT,
            retry_policy=_DEFAULT_RETRY,
        )

        result.changes_detected = diff_out.total_changes

        if diff_out.total_changes == 0 and not inp.force_full_sync:
            # Nothing changed — skip emit, persist snapshot, return early
            result.phase = SyncPhase.COMPLETED.value
            result.deployed = False
            await workflow.execute_activity(
                persist_snapshot,
                SnapshotInput(
                    tenant_id=inp.tenant_id,
                    model_id=inp.model_id,
                    sml_json=extract_out.sml_json,
                    version_tag=inp.version_tag,
                    run_id=inp.run_id,
                    status="no_change",
                ),
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=_DEFAULT_RETRY,
            )
            return result

        # ---- Phase 3: Emit to Fabric ------------------------------------
        self._phase = SyncPhase.EMITTING
        if self._cancel_requested:
            result.phase = SyncPhase.FAILED.value
            result.error = "Cancelled before emission"
            return result

        emit_out: EmitOutput = await workflow.execute_activity(
            emit_to_fabric,
            EmitInput(
                tenant_id=inp.tenant_id,
                model_id=inp.model_id,
                sml_json=extract_out.sml_json,
                diff_json=diff_out.diff_json,
                fabric_workspace_id=inp.fabric_workspace_id,
                version_tag=inp.version_tag,
            ),
            start_to_close_timeout=_EMIT_TIMEOUT,
            retry_policy=_EMIT_RETRY,
        )

        if emit_out.error:
            result.phase = SyncPhase.FAILED.value
            result.error = f"Emission failed: {emit_out.error}"
        else:
            result.deployed = True
            result.phase = SyncPhase.COMPLETED.value

        # ---- Phase 4: Persist snapshot -----------------------------------
        await workflow.execute_activity(
            persist_snapshot,
            SnapshotInput(
                tenant_id=inp.tenant_id,
                model_id=inp.model_id,
                sml_json=extract_out.sml_json,
                version_tag=inp.version_tag,
                run_id=inp.run_id,
                status="success" if result.deployed else "failed",
            ),
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_DEFAULT_RETRY,
        )

        return result


# ---------------------------------------------------------------------------
# Batch orchestrator workflow (fan-out / fan-in)
# ---------------------------------------------------------------------------


@workflow.defn(name="BatchSyncWorkflow", sandboxed=False)
class BatchSyncWorkflow:
    """
    Fan-out workflow that launches N child ``SyncModelWorkflow`` instances
    and aggregates the results.

    Use this to synchronise an entire tenant's model portfolio in one
    durable operation.
    """

    @workflow.run
    async def run(self, models: List[SyncWorkflowInput]) -> List[SyncWorkflowResult]:
        """
        Groups models by target schema (database, schema) and executes them
        sequentially within each schema group to prevent DDL race conditions
        (e.g. concurrent DROP/CREATE TABLE).
        
        Parallelism is maintained across different target schemas.
        """
        import asyncio
        from collections import defaultdict

        # 1. Group models by target (database, schema)
        groups = defaultdict(list)
        for inp in models:
            key = (inp.database, inp.schema)
            groups[key].append(inp)

        # 2. Process each group
        async def _run_group_sequentially(group_models: List[SyncWorkflowInput]) -> List[SyncWorkflowResult]:
            group_results = []
            for inp in group_models:
                try:
                    # Sequential execution within the group
                    handle = await workflow.start_child_workflow(
                        SyncModelWorkflow.run,
                        inp,
                        id=f"sync-{inp.tenant_id}-{inp.model_id}-{inp.run_id}",
                        task_queue="semabridge-sync",
                        retry_policy=_DEFAULT_RETRY,
                    )
                    r = await handle
                    group_results.append(r)
                except Exception as exc:
                    group_results.append(
                        SyncWorkflowResult(
                            run_id=inp.run_id,
                            tenant_id=inp.tenant_id,
                            model_id=inp.model_id,
                            phase=SyncPhase.FAILED,
                            error=str(exc),
                        )
                    )
            return group_results

        # 3. Parallel execution across DIFFERENT targets
        tasks = [
            _run_group_sequentially(group_models)
            for group_models in groups.values()
        ]
        
        # Wait for all groups to complete
        batch_results_nested = await asyncio.gather(*tasks)
        
        # 4. Flatten and return
        final_results = []
        for sublist in batch_results_nested:
            final_results.extend(sublist)
            
        return final_results

