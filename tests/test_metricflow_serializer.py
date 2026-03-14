"""
Tests for Module 4: MetricFlow Serialization.

Verifies that OSI models are correctly converted to dbt MetricFlow
YAML format (semantic_models.yml + metrics.yml).
"""

import pytest
import yaml
from unittest.mock import MagicMock

from semabridge.converter.metricflow_serializer import (
    MetricFlowSerializer,
    _safe_name,
    _infer_time_granularity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_column(name, data_type="string", source_expression=None, is_key=False, is_calculated=False):
    col = MagicMock()
    col.unique_name = name
    col.data_type = MagicMock(value=data_type)
    col.source_expression = source_expression
    col.is_key = is_key
    col.is_calculated = is_calculated
    return col


def _make_dataset(name, columns, source_table=None, description=""):
    ds = MagicMock()
    ds.unique_name = name
    ds.columns = columns
    ds.source_table = source_table or name
    ds.description = description
    return ds


def _make_metric(
    name, dataset, source_column=None, aggregation="sum",
    sql_expression=None, expression=None, description="", label=None,
):
    m = MagicMock()
    m.unique_name = name
    m.dataset = dataset
    m.source_column = source_column
    m.aggregation = MagicMock(value=aggregation)
    m.sql_expression = sql_expression
    m.expression = expression
    m.description = description
    m.label = label or name
    return m


def _make_relationship(name, from_ds, to_ds, from_cols, to_cols):
    r = MagicMock()
    r.unique_name = name
    r.from_dataset = from_ds
    r.to_dataset = to_ds
    r.from_columns = from_cols
    r.to_columns = to_cols
    return r


def _make_model(name, datasets, metrics=None, relationships=None, dimensions=None):
    model = MagicMock()
    model.unique_name = name
    model.label = name
    model.datasets = datasets
    model.metrics = metrics or []
    model.relationships = relationships or []
    model.dimensions = dimensions or []
    return model


# ---------------------------------------------------------------------------
# _safe_name()
# ---------------------------------------------------------------------------

class TestSafeName:
    def test_simple(self):
        assert _safe_name("RevenueTotal") == "revenuetotal"

    def test_spaces_and_special(self):
        assert _safe_name("Revenue Total ($)") == "revenue_total"

    def test_empty(self):
        assert _safe_name("") == "unknown"

    def test_underscores(self):
        assert _safe_name("my__metric__name") == "my_metric_name"


# ---------------------------------------------------------------------------
# _infer_time_granularity()
# ---------------------------------------------------------------------------

class TestInferTimeGranularity:
    def test_year(self):
        assert _infer_time_granularity("FiscalYear") == "year"

    def test_month(self):
        assert _infer_time_granularity("MONTHNO") == "month"

    def test_quarter(self):
        assert _infer_time_granularity("QuarterEnd") == "quarter"

    def test_week(self):
        assert _infer_time_granularity("WeekNumber") == "week"

    def test_default_day(self):
        assert _infer_time_granularity("OrderDate") == "day"


# ---------------------------------------------------------------------------
# MetricFlowSerializer.serialize()
# ---------------------------------------------------------------------------

class TestMetricFlowSerializerBasic:
    """Basic serialization tests."""

    def test_serialize_produces_yaml_files(self):
        ds = _make_dataset("FactSales", [
            _make_column("Amount", "decimal"),
            _make_column("OrderDate", "date"),
        ])
        metric = _make_metric("TotalSales", "FactSales", source_column="Amount")
        model = _make_model("SalesModel", [ds], [metric])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)

        assert "semantic_models.yml" in files
        assert "metrics.yml" in files

    def test_semantic_model_yaml_is_valid(self):
        ds = _make_dataset("FactSales", [
            _make_column("Amount", "decimal"),
            _make_column("OrderDate", "date"),
        ])
        metric = _make_metric("TotalSales", "FactSales", source_column="Amount")
        model = _make_model("SalesModel", [ds], [metric])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)

        parsed = yaml.safe_load(files["semantic_models.yml"])
        assert "semantic_models" in parsed
        assert len(parsed["semantic_models"]) == 1
        sm = parsed["semantic_models"][0]
        assert sm["name"] == "factsales"

    def test_metrics_yaml_is_valid(self):
        ds = _make_dataset("FactSales", [
            _make_column("Amount", "decimal"),
        ])
        metric = _make_metric("TotalSales", "FactSales", source_column="Amount")
        model = _make_model("SalesModel", [ds], [metric])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)

        parsed = yaml.safe_load(files["metrics.yml"])
        assert "metrics" in parsed
        assert len(parsed["metrics"]) == 1
        assert parsed["metrics"][0]["name"] == "totalsales"
        assert parsed["metrics"][0]["type"] == "simple"


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

class TestMetricFlowEntities:
    def test_pk_entity_from_relationship(self):
        ds1 = _make_dataset("FactSales", [
            _make_column("ProductId", "integer"),
            _make_column("Amount", "decimal"),
        ])
        ds2 = _make_dataset("DimProduct", [
            _make_column("ProductId", "integer"),
            _make_column("ProductName", "string"),
        ])
        rel = _make_relationship(
            "fk_product", "FactSales", "DimProduct",
            ["ProductId"], ["ProductId"],
        )
        model = _make_model("Sales", [ds1, ds2], relationships=[rel])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        # DimProduct should have a primary entity for ProductId
        dim_sm = next(
            sm for sm in parsed["semantic_models"]
            if sm["name"] == "dimproduct"
        )
        assert any(
            e["type"] == "primary" and "PRODUCTID" in e["expr"]
            for e in dim_sm.get("entities", [])
        )

    def test_fk_entity(self):
        ds1 = _make_dataset("FactSales", [
            _make_column("ProductId", "integer"),
        ])
        ds2 = _make_dataset("DimProduct", [
            _make_column("ProductId", "integer"),
        ])
        rel = _make_relationship(
            "fk_product", "FactSales", "DimProduct",
            ["ProductId"], ["ProductId"],
        )
        model = _make_model("Sales", [ds1, ds2], relationships=[rel])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        fact_sm = next(
            sm for sm in parsed["semantic_models"]
            if sm["name"] == "factsales"
        )
        assert any(
            e["type"] == "foreign"
            for e in fact_sm.get("entities", [])
        )

    def test_pk_entity_from_is_key(self):
        ds = _make_dataset("DimProduct", [
            _make_column("ProductId", "integer", is_key=True),
            _make_column("Name", "string"),
        ])
        model = _make_model("Sales", [ds])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        sm = parsed["semantic_models"][0]
        assert any(
            e["type"] == "primary"
            for e in sm.get("entities", [])
        )


# ---------------------------------------------------------------------------
# Measures
# ---------------------------------------------------------------------------

class TestMetricFlowMeasures:
    def test_simple_sum_measure(self):
        ds = _make_dataset("FactSales", [
            _make_column("Amount", "decimal"),
        ])
        metric = _make_metric("TotalSales", "FactSales", source_column="Amount", aggregation="sum")
        model = _make_model("Sales", [ds], [metric])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        sm = parsed["semantic_models"][0]
        measures = sm.get("measures", [])
        assert len(measures) == 1
        assert measures[0]["agg"] == "sum"
        assert measures[0]["expr"] == "AMOUNT"

    def test_count_distinct_measure(self):
        ds = _make_dataset("FactOrders", [
            _make_column("CustomerId", "integer"),
        ])
        metric = _make_metric(
            "UniqueCustomers", "FactOrders",
            source_column="CustomerId", aggregation="count_distinct",
        )
        model = _make_model("Orders", [ds], [metric])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        sm = parsed["semantic_models"][0]
        measures = sm.get("measures", [])
        assert measures[0]["agg"] == "count_distinct"


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

class TestMetricFlowDimensions:
    def test_categorical_dimension(self):
        ds = _make_dataset("DimProduct", [
            _make_column("Category", "string"),
        ])
        model = _make_model("Model", [ds])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        sm = parsed["semantic_models"][0]
        dims = sm.get("dimensions", [])
        assert any(d["type"] == "categorical" for d in dims)

    def test_time_dimension(self):
        ds = _make_dataset("FactSales", [
            _make_column("OrderDate", "date"),
        ])
        model = _make_model("Model", [ds])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["semantic_models.yml"])

        sm = parsed["semantic_models"][0]
        dims = sm.get("dimensions", [])
        time_dims = [d for d in dims if d["type"] == "time"]
        assert len(time_dims) >= 1
        assert "time_granularity" in time_dims[0].get("type_params", {})


# ---------------------------------------------------------------------------
# Derived Metrics
# ---------------------------------------------------------------------------

class TestMetricFlowDerivedMetrics:
    def test_derived_metric_from_expression(self):
        ds = _make_dataset("Fact", [_make_column("A", "decimal")])
        metric = _make_metric(
            "ProfitMargin", "Fact",
            sql_expression="SUM(Revenue) / SUM(Cost)",
        )
        model = _make_model("Model", [ds], [metric])

        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        parsed = yaml.safe_load(files["metrics.yml"])

        m = parsed["metrics"][0]
        assert m["type"] == "derived"
        assert "SUM(Revenue) / SUM(Cost)" in m["type_params"]["expr"]


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------

class TestMetricFlowEdgeCases:
    def test_empty_model(self):
        model = _make_model("Empty", [])
        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        # No datasets → no semantic models
        assert files == {} or (
            "semantic_models" not in yaml.safe_load(
                files.get("semantic_models.yml", "semantic_models: []")
            ) or yaml.safe_load(
                files.get("semantic_models.yml", "semantic_models: []")
            )["semantic_models"] == []
        )

    def test_model_with_no_metrics(self):
        ds = _make_dataset("Dim", [_make_column("Name", "string")])
        model = _make_model("Model", [ds])
        serializer = MetricFlowSerializer()
        files = serializer.serialize(model)
        # Should still produce semantic_models.yml (with dimensions)
        assert "semantic_models.yml" in files
