"""Unit tests for semabridge.utils.relationship_graph — the target-agnostic
BFS extracted from SnowflakeEmitter._relationship_edges/_find_relationship_path.

All synthetic (SMLRelationship instances) — no DB, no emitter, no target
connector. Placeholder table names throughout, none tied to any real model.
"""
from __future__ import annotations

from semabridge.sml.models import SMLRelationship
from semabridge.utils.relationship_graph import (
    build_relationship_edges,
    find_relationship_path,
    has_relationship_path,
)


def _rel(from_ds, from_cols, to_ds, to_cols, is_active=True):
    return SMLRelationship(
        unique_name=f"REL_{from_ds}_{to_ds}",
        from_dataset=from_ds,
        from_columns=from_cols,
        to_dataset=to_ds,
        to_columns=to_cols,
        is_active=is_active,
    )


def test_direct_relationship_both_directions_produce_an_edge():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    assert len(build_relationship_edges(rels, "Fact")) == 1
    assert len(build_relationship_edges(rels, "Product")) == 1
    assert build_relationship_edges(rels, "Fact")[0].next_dataset == "Product"
    assert build_relationship_edges(rels, "Product")[0].next_dataset == "Fact"


def test_inactive_relationship_produces_no_edge():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"], is_active=False)]
    assert build_relationship_edges(rels, "Fact") == []


def test_find_path_direct_hop():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    path = find_relationship_path(rels, "Fact", "Product")
    assert path is not None
    assert len(path) == 1
    assert path[0].next_dataset == "Product"


def test_find_path_multi_hop():
    rels = [
        _rel("Fact", ["ProductId"], "Product", ["ProductId"]),
        _rel("Product", ["CategoryId"], "Category", ["CategoryId"]),
    ]
    path = find_relationship_path(rels, "Fact", "Category")
    assert path is not None
    assert [e.next_dataset for e in path] == ["Product", "Category"]


def test_no_path_returns_none_not_empty_list():
    """The exact []-vs-None ambiguity fix: a disconnected table (no
    relationship at all) must return None, distinguishable from the
    trivial same-dataset case, which returns []."""
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    path = find_relationship_path(rels, "Disconnected", "Product")
    assert path is None


def test_same_dataset_returns_empty_list_not_none():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    path = find_relationship_path(rels, "Fact", "Fact")
    assert path == []
    assert path is not None


def test_has_relationship_path_true_for_same_dataset():
    assert has_relationship_path([], "Fact", "Fact") is True


def test_has_relationship_path_false_for_disconnected_table():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    assert has_relationship_path(rels, "Disconnected", "Product") is False


def test_has_relationship_path_true_for_multi_hop():
    rels = [
        _rel("Fact", ["ProductId"], "Product", ["ProductId"]),
        _rel("Product", ["CategoryId"], "Category", ["CategoryId"]),
    ]
    assert has_relationship_path(rels, "Fact", "Category") is True


def test_path_finding_is_case_insensitive_on_dataset_names():
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    assert has_relationship_path(rels, "fact", "PRODUCT") is True


def test_no_relationships_at_all_means_no_path_between_distinct_tables():
    """The KPI-selector-table shape: a dataset with zero relationships
    anywhere in the model has no path to anything but itself."""
    rels = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]
    assert has_relationship_path(rels, "SelectorTable", "Date") is False
    assert has_relationship_path(rels, "SelectorTable", "SelectorTable") is True
