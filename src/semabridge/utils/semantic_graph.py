"""Deterministic semantic relationship graph utilities.

Builds a normalized, deterministic graph from SML relationships so routing logic
can consistently identify fact/dimension pathways.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from semabridge.sml.models import SMLModel, SMLRelationship


@dataclass(frozen=True)
class GraphEdge:
    """Normalized directed edge in semantic graph."""

    source: str
    target: str
    cardinality: str
    relationship_name: str


class SemanticGraph:
    """Deterministic, cycle-aware relationship graph for semantic routing."""

    def __init__(self, model: SMLModel):
        self._canonical_names: dict[str, str] = {}
        self._outgoing: dict[str, list[GraphEdge]] = {}
        self._incoming: dict[str, list[GraphEdge]] = {}
        self._cycle_edges: dict[str, set[str]] = {}
        self._many_sides: set[str] = set()
        self._many_to_many_tables: set[str] = set()

        self._register_dataset_names(model)
        self._build_edges(model)
        self._sort_edges()
        self._validate_directed_acyclic_core()

    @property
    def tables(self) -> list[str]:
        """Return graph tables in deterministic order."""
        return sorted(self._canonical_names.values())

    def get_neighbors(self, table: str) -> dict[str, str]:
        """Return deterministic outgoing neighbors and edge cardinality."""
        canonical = self._canonical(table)
        edges = self._outgoing.get(canonical, [])
        return {edge.target: edge.cardinality for edge in edges}

    def get_incoming_neighbors(self, table: str) -> dict[str, str]:
        """Return deterministic incoming neighbors and edge cardinality."""
        canonical = self._canonical(table)
        edges = self._incoming.get(canonical, [])
        return {edge.source: edge.cardinality for edge in edges}

    def get_reachable_from(self, table: str) -> set[str]:
        """Return all tables reachable from the provided table via BFS."""
        start = self._canonical(table)
        visited: set[str] = {start}
        queue: deque[str] = deque([start])

        while queue:
            current = queue.popleft()
            for edge in self._outgoing.get(current, []):
                if edge.target in visited:
                    continue
                visited.add(edge.target)
                queue.append(edge.target)

        visited.discard(start)
        return visited

    def get_many_sides(self) -> set[str]:
        """Return tables identified on the many side of relationships."""
        return set(self._many_sides)

    def is_many_to_many_table(self, table: str) -> bool:
        """Return True when table participates in a many-to-many relationship."""
        canonical = self._canonical(table)
        return canonical in self._many_to_many_tables

    def _register_dataset_names(self, model: SMLModel) -> None:
        for dataset in model.datasets:
            self._register_name(dataset.unique_name)

    def _build_edges(self, model: SMLModel) -> None:
        for relationship in sorted(model.relationships, key=lambda rel: str(rel.unique_name or "")):
            if not relationship.is_active:
                continue
            self._register_relationship_endpoints(relationship)
            self._ingest_relationship(relationship)

        for table in self.tables:
            self._outgoing.setdefault(table, [])
            self._incoming.setdefault(table, [])
            self._cycle_edges.setdefault(table, set())

    def _register_relationship_endpoints(self, relationship: SMLRelationship) -> None:
        self._register_name(relationship.from_dataset)
        self._register_name(relationship.to_dataset)

    def _ingest_relationship(self, relationship: SMLRelationship) -> None:
        card = str(getattr(relationship.cardinality, "value", relationship.cardinality) or "").strip().lower()
        card = card.replace("-", "_")

        from_name = self._canonical(relationship.from_dataset)
        to_name = self._canonical(relationship.to_dataset)

        if card == "many_to_one":
            self._add_edge(from_name, to_name, "many_to_one", relationship.unique_name)
            self._many_sides.add(from_name)
            self._cycle_edges[from_name].add(to_name)
            return

        if card == "one_to_many":
            # Normalize to many -> one direction for deterministic routing.
            self._add_edge(to_name, from_name, "many_to_one", relationship.unique_name)
            self._many_sides.add(to_name)
            self._cycle_edges[to_name].add(from_name)
            return

        if card == "many_to_many":
            # Keep bidirectional traversal support, but do not include in DAG cycle core.
            self._add_edge(from_name, to_name, "many_to_many", relationship.unique_name)
            self._add_edge(to_name, from_name, "many_to_many", relationship.unique_name)
            self._many_sides.add(from_name)
            self._many_sides.add(to_name)
            self._many_to_many_tables.add(from_name)
            self._many_to_many_tables.add(to_name)
            return

        if card == "one_to_one":
            self._add_edge(from_name, to_name, "one_to_one", relationship.unique_name)
            self._add_edge(to_name, from_name, "one_to_one", relationship.unique_name)
            return

        # Unknown cardinality: preserve directed edge as modeled.
        self._add_edge(from_name, to_name, "unknown", relationship.unique_name)
        self._cycle_edges[from_name].add(to_name)

    def _add_edge(self, source: str, target: str, cardinality: str, relationship_name: str) -> None:
        self._outgoing.setdefault(source, []).append(
            GraphEdge(
                source=source,
                target=target,
                cardinality=cardinality,
                relationship_name=str(relationship_name or ""),
            )
        )
        self._incoming.setdefault(target, []).append(
            GraphEdge(
                source=source,
                target=target,
                cardinality=cardinality,
                relationship_name=str(relationship_name or ""),
            )
        )

    def _sort_edges(self) -> None:
        for table in list(self._outgoing):
            self._outgoing[table] = sorted(
                self._outgoing[table],
                key=lambda edge: (edge.target, edge.cardinality, edge.relationship_name),
            )
        for table in list(self._incoming):
            self._incoming[table] = sorted(
                self._incoming[table],
                key=lambda edge: (edge.source, edge.cardinality, edge.relationship_name),
            )

    def _validate_directed_acyclic_core(self) -> None:
        """Validate no directed cycles exist in many->one/unknown core graph."""

        visited: set[str] = set()
        active_stack: set[str] = set()

        def dfs(node: str, path: list[str]) -> None:
            if node in active_stack:
                cycle_start = path.index(node)
                cycle_path = path[cycle_start:] + [node]
                cycle_text = " -> ".join(cycle_path)
                raise ValueError(f"Relationship cycle detected: {cycle_text}")

            if node in visited:
                return

            visited.add(node)
            active_stack.add(node)
            next_nodes = sorted(self._cycle_edges.get(node, set()))
            for next_node in next_nodes:
                dfs(next_node, path + [next_node])
            active_stack.remove(node)

        for node in sorted(self._cycle_edges):
            if node in visited:
                continue
            dfs(node, [node])

    def _register_name(self, name: str) -> None:
        cleaned = str(name or "").strip()
        if not cleaned:
            return
        key = cleaned.upper()
        existing = self._canonical_names.get(key)
        if existing is None:
            self._canonical_names[key] = cleaned
            self._outgoing.setdefault(cleaned, [])
            self._incoming.setdefault(cleaned, [])
            self._cycle_edges.setdefault(cleaned, set())
            return

        if cleaned < existing:
            self._canonical_names[key] = cleaned
            self._outgoing.setdefault(cleaned, self._outgoing.pop(existing, []))
            self._incoming.setdefault(cleaned, self._incoming.pop(existing, []))
            self._cycle_edges.setdefault(cleaned, self._cycle_edges.pop(existing, set()))

    def _canonical(self, name: str) -> str:
        key = str(name or "").strip().upper()
        if key not in self._canonical_names:
            raise ValueError(f"Table '{name}' not found in semantic graph")
        return self._canonical_names[key]
