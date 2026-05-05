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

    def build_query(self, *args, **kwargs) -> str:
        """Build a materialization query.

        Supported call forms:
        1) Legacy: build_query(triage_result, model)
        2) Batch:  build_query(metrics=[...], triage_results={...}, grain_dimensions=[...])
        """
        if "metrics" in kwargs and "triage_results" in kwargs:
            return self._build_batch_query(
                metrics=kwargs.get("metrics") or [],
                triage_results=kwargs.get("triage_results") or {},
                grain_dimensions=kwargs.get("grain_dimensions") or [],
            )

        if len(args) >= 1 and isinstance(args[0], TriageResult):
            triage_result: TriageResult = args[0]
            if triage_result.strategy == MaterializationStrategy.PASSTHROUGH:
                return self._build_passthrough_query(triage_result)
            if triage_result.strategy == MaterializationStrategy.ALIGNED_HISTORY:
                return self._build_aligned_history_query(triage_result)
            return self._build_decomposition_query(triage_result)

        raise ValueError("Unsupported build_query signature")

    def _build_batch_query(
        self,
        metrics: list[Any],
        triage_results: dict[str, TriageResult],
        grain_dimensions: list[str],
    ) -> str:
        """Build a SUMMARIZECOLUMNS batch DAX query for multiple measures."""
        dim_parts = [d for d in grain_dimensions if d]

        measure_parts: list[str] = []
        for metric in metrics:
            metric_name = str(getattr(metric, "unique_name", "") or "").strip()
            if not metric_name:
                continue

            triage = triage_results.get(metric_name)
            alias = metric_name.replace('"', '""')

            # For now, materialize the base measure in all tiers.
            # Tier-specific reconstruction happens in Snowflake view generation.
            if triage and triage.strategy == MaterializationStrategy.DECOMPOSITION and triage.components:
                measure_parts.append(f'"{alias}", [{metric_name}]')
                num_expr = triage.components.get("_Num", "BLANK()")
                denom_expr = triage.components.get("_Denom", "BLANK()")
                measure_parts.append(f'"{alias}_Num", {num_expr}')
                measure_parts.append(f'"{alias}_Denom", {denom_expr}')

            else:
                measure_parts.append(f'"{alias}", [{metric_name}]')

        if not measure_parts:
            return ""

        all_parts = dim_parts + measure_parts
        parts_block = ",\n    ".join(all_parts)
        return f"EVALUATE\nSUMMARIZECOLUMNS(\n    {parts_block}\n)"

    def _build_passthrough_query(self, result: TriageResult) -> str:
        """Build passthrough query."""
        return f"SELECT {result.measure_name} FROM [Table]"

    def _build_aligned_history_query(self, result: TriageResult) -> str:
        """Build aligned history query."""
        return f"SELECT {result.measure_name} FROM [Table WITH(TEMPORAL)]"

    def _build_decomposition_query(self, result: TriageResult) -> str:
        """Build decomposition query."""
        return f"SELECT {result.measure_name} FROM [Table DECOMPOSED]"
