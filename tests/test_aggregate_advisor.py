"""Tests for Mandate 6: Aggregate Advisor — automatic aggregate recommendations."""

import pytest
from semabridge.connectors.aggregate_advisor import AggregateAdvisor, AggregateRecommendation
from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, AggregationType, Cardinality, CrossFilterDirection,
)


@pytest.fixture
def sales_model():
    """Create a realistic SML model with fact/dim tables."""
    return SMLModel(
        unique_name="SalesModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                source_table="SALES",
                is_fact=True,
                row_count=5_000_000,
                columns=[
                    SMLColumn(unique_name="OrderID", is_key=True, data_type=DataType.INTEGER),
                    SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="ProductID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Quantity", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="OrderDate", data_type=DataType.DATE),
                ],
            ),
            SMLDataset(
                unique_name="Customers",
                source_table="CUSTOMERS",
                columns=[
                    SMLColumn(unique_name="CustomerID", is_key=True, data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Region", data_type=DataType.STRING),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TotalRevenue",
                dataset="Sales",
                aggregation=AggregationType.SUM,
                source_column="Revenue",
            ),
            SMLMetric(
                unique_name="TotalQuantity",
                dataset="Sales",
                aggregation=AggregationType.SUM,
                source_column="Quantity",
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="Sales_Customers",
                from_dataset="Sales",
                from_columns=["CustomerID"],
                to_dataset="Customers",
                to_columns=["CustomerID"],
                cardinality=Cardinality.MANY_TO_ONE,
                cross_filter=CrossFilterDirection.SINGLE,
            ),
        ],
    )


class TestAggregateAdvisor:
    """Test aggregate recommendation generation."""

    def test_generates_recommendations(self, sales_model):
        advisor = AggregateAdvisor(min_row_threshold=1_000_000)
        recs = advisor.analyze(sales_model)
        assert len(recs) >= 1

    def test_recommendation_has_ddl(self, sales_model):
        advisor = AggregateAdvisor(min_row_threshold=1_000_000)
        recs = advisor.analyze(sales_model)
        assert all(rec.ddl for rec in recs)
        assert all("CREATE OR REPLACE TABLE" in rec.ddl for rec in recs)

    def test_recommendation_has_metrics(self, sales_model):
        advisor = AggregateAdvisor(min_row_threshold=1_000_000)
        recs = advisor.analyze(sales_model)
        for rec in recs:
            assert len(rec.metrics) >= 1

    def test_skips_small_tables(self):
        """Tables below row threshold should be skipped."""
        small_model = SMLModel(
            unique_name="SmallModel",
            datasets=[
                SMLDataset(
                    unique_name="Tiny",
                    source_table="TINY",
                    is_fact=True,
                    row_count=100,
                    columns=[
                        SMLColumn(unique_name="ID", is_key=True),
                        SMLColumn(unique_name="Value", data_type=DataType.DECIMAL),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="SumVal",
                    dataset="Tiny",
                    aggregation=AggregationType.SUM,
                    source_column="Value",
                ),
            ],
        )
        advisor = AggregateAdvisor(min_row_threshold=1_000)
        recs = advisor.analyze(small_model)
        assert len(recs) == 0

    def test_uses_relationship_columns(self, sales_model):
        advisor = AggregateAdvisor(min_row_threshold=1_000_000)
        recs = advisor.analyze(sales_model)
        # Should use CustomerID from the relationship as a group-by column
        for rec in recs:
            if rec.source_dataset == "Sales":
                assert "CustomerID" in rec.group_by_columns

    def test_qualified_ddl(self, sales_model):
        advisor = AggregateAdvisor(
            min_row_threshold=1_000_000,
            database="PROD_DB",
            schema_name="ANALYTICS",
        )
        recs = advisor.analyze(sales_model)
        for rec in recs:
            assert "PROD_DB.ANALYTICS" in rec.ddl
