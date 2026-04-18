"""Tests for deterministic table categorization based on semantic graph."""

from __future__ import annotations

from semabridge.sml.models import (
    Cardinality,
    DataType,
    SMLColumn,
    SMLMetric,
    SMLDataset,
    SMLModel,
    SMLRelationship,
)
from semabridge.utils.semantic_graph import SemanticGraph
from semabridge.utils.table_categorizer import (
    CATEGORY_BRIDGE,
    CATEGORY_DIMENSION,
    CATEGORY_FACT,
    TableCategorizer,
    extract_and_remove_dummy_tables,
)


def _base_model(relationships: list[SMLRelationship]) -> SMLModel:
    return SMLModel(
        unique_name="CategorizerModel",
        datasets=[
            SMLDataset(unique_name="Fact", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)]),
            SMLDataset(unique_name="Dim", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)]),
            SMLDataset(unique_name="Bridge", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)]),
        ],
        relationships=relationships,
    )


def test_table_categorizer_identifies_fact_and_dimension() -> None:
    model = _base_model(
        [
            SMLRelationship(
                unique_name="fact_to_dim",
                from_dataset="Fact",
                from_columns=["dim_id"],
                to_dataset="Dim",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ]
    )

    graph = SemanticGraph(model)
    categorizer = TableCategorizer()
    result = categorizer.categorize(graph)

    assert result["Fact"]["category"] == CATEGORY_FACT
    assert result["Dim"]["category"] == CATEGORY_DIMENSION


def test_table_categorizer_identifies_bridge_for_mixed_direction() -> None:
    model = _base_model(
        [
            SMLRelationship(
                unique_name="fact_to_bridge",
                from_dataset="Fact",
                from_columns=["bridge_id"],
                to_dataset="Bridge",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="bridge_to_dim",
                from_dataset="Bridge",
                from_columns=["dim_id"],
                to_dataset="Dim",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ]
    )

    graph = SemanticGraph(model)
    categorizer = TableCategorizer()
    result = categorizer.categorize(graph)

    assert result["Bridge"]["category"] == CATEGORY_BRIDGE


def test_table_categorizer_defaults_isolated_table_to_fact() -> None:
    model = SMLModel(
        unique_name="SingleTable",
        datasets=[SMLDataset(unique_name="OnlyTable", columns=[SMLColumn(unique_name="id", data_type=DataType.INTEGER)])],
        relationships=[],
    )

    graph = SemanticGraph(model)
    categorizer = TableCategorizer()
    result = categorizer.categorize(graph)

    assert result["OnlyTable"]["category"] == CATEGORY_FACT
    category, confidence = categorizer.get_category("OnlyTable")
    assert category == CATEGORY_FACT
    assert confidence == "LOW"


def test_table_categorizer_output_is_deterministic() -> None:
    model = _base_model(
        [
            SMLRelationship(
                unique_name="fact_to_dim",
                from_dataset="Fact",
                from_columns=["dim_id"],
                to_dataset="Dim",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ]
    )

    graph = SemanticGraph(model)
    categorizer = TableCategorizer()

    first = categorizer.categorize(graph)
    second = categorizer.categorize(graph)

    assert first == second


def test_extract_and_remove_dummy_tables_routes_measure_and_drops_dummy() -> None:
    model = SMLModel(
        unique_name="DummyRoutingModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
            SMLDataset(unique_name="Project Measures", columns=[]),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Sales",
                dataset="Project Measures",
                expression="SUM(Sales[Amount])",
            )
        ],
        relationships=[],
    )

    mutated, summary = extract_and_remove_dummy_tables(model)
    measure = mutated.get_metric("Total Sales")

    assert measure is not None
    assert [ds.unique_name for ds in mutated.datasets] == ["Sales"]
    assert measure.dataset == "Sales"
    assert summary["dummy_tables_dropped"] == 1
    assert summary["dead_tables_dropped"] == 0
    assert summary["measures_routed"] == 1
    assert summary["measures_flagged"] == 0


def test_extract_and_remove_dummy_tables_flags_ambiguous_measure() -> None:
    model = SMLModel(
        unique_name="DummyAmbiguousModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL)],
            ),
            SMLDataset(
                unique_name="Orders",
                columns=[SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL)],
            ),
            SMLDataset(unique_name="Project Measures", columns=[]),
        ],
        metrics=[
            SMLMetric(
                unique_name="Blended Metric",
                dataset="Project Measures",
                expression="SUM(Sales[Amount]) + SUM(Orders[Amount])",
            )
        ],
        relationships=[],
    )

    mutated, summary = extract_and_remove_dummy_tables(model)
    measure = mutated.get_metric("Blended Metric")

    assert measure is not None
    assert measure.dataset == "Project Measures"
    assert measure.sync_enabled is False
    assert measure.sync_failure_reason == "DUMMY_TABLE_ROUTING_AMBIGUOUS"
    assert summary["dummy_tables_dropped"] == 1
    assert summary["measures_routed"] == 0
    assert summary["measures_flagged"] == 1
    assert hasattr(mutated, "review_required_measures")
    assert len(mutated.review_required_measures) == 1
