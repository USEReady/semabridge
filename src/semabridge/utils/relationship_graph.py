"""Target-agnostic relationship-graph reachability.

Extracted from SnowflakeEmitter._relationship_edges/_find_relationship_path
(connectors/snowflake_emitter.py), which needed this purely as a BFS over
``model.relationships`` but had it entangled with a live Snowflake
connection/schema-manager instance. This module has no such dependency —
pure graph traversal over a model's relationship list, no I/O — so it can
run at mapping time, before any target is even chosen, and be reused by any
target connector (see databricks_publisher.py's independent
``_find_relationship_path``, which duplicates this same pattern a third
time and is a candidate to migrate here too, not done as part of this
change).

Primary use case today: detecting whether a metric's DAX/SQL expression
references a dimension unreachable from its own base table — the class of
failure Snowflake's semantic-view compiler reports as "A metric cannot
refer to another dimension from an unrelated entity" (error 010211) — see
``converter/dax_ast_parser.py``'s
``dax_calculate_filters_unreachable_dimension``.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Tuple


class RelationshipEdge:
    """One traversable hop discovered by find_relationship_path.

    current_dataset/next_dataset name the two ends of the hop in the
    direction actually traversed, which may be the reverse of how the
    underlying relationship declared from_dataset/to_dataset — an
    is_active relationship is traversable in either direction for pure
    reachability purposes (this module never emits SQL/JOINs, it only
    answers "is there a path").
    """

    __slots__ = ("current_dataset", "next_dataset", "current_columns", "next_columns")

    def __init__(
        self,
        current_dataset: str,
        next_dataset: str,
        current_columns: List[str],
        next_columns: List[str],
    ) -> None:
        self.current_dataset = current_dataset
        self.next_dataset = next_dataset
        self.current_columns = current_columns
        self.next_columns = next_columns

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"RelationshipEdge({self.current_dataset!r} -> {self.next_dataset!r})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, RelationshipEdge):
            return NotImplemented
        return (
            self.current_dataset == other.current_dataset
            and self.next_dataset == other.next_dataset
            and self.current_columns == other.current_columns
            and self.next_columns == other.next_columns
        )


def build_relationship_edges(relationships: Iterable[Any], dataset_name: str) -> List[RelationshipEdge]:
    """All is_active relationship edges touching *dataset_name*, in the
    direction FROM dataset_name.

    Accepts any relationship-like objects exposing ``is_active``,
    ``from_dataset``, ``to_dataset``, ``from_columns``, ``to_columns``
    attributes (e.g. SMLRelationship) — not typed to a specific model
    class, so any source/target's relationship representation works as
    long as it exposes these.
    """
    edges: List[RelationshipEdge] = []
    for rel in relationships or []:
        if not getattr(rel, "is_active", True):
            continue
        from_ds = getattr(rel, "from_dataset", None)
        to_ds = getattr(rel, "to_dataset", None)
        from_cols = list(getattr(rel, "from_columns", []) or [])
        to_cols = list(getattr(rel, "to_columns", []) or [])
        if not from_ds or not to_ds or not from_cols or not to_cols:
            continue
        if str(from_ds).casefold() == str(dataset_name).casefold():
            edges.append(RelationshipEdge(from_ds, to_ds, from_cols, to_cols))
        if str(to_ds).casefold() == str(dataset_name).casefold():
            edges.append(RelationshipEdge(to_ds, from_ds, to_cols, from_cols))
    return edges


def find_relationship_path(
    relationships: Iterable[Any],
    start_dataset: str,
    target_dataset: str,
) -> Optional[List[RelationshipEdge]]:
    """BFS shortest path of relationship hops from start_dataset to
    target_dataset.

    Returns:
      - ``[]`` if start_dataset and target_dataset are the SAME dataset
        (trivially connected, zero hops needed).
      - a non-empty ``list[RelationshipEdge]`` if a path exists.
      - ``None`` if NO path exists at all.

    This return-type distinction is the fix for an ambiguity in the
    original ``SnowflakeEmitter._find_relationship_path`` this was
    extracted from: that method returned ``[]`` for BOTH "same dataset"
    and "no path found," so its one existing caller's ``if not path:``
    check could not (and did not need to, for that call site) tell the
    two apart. A caller that DOES need to tell them apart — e.g. a
    detector that must not fire on a metric whose own table already
    "reaches" itself — must check ``is None`` explicitly, not ``not path``.
    """
    relationships = list(relationships or [])
    if str(start_dataset).casefold() == str(target_dataset).casefold():
        return []

    queue: List[Tuple[str, List[RelationshipEdge]]] = [(start_dataset, [])]
    visited = {str(start_dataset).casefold()}
    while queue:
        current, path = queue.pop(0)
        for edge in build_relationship_edges(relationships, current):
            key = str(edge.next_dataset).casefold()
            if key in visited:
                continue
            next_path = [*path, edge]
            if key == str(target_dataset).casefold():
                return next_path
            visited.add(key)
            queue.append((edge.next_dataset, next_path))
    return None


def has_relationship_path(relationships: Iterable[Any], start_dataset: str, target_dataset: str) -> bool:
    """True if ANY path — including the trivial same-dataset case —
    connects start_dataset to target_dataset. Convenience wrapper over
    find_relationship_path for callers that only care about reachability,
    not the actual hop sequence."""
    return find_relationship_path(relationships, start_dataset, target_dataset) is not None
