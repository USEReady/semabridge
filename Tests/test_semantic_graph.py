"""Tests for deterministic semantic relationship graph service."""

from __future__ import annotations

import pytest

from semabridge.sml.models import (
    Cardinality,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLModel,
    SMLRelationship,
)
from semabridge.utils.semantic_graph import SemanticGraph


def _model_with_relationships(relationships: list[SMLRelationship]) -> SMLModel:
    return SMLModel(
        unique_name="GraphModel",
        datasets=[
            SMLDataset(unique_name="Fact", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)]),
            SMLDataset(unique_name="DimA", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)]),
            SMLDataset(unique_name="DimB", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)]),
        ],
        relationships=relationships,
    )


def test_semantic_graph_normalizes_many_to_one_direction() -> None:
    model = _model_with_relationships(
        [
            SMLRelationship(
                unique_name="fact_to_dim",
                from_dataset="Fact",
                from_columns=["dim_id"],
                to_dataset="DimA",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ]
    )

    graph = SemanticGraph(model)

    assert graph.get_neighbors("Fact") == {"DimA": "many_to_one"}
    assert graph.get_incoming_neighbors("DimA") == {"Fact": "many_to_one"}
    assert graph.get_many_sides() == {"Fact"}


def test_semantic_graph_normalizes_one_to_many_to_many_to_one_core() -> None:
    model = _model_with_relationships(
        [
            SMLRelationship(
                unique_name="dim_to_fact",
                from_dataset="DimA",
                from_columns=["id"],
                to_dataset="Fact",
                to_columns=["dim_id"],
                cardinality=Cardinality.ONE_TO_MANY,
            )
        ]
    )

    graph = SemanticGraph(model)

    # Normalized orientation remains many -> one (Fact -> DimA)
    assert graph.get_neighbors("Fact") == {"DimA": "many_to_one"}
    assert graph.get_many_sides() == {"Fact"}


def test_semantic_graph_reachable_is_deterministic() -> None:
    model = _model_with_relationships(
        [
            SMLRelationship(
                unique_name="fact_to_dimb",
                from_dataset="Fact",
                from_columns=["b_id"],
                to_dataset="DimB",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="fact_to_dima",
                from_dataset="Fact",
                from_columns=["a_id"],
                to_dataset="DimA",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ]
    )

    graph = SemanticGraph(model)
    reachable_first = graph.get_reachable_from("Fact")
    reachable_second = graph.get_reachable_from("Fact")

    assert reachable_first == reachable_second
    assert reachable_first == {"DimA", "DimB"}


def test_semantic_graph_detects_directed_cycle_in_core_graph() -> None:
    model = _model_with_relationships(
        [
            SMLRelationship(
                unique_name="fact_to_dima",
                from_dataset="Fact",
                from_columns=["a_id"],
                to_dataset="DimA",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="dima_to_fact",
                from_dataset="DimA",
                from_columns=["fact_id"],
                to_dataset="Fact",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ]
    )

    with pytest.raises(ValueError, match="Relationship cycle detected"):
        SemanticGraph(model)
