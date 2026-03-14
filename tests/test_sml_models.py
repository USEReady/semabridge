"""
Tests for SML models.
"""

import pytest
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLDimension,
    SMLAttribute,
    SMLHierarchy,
    SMLLevel,
    SMLMetric,
    SMLRelationship,
    DataType,
    AggregationType,
    Cardinality,
)


class TestDataType:
    """Tests for DataType enum."""
    
    def test_from_snowflake_varchar(self):
        assert DataType.from_snowflake("VARCHAR") == DataType.STRING
        assert DataType.from_snowflake("VARCHAR(100)") == DataType.STRING
    
    def test_from_snowflake_number(self):
        assert DataType.from_snowflake("NUMBER") == DataType.DECIMAL
        assert DataType.from_snowflake("NUMBER(10,2)") == DataType.DECIMAL
    
    def test_from_snowflake_integer(self):
        assert DataType.from_snowflake("INTEGER") == DataType.INTEGER
        assert DataType.from_snowflake("INT") == DataType.INTEGER
        assert DataType.from_snowflake("BIGINT") == DataType.INTEGER
    
    def test_from_snowflake_float(self):
        assert DataType.from_snowflake("FLOAT") == DataType.FLOAT
        assert DataType.from_snowflake("DOUBLE") == DataType.FLOAT
    
    def test_from_snowflake_date(self):
        assert DataType.from_snowflake("DATE") == DataType.DATE
        assert DataType.from_snowflake("TIMESTAMP") == DataType.DATETIME
        assert DataType.from_snowflake("TIMESTAMP_NTZ") == DataType.DATETIME
    
    def test_from_snowflake_boolean(self):
        assert DataType.from_snowflake("BOOLEAN") == DataType.BOOLEAN
    
    def test_from_snowflake_unknown(self):
        assert DataType.from_snowflake("UNKNOWN_TYPE") == DataType.UNKNOWN
    
    def test_to_powerbi(self):
        # TMSL requires lowercase types (string, int64, double, etc.)
        assert DataType.STRING.to_powerbi() == "string"
        assert DataType.INTEGER.to_powerbi() == "int64"
        assert DataType.DECIMAL.to_powerbi() == "decimal"
        assert DataType.FLOAT.to_powerbi() == "double"
        assert DataType.BOOLEAN.to_powerbi() == "boolean"


class TestSMLColumn:
    """Tests for SMLColumn model."""
    
    def test_basic_column(self):
        col = SMLColumn(
            unique_name="customer_id",
            data_type=DataType.INTEGER,
            is_key=True,
        )
        assert col.unique_name == "customer_id"
        assert col.label == "customer_id"  # Auto-set from unique_name
        assert col.is_key is True
    
    def test_column_with_label(self):
        col = SMLColumn(
            unique_name="cust_id",
            label="Customer ID",
            data_type=DataType.INTEGER,
        )
        assert col.label == "Customer ID"


class TestSMLDataset:
    """Tests for SMLDataset model."""
    
    def test_basic_dataset(self):
        ds = SMLDataset(
            unique_name="customers",
            columns=[
                SMLColumn(unique_name="id", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="name", data_type=DataType.STRING),
            ],
        )
        assert ds.unique_name == "customers"
        assert len(ds.columns) == 2
        assert ds.get_column("id") is not None
        assert ds.get_column("id").is_key is True
    
    def test_get_numeric_columns(self):
        ds = SMLDataset(
            unique_name="sales",
            columns=[
                SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="name", data_type=DataType.STRING),
                SMLColumn(unique_name="quantity", data_type=DataType.INTEGER),
            ],
        )
        numeric = ds.get_numeric_columns()
        assert len(numeric) == 3
        assert all(c.data_type in {DataType.INTEGER, DataType.DECIMAL, DataType.FLOAT} for c in numeric)


class TestSMLMetric:
    """Tests for SMLMetric model."""
    
    def test_auto_generated_expression(self):
        metric = SMLMetric(
            unique_name="total_sales",
            dataset="sales",
            source_column="amount",
            aggregation=AggregationType.SUM,
        )
        assert metric.expression == "SUM([amount])"
    
    def test_avg_expression(self):
        metric = SMLMetric(
            unique_name="avg_price",
            dataset="products",
            source_column="price",
            aggregation=AggregationType.AVG,
        )
        assert metric.expression == "AVERAGE([price])"
    
    def test_custom_expression_preserved(self):
        metric = SMLMetric(
            unique_name="profit_margin",
            dataset="sales",
            expression="DIVIDE([Total Revenue], [Total Cost])",
        )
        assert metric.expression == "DIVIDE([Total Revenue], [Total Cost])"


class TestSMLRelationship:
    """Tests for SMLRelationship model."""
    
    def test_basic_relationship(self):
        rel = SMLRelationship(
            unique_name="sales_to_customer",
            from_dataset="sales",
            from_columns=["customer_id"],
            to_dataset="customers",
            to_columns=["id"],
            cardinality=Cardinality.MANY_TO_ONE,
        )
        assert rel.from_column == "customer_id"
        assert rel.to_column == "id"


class TestSMLModel:
    """Tests for SMLModel model."""
    
    def test_complete_model(self):
        model = SMLModel(
            unique_name="sales_model",
            description="Test sales model",
            datasets=[
                SMLDataset(
                    unique_name="sales",
                    columns=[
                        SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                    ],
                    is_fact=True,
                ),
                SMLDataset(
                    unique_name="customers",
                    columns=[
                        SMLColumn(unique_name="id", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="name", data_type=DataType.STRING),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="total_sales",
                    dataset="sales",
                    source_column="amount",
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="sales_customer",
                    from_dataset="sales",
                    from_columns=["customer_id"],
                    to_dataset="customers",
                    to_columns=["id"],
                ),
            ],
        )
        
        assert model.dataset_count == 2
        assert model.metric_count == 1
        assert model.relationship_count == 1
        assert model.total_columns == 4
        
        summary = model.get_summary()
        assert summary["datasets"] == 2
        assert summary["metrics"] == 1
