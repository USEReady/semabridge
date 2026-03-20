"""Regression tests for TMSL relationship naming and deduplication."""

from semabridge.connectors.tmsl_generator import TMSLGenerator
from semabridge.sml.models import Cardinality, DataType, SMLColumn, SMLDataset, SMLModel, SMLRelationship


def _base_model_with_relationships(relationships: list[SMLRelationship]) -> SMLModel:
    return SMLModel(
        unique_name="test_model",
        datasets=[
            SMLDataset(
                unique_name="FACT",
                source_table="FACT",
                columns=[SMLColumn(unique_name="PRODUCT_ID", data_type=DataType.INTEGER)],
            ),
            SMLDataset(
                unique_name="PRODUCT",
                source_table="PRODUCT",
                columns=[SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True)],
            ),
        ],
        relationships=relationships,
    )


def test_tmsl_generator_uses_canonical_relationship_name() -> None:
    model = _base_model_with_relationships(
        [
            SMLRelationship(
                unique_name="SYS_RELATIONSHIP_abc123",
                from_dataset="FACT",
                from_columns=["PRODUCT_ID"],
                to_dataset="PRODUCT",
                to_columns=["ID"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ]
    )

    model_bim = TMSLGenerator(model).generate()
    relationships = model_bim["model"]["relationships"]

    assert len(relationships) == 1
    assert relationships[0]["name"] == "REL_FACT_PRODUCT_ID__PRODUCT_ID"
    assert not relationships[0]["name"].startswith("SYS_RELATIONSHIP_")


def test_tmsl_generator_deduplicates_same_endpoint() -> None:
    duplicated = [
        SMLRelationship(
            unique_name="REL_FACT_PRODUCT_ID__PRODUCT_ID",
            from_dataset="FACT",
            from_columns=["PRODUCT_ID"],
            to_dataset="PRODUCT",
            to_columns=["ID"],
            cardinality=Cardinality.MANY_TO_ONE,
        ),
        SMLRelationship(
            unique_name="REL_FACT_PRODUCT_ID__PRODUCT_ID",
            from_dataset="FACT",
            from_columns=["PRODUCT_ID"],
            to_dataset="PRODUCT",
            to_columns=["ID"],
            cardinality=Cardinality.MANY_TO_ONE,
        ),
    ]

    model_bim = TMSLGenerator(_base_model_with_relationships(duplicated)).generate()
    relationships = model_bim["model"]["relationships"]

    assert len(relationships) == 1
    assert relationships[0]["name"] == "REL_FACT_PRODUCT_ID__PRODUCT_ID"
