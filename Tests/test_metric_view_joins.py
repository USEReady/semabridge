"""
Tests for multi-table metric view joins functionality.

Tests the full stack from join tree building through YAML generation.
"""

import pytest
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, Cardinality, SMLJoin,
)
from semabridge.utils.join_builder import JoinTreeBuilder
from semabridge.core.behavior import DatabricksBehavior, ConnectorBehavior


class TestSMLJoin:
    """Test SMLJoin model."""
    
    def test_join_creation(self):
        """Test basic SMLJoin creation."""
        join = SMLJoin(
            name="customer",
            source="db.schema.customer",
            on="orders.cust_id = customer.id"
        )
        assert join.name == "customer"
        assert join.source == "db.schema.customer"
        assert join.on == "orders.cust_id = customer.id"
        assert join.joins == []
    
    def test_nested_joins(self):
        """Test nested SMLJoin for snowflake schemas."""
        nation_join = SMLJoin(
            name="nation",
            source="db.schema.nation",
            on="customer.nation_id = nation.id"
        )
        customer_join = SMLJoin(
            name="customer",
            source="db.schema.customer",
            on="orders.cust_id = customer.id",
            joins=[nation_join]
        )
        assert len(customer_join.joins) == 1
        assert customer_join.joins[0].name == "nation"
    
    def test_join_validation(self):
        """Test that empty join condition raises error."""
        with pytest.raises(ValueError):
            SMLJoin(
                name="customer",
                source="db.schema.customer",
                on=""  # Empty condition should fail
            )


class TestJoinTreeBuilder:
    """Test JoinTreeBuilder functionality."""
    
    @pytest.fixture
    def simple_model(self):
        """Create a simple TPC-H-like model."""
        model = SMLModel(
            unique_name="tpch_model",
            datasets=[
                SMLDataset(
                    unique_name="orders",
                    source_table="orders",
                    source_schema="public",
                    columns=[
                        SMLColumn(unique_name="o_orderkey", data_type=DataType.INTEGER),
                        SMLColumn(unique_name="o_custkey", data_type=DataType.INTEGER),
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
        return model
    
    def test_builder_init(self, simple_model):
        """Test JoinTreeBuilder initialization."""
        builder = JoinTreeBuilder(simple_model)
        assert builder.model is simple_model
    
    def test_build_direct_join(self, simple_model):
        """Test building a single direct join."""
        builder = JoinTreeBuilder(simple_model)
        joins = builder.build_join_tree("orders")
        
        assert len(joins) == 1
        assert joins[0].name == "customer"
        assert "orders" not in joins[0].on.lower() or "o_custkey" in joins[0].on
    
    def test_build_nested_joins(self, simple_model):
        """Test building nested joins for snowflake schema."""
        builder = JoinTreeBuilder(simple_model)
        joins = builder.build_join_tree("orders")
        
        # Should have customer join with nested nation join
        assert len(joins) == 1
        assert joins[0].name == "customer"
        assert len(joins[0].joins) == 1
        assert joins[0].joins[0].name == "nation"
    
    def test_invalid_dataset_raises_error(self, simple_model):
        """Test that invalid dataset raises ValueError."""
        builder = JoinTreeBuilder(simple_model)
        with pytest.raises(ValueError):
            builder.build_join_tree("nonexistent")
    
    def test_circular_relationship_detection(self):
        """Test that circular relationships are handled gracefully."""
        # Create a model with circular relationship
        model = SMLModel(
            unique_name="circular_model",
            datasets=[
                SMLDataset(unique_name="table_a"),
                SMLDataset(unique_name="table_b"),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="a_to_b",
                    from_dataset="table_a",
                    from_columns=["id"],
                    to_dataset="table_b",
                    to_columns=["id"],
                ),
                SMLRelationship(
                    unique_name="b_to_a",
                    from_dataset="table_b",
                    from_columns=["id"],
                    to_dataset="table_a",
                    to_columns=["id"],
                ),
            ]
        )
        builder = JoinTreeBuilder(model)
        joins = builder.build_join_tree("table_a")
        
        # Should only include table_b, not a circular reference back to table_a
        assert len(joins) == 1
        assert joins[0].name == "table_b"
        assert len(joins[0].joins) == 0  # No circular join back
    
    def test_max_depth_limit(self, simple_model):
        """Test that max_depth parameter is respected."""
        builder = JoinTreeBuilder(simple_model)
        
        # With max_depth=1, should only get direct joins, no nested
        joins = builder.build_join_tree("orders", max_depth=1)
        assert len(joins) == 1
        assert len(joins[0].joins) == 0  # No nested joins

    def test_build_join_tree_quotes_columns_with_spaces(self):
        """Join conditions should quote identifiers that contain spaces."""
        model = SMLModel(
            unique_name="spaced_columns",
            datasets=[
                SMLDataset(unique_name="Fact", source_table="fact"),
                SMLDataset(unique_name="BU", source_table="bu"),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="fact_to_bu",
                    from_dataset="Fact",
                    from_columns=["BU Key"],
                    to_dataset="BU",
                    to_columns=["BU Key"],
                    cardinality=Cardinality.MANY_TO_ONE,
                )
            ],
        )
        builder = JoinTreeBuilder(model)

        joins = builder.build_join_tree("Fact")

        assert len(joins) == 1
        assert joins[0].on == ""
        assert joins[0].using == ["BU Key"]


class TestDatabricksBehaviorFlag:
    """Test the enable_metric_view_joins behavior flag."""
    
    def test_flag_defaults_to_false(self):
        """Test that the flag defaults to False."""
        behavior = DatabricksBehavior()
        assert behavior.enable_metric_view_joins is False
    
    def test_flag_respects_configuration(self):
        """Test that the flag can be set via configuration."""
        behavior = DatabricksBehavior(enable_metric_view_joins=True)
        assert behavior.enable_metric_view_joins is True
    
    def test_connector_behavior_includes_flag(self):
        """Test that ConnectorBehavior loads the flag."""
        behavior = ConnectorBehavior(
            databricks=DatabricksBehavior(enable_metric_view_joins=True)
        )
        assert behavior.databricks.enable_metric_view_joins is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
