"""Service to extract which measures belong to each fact table.

A measure may reference dimensions reachable only from some facts, making
per-fact filtering necessary. This module builds a mapping of metrics to
their applicable anchor facts.
"""

from __future__ import annotations

import re
from functools import lru_cache

from semabridge.sml.models import SMLMetric
from semabridge.utils.semantic_graph import SemanticGraph


class MeasureFactMappingError(Exception):
    """Raised when measure fact mapping fails."""

    pass


class MeasureFactMapping:
    """Service that extracts which measures belong to each fact table.

    A measure is anchored to facts where all its referenced dimensions are
    reachable via the relationship graph. Uses regex-based heuristic to
    parse DAX expressions and extract table references.
    """

    # DAX table reference patterns (regex-based heuristic)
    # Matches: [TableName], Table[ColumnName], RELATED(Table[...]), etc.
    _DAX_TABLE_PATTERNS = [
        r"\[([^\]]+)\]\.",  # [TableName]. reference
        r"(\w+)\[",  # TableName[ reference
        r"(?:FILTER|CALCULATE)\s*\(\s*\[([^\]]+)\]",  # FILTER([TableName] or CALCULATE(...[TableName])
    ]

    def __init__(self):
        """Initialize measure fact mapping service."""
        self._expression_cache: dict[str, set[str]] = {}

    @lru_cache(maxsize=1024)
    def _parse_dax_expression(self, expression: str) -> frozenset[str]:
        """Parse DAX expression to extract table references.

        Uses regex-based heuristics to find table names referenced in DAX.
        Results are cached for performance.

        Args:
            expression: DAX expression string

        Returns:
            Frozenset of referenced table names (normalized to uppercase)
        """
        if not expression or not expression.strip():
            return frozenset()

        referenced_tables: set[str] = set()

        for pattern in self._DAX_TABLE_PATTERNS:
            matches = re.findall(pattern, expression, re.IGNORECASE)
            for match in matches:
                table_name = match.strip()
                if table_name:  # Skip empty matches
                    referenced_tables.add(table_name.upper())

        return frozenset(referenced_tables)

    def extract_measure_facts(
        self,
        metrics: list[SMLMetric],
        fact_tables: set[str],
        dimension_injector,
        graph: SemanticGraph,
    ) -> dict[str, set[str]]:
        """Extract which facts can anchor each metric.

        For each metric, determines which fact tables it can be anchored to.
        A metric can anchor to a fact if all dimensions it references are
        reachable from that fact via the relationship graph.

        Args:
            metrics: List of SMLMetric objects to process
            fact_tables: Set of fact table names (typically from TableCategorizer)
            dimension_injector: DimensionInjector instance for dimension lookup
            graph: SemanticGraph instance for relationship queries

        Returns:
            Mapping {metric_name: {fact_tables_that_can_anchor_this_metric}}
            If a metric has no references or cannot anchor to any fact, it maps to empty set.

        Raises:
            MeasureFactMappingError: If metric has invalid structure or graph query fails
        """
        result: dict[str, set[str]] = {}

        for metric in metrics:
            try:
                metric_name = metric.unique_name
                expression = metric.expression or ""

                # Extract referenced tables from expression
                referenced_tables = self._parse_dax_expression(expression)

                # Determine which facts can anchor this metric
                applicable_facts = self._find_applicable_facts(
                    referenced_tables, fact_tables, dimension_injector, graph
                )

                result[metric_name] = applicable_facts

            except Exception as e:
                raise MeasureFactMappingError(
                    f"Failed to map metric '{metric.unique_name}': {str(e)}"
                ) from e

        return result

    def _find_applicable_facts(
        self,
        referenced_tables: set[str],
        fact_tables: set[str],
        dimension_injector,
        graph: SemanticGraph,
    ) -> set[str]:
        """Find which facts can anchor a metric given its referenced tables.

        A metric can anchor to a fact if all its referenced dimensions are
        reachable from that fact via the relationship graph.

        Args:
            referenced_tables: Set of table names referenced in the metric expression
            fact_tables: Set of all fact table names
            dimension_injector: DimensionInjector for dimension lookup
            graph: SemanticGraph for relationship queries

        Returns:
            Set of fact table names that can anchor this metric
        """
        if not referenced_tables:
            # Metrics with no table references can anchor to all facts
            return set(fact_tables)

        # Canonicalize referenced table names using the graph
        canonical_referenced: set[str] = set()
        for table_name in referenced_tables:
            try:
                canonical_name = graph._canonical(table_name)
                canonical_referenced.add(canonical_name)
            except ValueError:
                # Table not found in graph, cannot be anchored
                return set()

        applicable_facts: set[str] = set()

        for fact in fact_tables:
            # Get dimensions reachable from this fact
            try:
                reachable_dimensions = dimension_injector.get_dimensions_for_fact(fact, graph)
                # Include the fact itself as a reachable table
                reachable_tables = {fact} | set(reachable_dimensions)

                # Check if all referenced tables are reachable
                if canonical_referenced.issubset(reachable_tables):
                    applicable_facts.add(fact)

            except Exception:
                # If dimension lookup fails for this fact, skip it
                continue

        return applicable_facts

    def clear_cache(self) -> None:
        """Clear expression parse cache."""
        self._parse_dax_expression.cache_clear()
        self._expression_cache.clear()
