"""Backward-compatible proxy for bi_truth_comparison."""

from semabridge.ai.bi_truth_comparison import (
    compare_truth_results,
    get_default_absolute_floor as get_bi_truth_absolute_floor,
    get_default_tolerance as get_bi_truth_tolerance,
)

__all__ = [
    "compare_truth_results",
    "get_bi_truth_absolute_floor",
    "get_bi_truth_tolerance",
]
