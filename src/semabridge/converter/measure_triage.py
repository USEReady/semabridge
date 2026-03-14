"""
Measure Triage — triage classification for DAX measures.

Classifies DAX expressions into materialization strategies:
- Tier 1 (Passthrough): Simple aggregations that can pass through unchanged
- Tier 2 (Aligned History): Requires historical alignment or slowly-changing dimensions
- Tier 3 (Decomposition): Requires semantic layer decomposition for correctness
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, List, Optional


class MaterializationStrategy(str, Enum):
    """Materialization strategies for measures."""

    PASSTHROUGH = "passthrough"      # Tier 1
    ALIGNED_HISTORY = "aligned_history"  # Tier 2  
    DECOMPOSITION = "decomposition"     # Tier 3


@dataclass
class TriageResult:
    """Result of triaging a measure."""

    measure_name: str
    expression: str
    strategy: MaterializationStrategy
    reason: str
    recommendations: List[str]


class MeasureTriage:
    """
    Classifies DAX measures into materialization strategies.
    """

    def __init__(self):
        pass

    def triage(self, measure_name: str, expression: str) -> TriageResult:
        """
        Triage a DAX measure expression.

        Args:
            measure_name: Name of the measure
            expression: DAX expression

        Returns:
            TriageResult with strategy and reasoning
        """
        strategy = self._determine_strategy(expression)

        reason = self._get_strategy_reason(strategy)
        recommendations = self._get_recommendations(strategy, expression)

        return TriageResult(
            measure_name=measure_name,
            expression=expression,
            strategy=strategy,
            reason=reason,
            recommendations=recommendations,
        )

    def _determine_strategy(self, expression: str) -> MaterializationStrategy:
        """Determine materialization strategy based on expression."""
        expr_upper = expression.upper()

        # Check for Tier 3 patterns (decomposition required)
        if any(pattern in expr_upper for pattern in [
            'CALCULATE', 'FILTER', 'CONTEXT', 'ALL(', 'DATEFILTER',
        ]):
            # Complex filters usually need decomposition
            paren_count = expression.count('(')
            if paren_count > 3:
                return MaterializationStrategy.DECOMPOSITION

        # Check for Tier 2 patterns (aligned history)
        if any(pattern in expr_upper for pattern in [
            'PREVIOUSDAY', 'PREVIOUSMONTH', 'YTD', 'MTD', 'DATEADD',
        ]):
            return MaterializationStrategy.ALIGNED_HISTORY

        # Default to Tier 1 (passthrough)
        return MaterializationStrategy.PASSTHROUGH

    def _get_strategy_reason(self, strategy: MaterializationStrategy) -> str:
        """Get human-readable reason for strategy."""
        reasons = {
            MaterializationStrategy.PASSTHROUGH: "Simple aggregation suitable for passthrough",
            MaterializationStrategy.ALIGNED_HISTORY: "Requires historical alignment",
            MaterializationStrategy.DECOMPOSITION: "Requires semantic decomposition",
        }
        return reasons.get(strategy, "Unknown strategy")

    def _get_recommendations(
        self,
        strategy: MaterializationStrategy,
        expression: str,
    ) -> List[str]:
        """Get recommendations for handling the strategy."""
        recommendations = []

        if strategy == MaterializationStrategy.PASSTHROUGH:
            recommendations.append("This measure can be materialized as-is")

        elif strategy == MaterializationStrategy.ALIGNED_HISTORY:
            recommendations.append("Ensure slowly-changing dimensions are synced")
            recommendations.append("Consider materialization with SCD type 2")

        elif strategy == MaterializationStrategy.DECOMPOSITION:
            recommendations.append("Break down expression into component measures")
            recommendations.append("Materialize components separately")
            recommendations.append("Define composition rules in semantic layer")

        return recommendations
