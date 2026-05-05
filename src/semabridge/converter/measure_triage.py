"""
Measure Triage — triage classification for DAX measures.

Classifies DAX expressions into materialization strategies:
- Tier 1 (Passthrough): Simple aggregations that can pass through unchanged
- Tier 2 (Aligned History): Requires historical alignment or slowly-changing dimensions
- Tier 3 (Decomposition): Requires semantic layer decomposition for correctness
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    tier: int
    strategy: MaterializationStrategy
    reason: str
    recommendations: List[str]
    aligned_measures: list[str] = field(default_factory=list)
    components: dict[str, str] = field(default_factory=dict)


class MeasureTriage:
    """
    Classifies DAX measures into materialization strategies.
    """

    def __init__(self):
        pass

    def classify(self, metric: Any) -> TriageResult:
        """Classify a metric object into a materialization strategy.

        Expected metric shape: object with ``unique_name`` and ``expression``.
        """
        measure_name = str(getattr(metric, "unique_name", "") or "")
        expression = str(getattr(metric, "expression", "") or "")
        return self.triage(measure_name, expression)

    def classify_all(self, metrics: list[Any]) -> dict[str, TriageResult]:
        """Batch classify metrics keyed by metric unique_name."""
        results: dict[str, TriageResult] = {}
        for metric in metrics:
            result = self.classify(metric)
            results[result.measure_name] = result
        return results

    def triage(self, measure_name: str, expression: str) -> TriageResult:
        """
        Triage a DAX measure expression.

        Args:
            measure_name: Name of the measure
            expression: DAX expression

        Returns:
            TriageResult with strategy and reasoning
        """
        strategy, tier = self._determine_strategy(expression)
        aligned_measures = self._extract_aligned_measures(expression)
        components = self._extract_components(expression)

        reason = self._get_strategy_reason(strategy)
        recommendations = self._get_recommendations(strategy, expression)

        return TriageResult(
            measure_name=measure_name,
            expression=expression,
            tier=tier,
            strategy=strategy,
            reason=reason,
            recommendations=recommendations,
            aligned_measures=aligned_measures,
            components=components,
        )

    def _determine_strategy(self, expression: str) -> tuple[MaterializationStrategy, int]:
        """Determine materialization strategy based on expression."""
        expr_upper = expression.upper()

        if not expression.strip():
            return MaterializationStrategy.PASSTHROUGH, 1

        if "DIVIDE(" in expr_upper or "/" in expr_upper or "DISTINCTCOUNT(" in expr_upper:
            return MaterializationStrategy.DECOMPOSITION, 3

        if any(pattern in expr_upper for pattern in [
            'SAMEPERIODLASTYEAR', 'TOTALYTD', 'TOTALMTD', 'PREVIOUSMONTH',
            'PREVIOUSDAY', 'PREVIOUSYEAR', 'DATEADD', 'YTD', 'MTD',
        ]):
            return MaterializationStrategy.ALIGNED_HISTORY, 2

        if any(pattern in expr_upper for pattern in ['IF(', 'SWITCH(', 'VAR ', 'RETURN', 'SUMX(', 'AVERAGEX(']):
            return MaterializationStrategy.DECOMPOSITION, 3

        # Check for Tier 3 patterns (decomposition required)
        if any(pattern in expr_upper for pattern in [
            'CALCULATE', 'FILTER', 'CONTEXT', 'ALL(', 'DATEFILTER',
        ]):
            # Complex filters usually need decomposition
            paren_count = expression.count('(')
            if paren_count > 3:
                return MaterializationStrategy.DECOMPOSITION, 3

        # Check for Tier 2 patterns (aligned history)
        if any(pattern in expr_upper for pattern in [
            'PREVIOUSDAY', 'PREVIOUSMONTH', 'YTD', 'MTD', 'DATEADD',
        ]):
            return MaterializationStrategy.ALIGNED_HISTORY, 2

        # Default to Tier 1 (passthrough)
        return MaterializationStrategy.PASSTHROUGH, 1

    def _extract_aligned_measures(self, expression: str) -> list[str]:
        """Infer aligned-history companion suffixes from time-intel expressions."""
        expr_upper = expression.upper()
        suffixes: list[str] = []

        if "SAMEPERIODLASTYEAR" in expr_upper or "DATEADD" in expr_upper or "PREVIOUSYEAR" in expr_upper:
            suffixes.append("_LY")
        if "TOTALYTD" in expr_upper or "YTD" in expr_upper:
            suffixes.append("_YTD")
        if "TOTALMTD" in expr_upper or "MTD" in expr_upper:
            suffixes.append("_MTD")
        if "PREVIOUSMONTH" in expr_upper:
            suffixes.append("_PM")

        seen: set[str] = set()
        ordered: list[str] = []
        for s in suffixes:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
        return ordered

    def _extract_components(self, expression: str) -> dict[str, str]:
        """Infer decomposition numerator/denominator for ratio-like expressions."""
        expr = expression.strip()
        expr_upper = expr.upper()

        if "DIVIDE(" in expr_upper:
            # Very lightweight parse: DIVIDE(<num>, <den>)
            start = expr_upper.find("DIVIDE(") + len("DIVIDE(")
            end = expr.rfind(")")
            if end > start:
                payload = expr[start:end]
                parts = [p.strip() for p in payload.split(",", 1)]
                if len(parts) == 2:
                    return {"_Num": parts[0], "_Denom": parts[1]}

        if "/" in expr:
            parts = [p.strip() for p in expr.split("/", 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                return {"_Num": parts[0], "_Denom": parts[1]}

        return {}

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
