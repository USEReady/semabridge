"""
Materialization Builder — generates materialization queries for measures.

Builds SUMMARIZECOLUMNS DAX queries based on measure triage results.
"""

from __future__ import annotations

from typing import Any, List, Optional
from semabridge.converter.measure_triage import MaterializationStrategy, TriageResult


class MaterializationQueryBuilder:
    """Builds materialization queries for measures."""

    def __init__(self):
        pass

    def build_query(self, triage_result: TriageResult, model: Any) -> str:
        """Build a materialization query based on triage result."""
        if triage_result.strategy == MaterializationStrategy.PASSTHROUGH:
            return self._build_passthrough_query(triage_result)
        elif triage_result.strategy == MaterializationStrategy.ALIGNED_HISTORY:
            return self._build_aligned_history_query(triage_result)
        else:
            return self._build_decomposition_query(triage_result)

    def _build_passthrough_query(self, result: TriageResult) -> str:
        """Build passthrough query."""
        return f"SELECT {result.measure_name} FROM [Table]"

    def _build_aligned_history_query(self, result: TriageResult) -> str:
        """Build aligned history query."""
        return f"SELECT {result.measure_name} FROM [Table WITH(TEMPORAL)]"

    def _build_decomposition_query(self, result: TriageResult) -> str:
        """Build decomposition query."""
        return f"SELECT {result.measure_name} FROM [Table DECOMPOSED]"
