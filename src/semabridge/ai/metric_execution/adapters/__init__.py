"""Metric execution source adapters package."""
from semabridge.ai.metric_execution.adapters.base import SourceAdapter, SourceResult
from semabridge.ai.metric_execution.adapters.fabric_adapter import FabricSourceAdapter
from semabridge.ai.metric_execution.adapters.powerbi_adapter import PowerBISourceAdapter
from semabridge.ai.metric_execution.adapters.mock_source_adapter import MockSourceAdapter
from semabridge.ai.metric_execution.adapters.registry import get_source_adapter

__all__ = [
    "SourceAdapter",
    "SourceResult",
    "FabricSourceAdapter",
    "PowerBISourceAdapter",
    "MockSourceAdapter",
    "get_source_adapter",
]
