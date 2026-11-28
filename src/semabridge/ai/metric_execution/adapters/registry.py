"""Adapter registry for source semantic model platforms."""
from __future__ import annotations

from typing import Any, Dict

from semabridge.ai.metric_execution.adapters.base import SourceAdapter
from semabridge.ai.metric_execution.adapters.fabric_adapter import FabricSourceAdapter
from semabridge.ai.metric_execution.adapters.powerbi_adapter import PowerBISourceAdapter
from semabridge.ai.metric_execution.adapters.mock_source_adapter import MockSourceAdapter


def get_source_adapter(
    platform: str,
    config: Dict[str, Any],
    mock_mode: bool = False,
) -> SourceAdapter:
    """Resolve and return appropriate SourceAdapter instance.
    
    Args:
        platform: "fabric", "power_bi" (or "pbi", "powerbi"), or "mock".
        config: Connection and project configuration dictionary.
        mock_mode: If True, forces MockSourceAdapter backed by persisted fixtures.
    """
    if mock_mode:
        return MockSourceAdapter(config)

    p = str(platform or "").strip().lower()
    if p in ("fabric", "microsoft_fabric", "ms_fabric"):
        return FabricSourceAdapter(config)
    elif p in ("power_bi", "powerbi", "pbi", "pbi_desktop"):
        return PowerBISourceAdapter(config)
    elif p in ("mock", "fixture"):
        return MockSourceAdapter(config)

    raise ValueError(f"Unsupported source platform: '{platform}'. Must be 'fabric', 'power_bi', or 'mock'.")
