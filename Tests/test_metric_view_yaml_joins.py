"""
Tests for metric view YAML generation with joins.

Tests the integration of join tree building with Databricks metric view YAML.
"""

import pytest
import yaml as pyyaml
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, Cardinality, AggregationType,
)
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.settings import DatabricksConfig


class TestMetricViewYAMLWithJoins:
    """Test metric view YAML generation with joins."""
    
    @pytest.fixture
    def tpch_model(self):
        """Create a TPC-H-like model for testing."""
        return SMLModel(
            unique_name="tpch_model",
            datasets=[
                SMLDataset(
                    unique_name="orders",
                    source_table="orders",
                    source_schema="public",
                    columns=[
                        SMLColumn(unique_name="o_orderkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="o_custkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="o_totalprice", data_type=DataType.DECIMAL),
                    ]
                ),
                SMLDataset(
                    unique_name="customer",
                    source_table="customer",
                    source_schema="public",
                    columns=[
                        SMLColumn(unique_name="c_custkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="c_nationkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="c_name", data_type=DataType.STRING),
                    ]
                ),
                SMLDataset(
                    unique_name="nation",
                    source_table="nation",
                    source_schema="public",
                    columns=[
                        SMLColumn(unique_name="n_nationkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="n_name", data_type=DataType.STRING),
                    ]
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="order_count",
                    dataset="orders",
                    expression="COUNT(DISTINCT o_orderkey)",
                    aggregation=AggregationType.COUNT,
                ),
                SMLMetric(
                    unique_name="total_revenue",
                    dataset="orders",
                    expression="SUM(o_totalprice)",
                    aggregation=AggregationType.SUM,
                    source_column="o_totalprice",
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="orders_to_customer",
                    from_dataset="orders",
                    from_columns=["o_custkey"],
                    to_dataset="customer",
                    to_columns=["c_custkey"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="customer_to_nation",
                    from_dataset="customer",
                    from_columns=["c_nationkey"],
                    to_dataset="nation",
                    to_columns=["n_nationkey"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
            ]
        )
    
    @pytest.fixture
    def publisher_with_joins_enabled(self):
        """Create a DatabricksPublisher with joins enabled."""
        config = DatabricksConfig(
            host="https://test.cloud.databricks.com",
            token="test_token",
            warehouse_id="test_warehouse",
        )
        behavior_config = {
            "databricks": {
                "enable_metric_view_joins": True,
                "enable_cross_table_joins": True,
                "measure_view_type": "metric_view",
            }
        }
        behavior = ConnectorBehavior(**behavior_config)
        return DatabricksPublisher(config, behavior)
    
    @pytest.fixture
    def publisher_with_joins_disabled(self):
        """Create a DatabricksPublisher with joins disabled."""
        config = DatabricksConfig(
            host="https://test.cloud.databricks.com",
            token="test_token",
            warehouse_id="test_warehouse",
        )
        behavior_config = {
            "databricks": {
                "enable_metric_view_joins": False,
                "enable_cross_table_joins": True,
                "measure_view_type": "metric_view",
            }
        }
        behavior = ConnectorBehavior(**behavior_config)
        return DatabricksPublisher(config, behavior)
    
    def test_yaml_without_joins_when_disabled(self, tpch_model, publisher_with_joins_disabled):
        """Test that joins are not emitted when feature flag is disabled."""
        orders = tpch_model.get_dataset("orders")
        
        # Generate YAML for orders dataset
        yaml_text = publisher_with_joins_disabled._generate_metric_view_yaml(
            tpch_model,
            orders,
            "public.orders",
            [],  # No resolved measures for simplicity
            [],  # No bindings
        )
        
        # Should not contain 'joins:' when disabled
        assert "joins:" not in yaml_text
    
    def test_yaml_includes_joins_when_enabled(self, tpch_model, publisher_with_joins_enabled):
        """Test that joins are emitted when feature flag is enabled."""
        orders = tpch_model.get_dataset("orders")
        
        # Generate YAML for orders dataset
        yaml_text = publisher_with_joins_enabled._generate_metric_view_yaml(
            tpch_model,
            orders,
            "public.orders",
            [],  # No resolved measures for simplicity
            [],  # No bindings
        )
        
        # Should contain 'joins:' when enabled
        assert "joins:" in yaml_text
        assert "customer" in yaml_text  # Join name should appear
        assert "nation" in yaml_text    # Nested join should appear
    
    def test_join_yaml_structure(self, tpch_model, publisher_with_joins_enabled):
        """Test that generated join YAML has correct structure."""
        orders = tpch_model.get_dataset("orders")
        
        yaml_text = publisher_with_joins_enabled._generate_metric_view_yaml(
            tpch_model,
            orders,
            "public.orders",
            [],
            [],
        )
        
        # Parse YAML to verify structure
        yaml_obj = pyyaml.safe_load(yaml_text)
        
        assert "joins" in yaml_obj
        assert isinstance(yaml_obj["joins"], list)
        assert len(yaml_obj["joins"]) > 0
        
        # Check first join (customer)
        customer_join = yaml_obj["joins"][0]
        assert customer_join["name"] == "customer"
        assert "source" in customer_join
        assert "on" in customer_join
        
        # Check nested join (nation)
        if "joins" in customer_join:
            assert len(customer_join["joins"]) > 0
            nation_join = customer_join["joins"][0]
            assert nation_join["name"] == "nation"
    
    def test_yaml_is_valid_metric_view(self, tpch_model, publisher_with_joins_enabled):
        """Test that generated YAML is valid metric view YAML."""
        orders = tpch_model.get_dataset("orders")
        
        yaml_text = publisher_with_joins_enabled._generate_metric_view_yaml(
            tpch_model,
            orders,
            "public.orders",
            [],
            [],
        )
        
        # Should parse without errors
        yaml_obj = pyyaml.safe_load(yaml_text)
        
        # Should have required metric view properties
        assert "version" in yaml_obj
        assert yaml_obj["version"] is not None
        assert "source" in yaml_obj or "joins" in yaml_obj


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
