from __future__ import annotations

from semabridge.sml.models import Cardinality, DataType, SMLColumn, SMLDataset, SMLMetric, SMLModel, SMLRelationship
from semabridge.utils.semantic_model_validator import SemanticModelValidator


def _build_model() -> SMLModel:
    return SMLModel(
        unique_name="validator_model",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="CustomerId", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Product",
                columns=[
                    SMLColumn(unique_name="ProductId", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="CategoryId", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[
                    SMLColumn(unique_name="CustomerId", data_type=DataType.INTEGER),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Revenue",
                dataset="Sales",
                source_column="MissingRevenue",
                expression="SUM(Sales[MissingRevenue]) + COUNTROWS(Product[CategoryId])",
                depends_on_measures=["Gross Margin"],
            )
        ],
        relationships=[
            SMLRelationship(
                unique_name="Sales_to_Customer",
                from_dataset="Sales",
                from_columns=["CustomerId"],
                to_dataset="Customer",
                to_columns=["CustomerId"],
                cardinality=Cardinality.MANY_TO_ONE,
            )
        ],
    )


def test_validator_reports_missing_column_measure_and_relationship():
    model = _build_model()

    report = SemanticModelValidator().validate(model)

    assert not report.is_valid
    codes = {finding.code for finding in report.findings}
    assert "MISSING_COLUMN" in codes
    assert "MISSING_MEASURE" in codes
    assert "MISSING_RELATIONSHIP" in codes


def test_validator_flags_missing_source_column():
    model = _build_model()

    result = SemanticModelValidator().validate_metric(model, model.metrics[0])

    assert any(finding.code == "MISSING_COLUMN" and finding.column_name == "MissingRevenue" for finding in result.findings)


def test_validator_flags_unreachable_table_reference():
    model = _build_model()

    result = SemanticModelValidator().validate_metric(model, model.metrics[0])

    assert any(finding.code == "MISSING_RELATIONSHIP" and finding.table_name == "Product" for finding in result.findings)