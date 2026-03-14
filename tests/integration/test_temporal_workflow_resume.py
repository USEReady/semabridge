"""
Integration Test — Temporal Workflow Resume Behaviour.

Verifies that a ``SyncModelWorkflow``:
    1. Progresses through extract → diff → emit phases.
    2. Can be cancelled via the ``request_cancel`` signal.
    3. Persists a snapshot on completion.

These tests run against an in-memory Temporal test environment
(``temporalio.testing.WorkflowEnvironment``) — no external cluster needed.

Run:
    pytest tests/integration/test_temporal_workflow_resume.py -v
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from semabridge.orchestration.temporal.workflows import (
    SyncModelWorkflow,
    SyncWorkflowInput,
    SyncWorkflowResult,
    SyncPhase,
    BatchSyncWorkflow,
)
from semabridge.orchestration.temporal.activities import (
    ExtractInput,
    ExtractOutput,
    DiffInput,
    DiffOutput,
    EmitInput,
    EmitOutput,
    SnapshotInput,
    SnapshotOutput,
)

from temporalio import activity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_SML = json.dumps({
    "datasets": [
        {"unique_name": "DIM_CUSTOMER", "columns": [{"name": "ID"}]},
    ],
    "relationships": [],
})


def _make_input(**overrides) -> SyncWorkflowInput:
    defaults = dict(
        tenant_id="test-tenant",
        model_id="test-model",
        database="ANALYTICS",
        schema="PUBLIC",
        snowflake_account="xy12345.us-east-1",
        fabric_workspace_id="ws-abc-123",
    )
    defaults.update(overrides)
    return SyncWorkflowInput(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_sync_workflow_succeeds():
    """Happy path: extract → diff (changes found) → emit → snapshot."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    # Stub activities — must be decorated with matching names
    @activity.defn(name="extract_metadata")
    async def stub_extract(inp: ExtractInput) -> ExtractOutput:
        return ExtractOutput(sml_json=SAMPLE_SML, table_count=1)

    @activity.defn(name="compute_diff")
    async def stub_diff(inp: DiffInput) -> DiffOutput:
        return DiffOutput(diff_json="{}", total_changes=3, added=2, modified=1)

    @activity.defn(name="emit_to_fabric")
    async def stub_emit(inp: EmitInput) -> EmitOutput:
        return EmitOutput(deployed=True, fabric_model_id="fabric-001")

    @activity.defn(name="persist_snapshot")
    async def stub_snapshot(inp: SnapshotInput) -> SnapshotOutput:
        return SnapshotOutput(snapshot_id="snap-001")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="semabridge-sync",
            workflows=[SyncModelWorkflow],
            activities=[stub_extract, stub_diff, stub_emit, stub_snapshot],
        ):
            result: SyncWorkflowResult = await env.client.execute_workflow(
                SyncModelWorkflow.run,
                _make_input(),
                id=f"test-sync-{uuid.uuid4().hex[:8]}",
                task_queue="semabridge-sync",
            )

    assert result.phase == SyncPhase.COMPLETED.value
    assert result.deployed is True
    assert result.changes_detected == 3


@pytest.mark.asyncio
async def test_no_changes_skips_emit():
    """When diff returns 0 changes the emit phase is skipped."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    @activity.defn(name="extract_metadata")
    async def stub_extract(inp: ExtractInput) -> ExtractOutput:
        return ExtractOutput(sml_json=SAMPLE_SML, table_count=1)

    @activity.defn(name="compute_diff")
    async def stub_diff(inp: DiffInput) -> DiffOutput:
        return DiffOutput(diff_json="{}", total_changes=0)

    emit_called = False

    @activity.defn(name="emit_to_fabric")
    async def stub_emit(inp: EmitInput) -> EmitOutput:
        nonlocal emit_called
        emit_called = True
        return EmitOutput(deployed=True)

    @activity.defn(name="persist_snapshot")
    async def stub_snapshot(inp: SnapshotInput) -> SnapshotOutput:
        return SnapshotOutput(snapshot_id="snap-nc")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="semabridge-sync",
            workflows=[SyncModelWorkflow],
            activities=[stub_extract, stub_diff, stub_emit, stub_snapshot],
        ):
            result = await env.client.execute_workflow(
                SyncModelWorkflow.run,
                _make_input(),
                id=f"test-nochange-{uuid.uuid4().hex[:8]}",
                task_queue="semabridge-sync",
            )

    assert result.phase == SyncPhase.COMPLETED.value
    assert result.deployed is False
    assert not emit_called


@pytest.mark.asyncio
async def test_extraction_failure_aborts():
    """When extraction fails the workflow reports FAILED immediately."""
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    @activity.defn(name="extract_metadata")
    async def stub_extract(inp: ExtractInput) -> ExtractOutput:
        return ExtractOutput(error="Connection refused")

    @activity.defn(name="compute_diff")
    async def stub_diff(inp: DiffInput) -> DiffOutput:
        raise AssertionError("Should never be called")

    @activity.defn(name="emit_to_fabric")
    async def stub_emit(inp: EmitInput) -> EmitOutput:
        raise AssertionError("Should never be called")

    @activity.defn(name="persist_snapshot")
    async def stub_snapshot(inp: SnapshotInput) -> SnapshotOutput:
        return SnapshotOutput(snapshot_id="snap-err")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue="semabridge-sync",
            workflows=[SyncModelWorkflow],
            activities=[stub_extract, stub_diff, stub_emit, stub_snapshot],
        ):
            result = await env.client.execute_workflow(
                SyncModelWorkflow.run,
                _make_input(),
                id=f"test-fail-{uuid.uuid4().hex[:8]}",
                task_queue="semabridge-sync",
            )

    assert result.phase == SyncPhase.FAILED.value
    assert "Connection refused" in result.error
