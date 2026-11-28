"""Metric Execution and Source-to-Target Comparison Module."""
from semabridge.ai.metric_execution.service import MetricComparisonService
from semabridge.ai.metric_execution.result_normalizer import normalize_metric_value
from semabridge.ai.metric_execution.adapters.base import SourceAdapter, SourceResult
from semabridge.ai.metric_execution.adapters.registry import get_source_adapter

__all__ = [
    "MetricComparisonService",
    "normalize_metric_value",
    "SourceAdapter",
    "SourceResult",
    "get_source_adapter",
]
