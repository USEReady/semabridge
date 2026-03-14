"""
Unit tests for semabridge.intermediate.models.

Tests the OSI (Open Semantic Interchange) Pydantic models.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from semabridge.intermediate import (
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
    OSIDataType,
    OSIAttribute,
    OSIColumn,
    OSIDataset,
    OSIDimension,
    OSIHierarchy,
    OSILevel,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)


class TestOSIEnums:
    """Test OSI enumeration types."""

    def test_aggregation_types(self):
        """Test aggregation type enum values."""
        assert OSIAggregationType.SUM.value == "sum"
        assert OSIAggregationType.COUNT.value == "count"
        assert OSIAggregationType.AVG.value == "avg"

    def test_data_types(self):
        """Test data type enum values."""
        assert OSIDataType.STRING.value == "string"
        assert OSIDataType.INTEGER.value == "integer"
        assert OSIDataType.DECIMAL.value == "decimal"
        assert OSIDataType.DATE.value == "date"

    def test_cardinality_types(self):
        """Test cardinality enum values."""
        assert OSICardinality.ONE_TO_MANY.value == "one-to-many"
        assert OSICardinality.MANY_TO_ONE.value == "many-to-one"


class TestOSIColumn:
    """Test OSIColumn model."""

    def test_minimal_column(self):
        """Test column with minimal required fields."""
        col = OSIColumn(unique_name="REVENUE")
        assert col.unique_name == "REVENUE"
        assert col.label == "REVENUE"  # Auto-set from unique_name
        assert col.data_type == OSIDataType.STRING

    def test_full_column(self):
        """Test column with all fields."""
        col = OSIColumn(
            unique_name="TOTAL_SALES",
            label="Total Sales",
            data_type=OSIDataType.DECIMAL,
            description="Sum of all sales",
            is_key=False,
            is_hidden=True,
            format_string="$#,##0.00"
        )
        assert col.label == "Total Sales"
        assert col.is_hidden is True

    def test_empty_name_validation(self):
        """Test that empty unique_name is rejected."""
        with pytest.raises(PydanticValidationError):
            OSIColumn(unique_name="")

    def test_whitespace_name_validation(self):
        """Test that whitespace-only name is rejected."""
        with pytest.raises(PydanticValidationError):
            OSIColumn(unique_name="   ")


class TestOSIDataset:
    """Test OSIDataset model."""

    def test_minimal_dataset(self):
        """Test dataset with minimal fields."""
        ds = OSIDataset(unique_name="FACT_SALES")
        assert ds.unique_name == "FACT_SALES"
        assert ds.label == "FACT_SALES"
        assert ds.source_table == "FACT_SALES"
        assert ds.columns == []

    def test_dataset_with_columns(self):
        """Test dataset with columns."""
        ds = OSIDataset(
            unique_name="DIM_CUSTOMER",
            label="Customer Dimension",
            is_fact=False,
            columns=[
                OSIColumn(unique_name="ID", data_type=OSIDataType.INTEGER, is_key=True),
                OSIColumn(unique_name="NAME", data_type=OSIDataType.STRING),
            ]
        )
        assert len(ds.columns) == 2
        assert ds.columns[0].is_key is True

    def test_get_column(self):
        """Test get_column helper method."""
        ds = OSIDataset(
            unique_name="TEST",
            columns=[
                OSIColumn(unique_name="COL1"),
                OSIColumn(unique_name="COL2"),
            ]
        )
        assert ds.get_column("COL1") is not None
        assert ds.get_column("COL1").unique_name == "COL1"
        assert ds.get_column("NONEXISTENT") is None

    def test_get_key_columns(self):
        """Test get_key_columns helper method."""
        ds = OSIDataset(
            unique_name="TEST",
            columns=[
                OSIColumn(unique_name="ID", is_key=True),
                OSIColumn(unique_name="FK_ID", is_key=True),
                OSIColumn(unique_name="NAME"),
            ]
        )
        keys = ds.get_key_columns()
        assert len(keys) == 2
        assert all(k.is_key for k in keys)

    def test_get_numeric_columns(self):
        """Test get_numeric_columns helper method."""
        ds = OSIDataset(
            unique_name="FACT_SALES",
            columns=[
                OSIColumn(unique_name="REVENUE", data_type=OSIDataType.DECIMAL),
                OSIColumn(unique_name="QUANTITY", data_type=OSIDataType.INTEGER),
                OSIColumn(unique_name="NAME", data_type=OSIDataType.STRING),
            ]
        )
        numeric = ds.get_numeric_columns()
        assert len(numeric) == 2


class TestOSIMetric:
    """Test OSIMetric model."""

    def test_metric_with_source_column(self):
        """Test metric with source column aggregation."""
        metric = OSIMetric(
            unique_name="Total Revenue",
            dataset="FACT_SALES",
            source_column="REVENUE",
            aggregation=OSIAggregationType.SUM
        )
        assert metric.aggregation == OSIAggregationType.SUM
        assert metric.source_column == "REVENUE"

    def test_metric_with_expression(self):
        """Test metric with custom expression."""
        metric = OSIMetric(
            unique_name="Profit Margin",
            dataset="FACT_SALES",
            expression="SUM(REVENUE - COST) / SUM(REVENUE)"
        )
        assert metric.expression is not None
        assert metric.source_column is None

    def test_metric_requires_source_or_expression(self):
        """Test that metric must have source_column or expression."""
        with pytest.raises(PydanticValidationError):
            OSIMetric(
                unique_name="Invalid Metric",
                dataset="FACT_SALES"
                # Neither source_column nor expression provided
            )

    def test_metric_with_business_owner(self):
        """Test OSI business_owner attribute."""
        metric = OSIMetric(
            unique_name="Revenue",
            dataset="SALES",
            source_column="AMOUNT",
            business_owner="finance-team@company.com"
        )
        assert metric.business_owner == "finance-team@company.com"


class TestOSIRelationship:
    """Test OSIRelationship model."""

    def test_basic_relationship(self):
        """Test basic relationship definition."""
        rel = OSIRelationship(
            unique_name="Sales_to_Customer",
            from_dataset="FACT_SALES",
            from_columns=["CUSTOMER_ID"],
            to_dataset="DIM_CUSTOMER",
            to_columns=["ID"]
        )
        assert rel.cardinality == OSICardinality.MANY_TO_ONE
        assert rel.is_active is True

    def test_column_count_mismatch_validation(self):
        """Test that column counts must match."""
        with pytest.raises(PydanticValidationError):
            OSIRelationship(
                unique_name="Invalid",
                from_dataset="A",
                from_columns=["COL1", "COL2"],
                to_dataset="B",
                to_columns=["COL1"]  # Only one column
            )

    def test_composite_key_relationship(self):
        """Test relationship with composite keys."""
        rel = OSIRelationship(
            unique_name="Composite_Join",
            from_dataset="FACT",
            from_columns=["YEAR", "MONTH"],
            to_dataset="DIM_TIME",
            to_columns=["YEAR", "MONTH"]
        )
        assert len(rel.from_columns) == 2


class TestOSIDimension:
    """Test OSIDimension model."""

    def test_dimension_with_hierarchy(self):
        """Test dimension with hierarchy."""
        dim = OSIDimension(
            unique_name="Geography",
            dataset="DIM_GEOGRAPHY",
            hierarchies=[
                OSIHierarchy(
                    unique_name="Geo_Hierarchy",
                    levels=[
                        OSILevel(unique_name="Country", attribute="COUNTRY"),
                        OSILevel(unique_name="State", attribute="STATE"),
                        OSILevel(unique_name="City", attribute="CITY"),
                    ]
                )
            ]
        )
        assert len(dim.hierarchies) == 1
        assert len(dim.hierarchies[0].levels) == 3


class TestOSIModel:
    """Test OSIModel top-level container."""

    def test_minimal_model(self):
        """Test model with minimal fields."""
        model = OSIModel(unique_name="test_model")
        assert model.unique_name == "test_model"
        assert model.label == "test_model"
        assert model.version == "1.0.0"
        assert model.datasets == []

    def test_full_model(self):
        """Test complete model with all components."""
        model = OSIModel(
            unique_name="sales_analytics",
            label="Sales Analytics Model",
            description="Comprehensive sales model",
            datasets=[
                OSIDataset(
                    unique_name="FACT_SALES",
                    is_fact=True,
                    columns=[
                        OSIColumn(unique_name="REVENUE", data_type=OSIDataType.DECIMAL),
                        OSIColumn(unique_name="CUSTOMER_ID", data_type=OSIDataType.INTEGER),
                    ]
                ),
                OSIDataset(
                    unique_name="DIM_CUSTOMER",
                    columns=[
                        OSIColumn(unique_name="ID", data_type=OSIDataType.INTEGER, is_key=True),
                        OSIColumn(unique_name="NAME", data_type=OSIDataType.STRING),
                    ]
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="Total Revenue",
                    dataset="FACT_SALES",
                    source_column="REVENUE"
                )
            ],
            relationships=[
                OSIRelationship(
                    unique_name="Sales_Customer",
                    from_dataset="FACT_SALES",
                    from_columns=["CUSTOMER_ID"],
                    to_dataset="DIM_CUSTOMER",
                    to_columns=["ID"]
                )
            ],
            source_platform="fabric"
        )
        assert len(model.datasets) == 2
        assert len(model.metrics) == 1
        assert len(model.relationships) == 1
        assert model.source_platform == "fabric"

    def test_get_helpers(self):
        """Test model getter helper methods."""
        model = OSIModel(
            unique_name="test",
            datasets=[OSIDataset(unique_name="DS1")],
            metrics=[OSIMetric(unique_name="M1", dataset="DS1", source_column="X")],
        )
        assert model.get_dataset("DS1") is not None
        assert model.get_dataset("NONEXISTENT") is None
        assert model.get_metric("M1") is not None

    def test_validate_integrity_valid_model(self):
        """Test integrity validation on valid model."""
        model = OSIModel(
            unique_name="test",
            datasets=[OSIDataset(unique_name="SALES")],
            metrics=[OSIMetric(unique_name="Rev", dataset="SALES", source_column="AMT")],
        )
        errors = model.validate_integrity()
        assert errors == []

    def test_validate_integrity_invalid_metric_reference(self):
        """Test integrity validation catches invalid references."""
        model = OSIModel(
            unique_name="test",
            datasets=[OSIDataset(unique_name="SALES")],
            metrics=[
                OSIMetric(unique_name="Rev", dataset="NONEXISTENT_TABLE", source_column="AMT")
            ],
        )
        errors = model.validate_integrity()
        assert len(errors) == 1
        assert "NONEXISTENT_TABLE" in errors[0]

    def test_validate_integrity_invalid_relationship(self):
        """Test integrity validation catches invalid relationship."""
        model = OSIModel(
            unique_name="test",
            datasets=[OSIDataset(unique_name="SALES")],
            relationships=[
                OSIRelationship(
                    unique_name="bad_rel",
                    from_dataset="SALES",
                    from_columns=["X"],
                    to_dataset="MISSING_DIM",
                    to_columns=["Y"]
                )
            ],
        )
        errors = model.validate_integrity()
        assert len(errors) == 1
        assert "MISSING_DIM" in errors[0]

    def test_find_relationships_for_dataset(self):
        """Test finding relationships for a dataset."""
        model = OSIModel(
            unique_name="test",
            datasets=[
                OSIDataset(unique_name="FACT"),
                OSIDataset(unique_name="DIM1"),
                OSIDataset(unique_name="DIM2"),
            ],
            relationships=[
                OSIRelationship(
                    unique_name="r1",
                    from_dataset="FACT",
                    from_columns=["A"],
                    to_dataset="DIM1",
                    to_columns=["A"]
                ),
                OSIRelationship(
                    unique_name="r2",
                    from_dataset="FACT",
                    from_columns=["B"],
                    to_dataset="DIM2",
                    to_columns=["B"]
                ),
            ],
        )
        rels = model.find_relationships_for_dataset("FACT")
        assert len(rels) == 2

        rels_dim1 = model.find_relationships_for_dataset("DIM1")
        assert len(rels_dim1) == 1

    def test_model_has_created_at_timestamp(self):
        """Test model auto-generates created_at timestamp."""
        model = OSIModel(unique_name="test")
        assert model.created_at is not None
        assert isinstance(model.created_at, datetime)


class TestOSIStrictMode:
    """Test Pydantic strict mode enforcement."""

    def test_extra_fields_forbidden(self):
        """Test that extra fields are rejected."""
        with pytest.raises(PydanticValidationError):
            OSIColumn(unique_name="TEST", unknown_field="value")  # type: ignore
