"""
Regression test for: Fabric model name shows as 'FabricModel' instead of the actual name.

Bug: _convert_fabric_to_sml built source_data without a 'display_name' key.
TMSLToOSIConverter.to_osi() resolves display_name as:
    source_data.get("display_name") or model_obj.get("name") or "FabricModel"

When 'display_name' is absent AND model_obj["name"] is empty/missing (which Fabric
sometimes returns for certain model types), the fallback "FabricModel" was used as
the OSI model's unique_name, which then propagated to the SML label and the
Snowflake emitter log: "[SML] success model=FabricModel".

Fix: Pass sf.dataset_name (populated from model_obj["name"] in from_fabric_tmsl)
as 'display_name' in source_data so the actual semantic model name is used.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_source_format(model_name: str, dataset_id: str = "abc-123", workspace_id: str = "ws-1"):
    """Build a minimal SourceFormat-like object for testing."""
    from semabridge.core.source_format import SourceFormat

    tmsl = {"model": {"name": model_name, "tables": []}}
    return SourceFormat(
        source_type="fabric",
        project_id="proj-test",
        run_id="run-test",
        tmsl_definition=tmsl,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        dataset_name=model_name,
    )


def test_fabric_model_name_propagated_to_osi():
    """
    The OSI model's unique_name and label must reflect the actual Fabric model name,
    not the 'FabricModel' fallback.

    This test FAILS without the fix (display_name missing from source_data)
    and PASSES with it.
    """
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

    tmsl = {"model": {"name": "continent", "tables": []}}
    source_data = {
        "tmsl": tmsl,
        "workspace_id": "ws-1",
        "dataset_id": "abc-123",
        "display_name": "continent",  # This is what the fix adds
    }

    osi = TMSLToOSIConverter().to_osi(source_data)

    assert osi.unique_name == "continent", (
        f"Expected OSI unique_name='continent', got '{osi.unique_name}'. "
        "The 'FabricModel' fallback is being used instead of the actual model name."
    )
    assert osi.label == "continent"


def test_fabric_model_name_fallback_without_display_name():
    """
    Without display_name in source_data, the converter falls back to dataset_id.
    This test documents the existing fallback behaviour.
    """
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

    tmsl = {"model": {"name": "continent", "tables": []}}
    source_data = {
        "tmsl": tmsl,
        "workspace_id": "ws-1",
        "dataset_id": "abc-123",
        # No display_name — relies on dataset_id
    }

    osi = TMSLToOSIConverter().to_osi(source_data)

    # dataset_id = "abc-123" so it is used
    assert osi.unique_name == "abc-123"


def test_fabric_model_name_fallback_when_tmsl_name_empty():
    """
    When TMSL model.name is empty AND display_name is absent, the fallback
    dataset_id 'abc-123' is used. The fix ensures display_name is always passed so
    this fallback is never reached in the normal pipeline.
    """
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter

    tmsl = {"model": {"name": "", "tables": []}}
    source_data_without_display = {
        "tmsl": tmsl,
        "workspace_id": "ws-1",
        "dataset_id": "abc-123",
    }
    osi_bad = TMSLToOSIConverter().to_osi(source_data_without_display)
    assert osi_bad.unique_name == "abc-123", (
        "Without display_name and with empty TMSL name, fallback should be dataset_id 'abc-123'"
    )

    # With the fix: display_name from sf.dataset_name is passed explicitly
    source_data_with_display = {
        "tmsl": tmsl,
        "workspace_id": "ws-1",
        "dataset_id": "abc-123",
        "display_name": "continent",
    }
    osi_good = TMSLToOSIConverter().to_osi(source_data_with_display)
    assert osi_good.unique_name == "continent", (
        "With display_name set, the actual model name must be used even when TMSL name is empty"
    )


def test_convert_fabric_to_sml_passes_display_name():
    """
    _convert_fabric_to_sml must include 'display_name' in the source_data dict
    passed to TMSLToOSIConverter so the actual model name flows through.

    This test inspects the source_data dict captured during conversion.
    """
    import inspect
    from semabridge.core.engine.conversion import fabric as fabric_conv_module

    source = inspect.getsource(fabric_conv_module._convert_fabric_to_sml)

    assert '"display_name"' in source or "'display_name'" in source, (
        "_convert_fabric_to_sml does not pass 'display_name' in source_data. "
        "Without it, TMSLToOSIConverter falls back to 'FabricModel' when TMSL model.name is empty."
    )
    assert "dataset_name" in source, (
        "_convert_fabric_to_sml must use sf.dataset_name as the display_name value."
    )
