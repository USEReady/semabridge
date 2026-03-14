"""
Integration Test — Fabric 429 Throttling Behaviour.

Verifies that ``emit_to_fabric`` correctly:
    1. Detects HTTP 429 responses and raises ``FabricThrottledError``.
    2. Parses ``Retry-After`` values from error messages.
    3. Raises ``PermanentDeploymentError`` on 401/403/400.
    4. Returns success on clean deployment.

These tests mock the ``FabricPublisher`` so no real Fabric endpoint
is required.

Run:
    pytest tests/integration/test_fabric_429_handling.py -v
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch, MagicMock

from semabridge.orchestration.temporal.activities import (
    EmitInput,
    EmitOutput,
    FabricThrottledError,
    PermanentDeploymentError,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# Unit: Retry-After parser
# ---------------------------------------------------------------------------


class TestParseRetryAfter:
    def test_parses_standard_header(self):
        assert _parse_retry_after("HTTP 429: Retry-After: 45") == 45

    def test_parses_lowercase(self):
        assert _parse_retry_after("retry-after: 120") == 120

    def test_default_when_missing(self):
        assert _parse_retry_after("Some random error text") == 30

    def test_parses_from_mixed_text(self):
        msg = "Rate limited by Fabric API. Retry-After: 60 seconds. Contact admin."
        assert _parse_retry_after(msg) == 60


# ---------------------------------------------------------------------------
# Integration: emit_to_fabric activity
# ---------------------------------------------------------------------------

SAMPLE_SML = json.dumps({
    "datasets": [{"unique_name": "FACT_SALES"}],
})


def _make_emit_input(**kw) -> EmitInput:
    defaults = dict(
        tenant_id="t1",
        model_id="sales",
        sml_json=SAMPLE_SML,
        diff_json="{}",
        fabric_workspace_id="ws-123",
    )
    defaults.update(kw)
    return EmitInput(**defaults)


async def _call_emit(inp: EmitInput) -> EmitOutput:
    """Call the raw function behind the @activity.defn decorator."""
    # The underlying function is accessible as the original;
    # we also mock the activity module so heartbeat() is a no-op.
    import semabridge.orchestration.temporal.activities as act_mod
    # Call the raw coroutine
    return await act_mod.emit_to_fabric.__wrapped__(inp)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_emit_success():
    """Clean publish returns deployed=True."""
    mock_publisher_cls = MagicMock()
    mock_instance = mock_publisher_cls.return_value
    mock_instance.publish_model.return_value = {"id": "fabric-model-001"}

    mock_activity = MagicMock()
    mock_activity.heartbeat = MagicMock()

    import semabridge.orchestration.temporal.activities as act_mod

    with (
        patch.object(act_mod, "FabricPublisher", mock_publisher_cls),
        patch.object(act_mod, "FabricConfig", MagicMock()),
        patch.object(act_mod, "activity", mock_activity),
    ):
        raw_fn = getattr(act_mod.emit_to_fabric, "__wrapped__", act_mod.emit_to_fabric)
        result = await raw_fn(_make_emit_input())

    assert result.deployed is True
    assert result.fabric_model_id == "fabric-model-001"
    assert result.error == ""


@pytest.mark.asyncio
async def test_emit_429_raises_throttled():
    """A 429 from Fabric should raise FabricThrottledError."""
    from semabridge.connectors.fabric_publisher import PublishError

    mock_publisher_cls = MagicMock()
    mock_instance = mock_publisher_cls.return_value
    mock_instance.publish_model.side_effect = PublishError(
        "HTTP 429 Too Many Requests. Retry-After: 90"
    )

    mock_activity = MagicMock()
    mock_activity.heartbeat = MagicMock()

    import semabridge.orchestration.temporal.activities as act_mod

    with (
        patch.object(act_mod, "FabricPublisher", mock_publisher_cls),
        patch.object(act_mod, "PublishError", PublishError),
        patch.object(act_mod, "FabricConfig", MagicMock()),
        patch.object(act_mod, "activity", mock_activity),
    ):
        raw_fn = getattr(act_mod.emit_to_fabric, "__wrapped__", act_mod.emit_to_fabric)

        with pytest.raises(FabricThrottledError) as exc_info:
            await raw_fn(_make_emit_input())

    assert exc_info.value.retry_after == 90


@pytest.mark.asyncio
async def test_emit_401_raises_permanent():
    """Auth failures should be non-retryable."""
    from semabridge.connectors.fabric_publisher import PublishError

    mock_publisher_cls = MagicMock()
    mock_instance = mock_publisher_cls.return_value
    mock_instance.publish_model.side_effect = PublishError(
        "HTTP 401 Unauthorized: invalid credentials"
    )

    mock_activity = MagicMock()
    mock_activity.heartbeat = MagicMock()

    import semabridge.orchestration.temporal.activities as act_mod

    with (
        patch.object(act_mod, "FabricPublisher", mock_publisher_cls),
        patch.object(act_mod, "PublishError", PublishError),
        patch.object(act_mod, "FabricConfig", MagicMock()),
        patch.object(act_mod, "activity", mock_activity),
    ):
        raw_fn = getattr(act_mod.emit_to_fabric, "__wrapped__", act_mod.emit_to_fabric)

        with pytest.raises(PermanentDeploymentError):
            await raw_fn(_make_emit_input())
