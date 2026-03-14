"""
Tests for SML serializer.
"""

import tempfile
from pathlib import Path

import pytest
import yaml

from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    DataType,
    AggregationType,
    Cardinality,
)
from semabridge.sml.serializer import SMLSerializer


@pytest.fixture
def sample_model():
    """Create a sample SML model for testing."""
    return SMLModel(
        unique_name="test_model",
        label="Test Model",
        description="A test model",
        datasets=[
            SMLDataset(
                unique_name="customers",
                label="Customers",
                columns=[
                    SMLColumn(unique_name="id", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="name", data_type=DataType.STRING),
                ],
            ),
            SMLDataset(
                unique_name="orders",
                label="Orders",
                columns=[
                    SMLColumn(unique_name="id", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="customer_id", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="amount", data_type=DataType.DECIMAL),
                ],
                is_fact=True,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="total_amount",
                label="Total Amount",
                dataset="orders",
                source_column="amount",
                aggregation=AggregationType.SUM,
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="orders_customers",
                from_dataset="orders",
                from_columns=["customer_id"],
                to_dataset="customers",
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
    )


class TestSMLSerializer:
    """Tests for SML serializer."""
    
    def test_to_yaml(self, sample_model):
        yaml_str = SMLSerializer.to_yaml(sample_model)
        assert "unique_name: test_model" in yaml_str
        assert "customers" in yaml_str
        assert "orders" in yaml_str
    
    def test_from_yaml(self, sample_model):
        yaml_str = SMLSerializer.to_yaml(sample_model)
        loaded = SMLSerializer.from_yaml(yaml_str)
        
        assert loaded.unique_name == sample_model.unique_name
        assert loaded.dataset_count == sample_model.dataset_count
        assert loaded.metric_count == sample_model.metric_count
    
    def test_save_and_load_single_file(self, sample_model):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "model.yaml"
            
            # Save
            SMLSerializer.save(sample_model, path)
            assert path.exists()
            
            # Load
            loaded = SMLSerializer.load(path)
            assert loaded.unique_name == sample_model.unique_name
            assert loaded.dataset_count == 2
    
    def test_save_and_load_folder(self, sample_model):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sml_output"
            
            # Save as folder
            SMLSerializer.save(sample_model, path)
            
            # Check folder structure
            assert (path / "model.yaml").exists()
            assert (path / "datasets").exists()
            assert (path / "metrics").exists()
            
            # Load from folder
            loaded = SMLSerializer.load(path)
            assert loaded.unique_name == sample_model.unique_name
            assert loaded.dataset_count == 2


class TestYAMLOutput:
    """Tests for YAML output format."""
    
    def test_yaml_is_valid(self, sample_model):
        yaml_str = SMLSerializer.to_yaml(sample_model)
        # Should not raise
        data = yaml.safe_load(yaml_str)
        assert data is not None
        assert data["unique_name"] == "test_model"
    
    def test_yaml_contains_all_components(self, sample_model):
        yaml_str = SMLSerializer.to_yaml(sample_model)
        data = yaml.safe_load(yaml_str)
        
        assert "datasets" in data
        assert "metrics" in data
        assert "relationships" in data
        assert len(data["datasets"]) == 2
        assert len(data["metrics"]) == 1
        assert len(data["relationships"]) == 1
