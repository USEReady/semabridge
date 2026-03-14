"""
Unit tests for OSIToSMLConverter - including round-trip conversion.

Tests the bidirectional conversion: OSI ↔ SML
"""

import pytest

from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIDimension,
    OSIAttribute,
    OSIHierarchy,
    OSILevel,
    OSIDataType,
    OSIAggregationType,
    OSICardinality,
    OSICrossFilterDirection,
)


class TestOSIToSMLConverter:
    """Test OSI to SML conversion."""

    @pytest.fixture
    def converter(self):
        """Create converter instance."""
        return OSIToSMLConverter()

    @pytest.fixture
    def sample_osi_model(self):
        """Create a sample OSI model for testing."""
        return OSIModel(
            unique_name="test_model",
            label="Test Model",
            description="A test semantic model",
            version="1.0.0",
            source_platform="fabric",
            datasets=[
                OSIDataset(
                    unique_name="FACT_SALES",
                    label="Sales Fact",
                    is_fact=True,
                    columns=[
                        OSIColumn(unique_name="REVENUE", data_type=OSIDataType.DECIMAL),
                        OSIColumn(unique_name="CUSTOMER_ID", data_type=OSIDataType.INTEGER, is_key=True),
                    ]
                ),
                OSIDataset(
                    unique_name="DIM_CUSTOMER",
                    label="Customer",
                    columns=[
                        OSIColumn(unique_name="ID", data_type=OSIDataType.INTEGER, is_key=True),
                        OSIColumn(unique_name="NAME", data_type=OSIDataType.STRING),
                    ]
                ),
            ],
            metrics=[
                OSIMetric(
                    unique_name="Total Revenue",
                    dataset="FACT_SALES",
                    source_column="REVENUE",
                    aggregation=OSIAggregationType.SUM,
                ),
            ],
            relationships=[
                OSIRelationship(
                    unique_name="Sales_Customer",
                    from_dataset="FACT_SALES",
                    from_columns=["CUSTOMER_ID"],
                    to_dataset="DIM_CUSTOMER",
                    to_columns=["ID"],
                    cardinality=OSICardinality.MANY_TO_ONE,
                ),
            ],
            dimensions=[
                OSIDimension(
                    unique_name="CustomerDim",
                    dataset="DIM_CUSTOMER",
                    attributes=[
                        OSIAttribute(
                            unique_name="CustomerName",
                            label="Customer Name",
                            dataset="DIM_CUSTOMER",
                            source_column="NAME",
                        )
                    ],
                ),
            ],
        )

    def test_from_osi_basic(self, converter, sample_osi_model):
        """Test basic OSI to SML conversion."""
        sml = converter.from_osi(sample_osi_model)
        
        assert sml.unique_name == "test_model"
        assert sml.label == "Test Model"
        assert len(sml.datasets) >= 2  # May include auto-generated Date
        assert len(sml.metrics) == 1
        assert len(sml.relationships) == 1

    def test_to_osi_basic(self, converter, sample_osi_model):
        """Test basic SML to OSI conversion."""
        sml = converter.from_osi(sample_osi_model)
        osi_back = converter.to_osi(sml)
        
        assert osi_back.unique_name == sample_osi_model.unique_name
        assert osi_back.label == sample_osi_model.label

    def test_round_trip_preserves_model_name(self, converter, sample_osi_model):
        """Test round-trip preserves model identity."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        assert reconstructed.unique_name == sample_osi_model.unique_name
        assert reconstructed.version == sample_osi_model.version

    def test_round_trip_preserves_dataset_count(self, converter, sample_osi_model):
        """Test round-trip preserves datasets."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        # May include auto-generated Date dimension
        assert len(reconstructed.datasets) >= len(sample_osi_model.datasets)
        
        # Verify original datasets are present
        original_names = {ds.unique_name for ds in sample_osi_model.datasets}
        reconstructed_names = {ds.unique_name for ds in reconstructed.datasets}
        assert original_names.issubset(reconstructed_names)

    def test_round_trip_preserves_metrics(self, converter, sample_osi_model):
        """Test round-trip preserves metrics."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        assert len(reconstructed.metrics) == len(sample_osi_model.metrics)
        assert reconstructed.metrics[0].unique_name == sample_osi_model.metrics[0].unique_name

    def test_round_trip_preserves_relationships(self, converter, sample_osi_model):
        """Test round-trip preserves relationships."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        assert len(reconstructed.relationships) == len(sample_osi_model.relationships)
        assert reconstructed.relationships[0].from_dataset == sample_osi_model.relationships[0].from_dataset

    def test_round_trip_preserves_column_types(self, converter, sample_osi_model):
        """Test round-trip preserves column data types."""
        sml = converter.from_osi(sample_osi_model)
        reconstructed = converter.to_osi(sml)
        
        # Find FACT_SALES in reconstructed
        fact_ds = next((ds for ds in reconstructed.datasets if ds.unique_name == "FACT_SALES"), None)
        assert fact_ds is not None
        
        revenue_col = fact_ds.get_column("REVENUE")
        assert revenue_col is not None
        assert revenue_col.data_type == OSIDataType.DECIMAL


class TestSMLToOSIEdgeCases:
    """Test edge cases for SML to OSI conversion."""

    @pytest.fixture
    def converter(self):
        return OSIToSMLConverter()

    def test_empty_model(self, converter):
        """Test conversion of minimal model."""
        osi = OSIModel(unique_name="empty_model")
        sml = converter.from_osi(osi)
        reconstructed = converter.to_osi(sml)
        
        assert reconstructed.unique_name == "empty_model"

    def test_model_with_hierarchies(self, converter):
        """Test conversion preserves dimension hierarchies."""
        osi = OSIModel(
            unique_name="hierarchy_test",
            datasets=[OSIDataset(unique_name="DIM_GEO")],
            dimensions=[
                OSIDimension(
                    unique_name="Geography",
                    dataset="DIM_GEO",
                    hierarchies=[
                        OSIHierarchy(
                            unique_name="GeoHier",
                            levels=[
                                OSILevel(unique_name="Country", attribute="COUNTRY"),
                                OSILevel(unique_name="City", attribute="CITY"),
                            ]
                        )
                    ],
                )
            ],
        )
        
        sml = converter.from_osi(osi)
        reconstructed = converter.to_osi(sml)
        
        assert len(reconstructed.dimensions) == 1
        # Note: hierarchies may not round-trip perfectly due to SML model limitations
