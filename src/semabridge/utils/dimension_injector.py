"""Recursive dimension injector for fact-centric semantic routing.

Given a fact table and relationship graph, discovers all dimensions reachable from that fact
using breadth-first search with cycle detection and deterministic ordering.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from semabridge.utils.semantic_graph import SemanticGraph


class DimensionInjectorError(Exception):
    """Raised when dimension injection fails."""

    pass


@dataclass
class TraversalTrace:
    """Metadata about a dimension injection traversal."""

    start_table: str
    visited_tables: list[str] = field(default_factory=list)
    max_depth: int = 10
    max_depth_reached: bool = False
    cycles_detected: int = 0


class DimensionInjector:
    """Service that discovers all dimensions reachable from a fact table.

    Uses breadth-first search to traverse the relationship graph, stopping at
    the first occurrence of each table (cycle-safe), and returns results in
    deterministic alphabetical order.
    """

    def __init__(self, max_depth: int = 10):
        """Initialize injector with max traversal depth.

        Args:
            max_depth: Maximum depth to traverse from fact. Prevents infinite
                traversal in graphs with problematic structures. Default 10.
        """
        self.max_depth = max_depth
        self._traces: dict[str, TraversalTrace] = {}

    def get_dimensions_for_fact(self, fact: str, graph: SemanticGraph) -> list[str]:
        """Discover all dimensions reachable from the given fact table.

        Performs breadth-first traversal of the relationship graph starting from
        the fact table. Each reachable table is visited once (cycle-safe). Results
        are returned in deterministic alphabetical order.

        Args:
            fact: Name of the fact table (will be canonicalized by graph)
            graph: SemanticGraph instance to traverse

        Returns:
            List of dimension table names in alphabetical order, excluding the fact itself

        Raises:
            DimensionInjectorError: If fact table is not found in graph
        """
        # Let SemanticGraph handle canonicalization - it will raise ValueError if not found
        try:
            canonical_fact = graph._canonical(fact)
        except ValueError as e:
            raise DimensionInjectorError(str(e))

        # Initialize trace
        trace = TraversalTrace(start_table=canonical_fact, max_depth=self.max_depth)

        # BFS traversal
        visited: set[str] = {canonical_fact}
        queue: deque[tuple[str, int]] = deque([(canonical_fact, 0)])
        dimensions: set[str] = set()

        while queue:
            current_table, depth = queue.popleft()

            # Check depth limit
            if depth >= self.max_depth:
                trace.max_depth_reached = True
                continue

            # Get neighbors from graph
            neighbors = graph.get_neighbors(current_table)

            for neighbor_table in neighbors:
                if neighbor_table in visited:
                    # Cycle detected - skip
                    trace.cycles_detected += 1
                    continue

                visited.add(neighbor_table)
                dimensions.add(neighbor_table)
                trace.visited_tables.append(neighbor_table)
                queue.append((neighbor_table, depth + 1))

        # Store trace for later retrieval
        self._traces[fact] = trace

        # Return sorted list for determinism
        return sorted(list(dimensions))

    def get_traversal_trace(self, fact: str) -> dict | None:
        """Get metadata about the traversal for a given fact.

        Args:
            fact: Name of the fact table

        Returns:
            Dictionary with traversal metadata (visited tables, cycles, max depth reached),
            or None if no traversal has been performed for this fact.
        """
        trace = self._traces.get(fact)
        if trace is None:
            return None

        return {
            "start_table": trace.start_table,
            "visited_tables": trace.visited_tables,
            "max_depth": trace.max_depth,
            "max_depth_reached": trace.max_depth_reached,
            "cycles_detected": trace.cycles_detected,
        }
