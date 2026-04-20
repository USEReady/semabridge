"""Test suite for DimensionInjector service.

Tests recursive dimension discovery from fact tables with cycle-safe BFS traversal.
Covers simple hierarchies, multi-hop paths, cycles, determinism, and error cases.
"""

import pytest

from semabridge.sml.models import Cardinality, SMLDataset, SMLModel, SMLRelationship
from semabridge.utils.semantic_graph import SemanticGraph
from semabridge.utils.dimension_injector import DimensionInjector, DimensionInjectorError


class TestDimensionInjectorBasics:
    """Test simple dimension discovery scenarios."""

    def test_simple_fact_to_single_dimension(self):
        """Test discovering a single dimension directly connected to fact."""
        # Graph: Orders (fact) --MANY_TO_ONE--> Customers (dimension)
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        dimensions = injector.get_dimensions_for_fact("Orders", graph)

        assert dimensions == ["Customers"]

    def test_fact_with_no_dimensions(self):
        """Test fact table that has no outgoing relationships."""
        datasets = [SMLDataset(unique_name="Sales")]
        relationships = []
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        dimensions = injector.get_dimensions_for_fact("Sales", graph)

        assert dimensions == []

    def test_fact_not_in_graph_raises_exception(self):
        """Test that missing fact table raises DimensionInjectorError."""
        datasets = [SMLDataset(unique_name="Orders")]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=[],
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        with pytest.raises(DimensionInjectorError, match="not found"):
            injector.get_dimensions_for_fact("NonExistent", graph)

    def test_fact_case_insensitive_lookup(self):
        """Test that fact lookup is case-insensitive (matches SemanticGraph behavior)."""
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        # Query with lowercase should be canonicalized correctly by SemanticGraph
        # The canonical name is stored as registered (Orders), but lookup is case-insensitive
        dimensions = injector.get_dimensions_for_fact("orders", graph)

        assert "Customers" in dimensions


class TestDimensionInjectorHierarchies:
    """Test multi-hop dimension paths."""

    def test_multi_hop_fact_to_distant_dimension(self):
        """Test discovering dimensions multiple hops away.
        
        Orders (fact) --MANY_TO_ONE--> Customers --MANY_TO_ONE--> Countries (dimension)
        """
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
            SMLDataset(unique_name="Countries"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Customers_to_Countries",
                from_dataset="Customers",
                to_dataset="Countries",
                from_columns=["country_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        dimensions = injector.get_dimensions_for_fact("Orders", graph)

        # Both direct and transitive dimension should be included
        assert "Customers" in dimensions
        assert "Countries" in dimensions

    def test_multiple_direct_dimensions(self):
        """Test fact with multiple direct dimension connections.
        
        Orders (fact) --MANY_TO_ONE--> Customers
                    --MANY_TO_ONE--> Products
                    --MANY_TO_ONE--> Dates
        """
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
            SMLDataset(unique_name="Products"),
            SMLDataset(unique_name="Dates"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Products",
                from_dataset="Orders",
                to_dataset="Products",
                from_columns=["product_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Dates",
                from_dataset="Orders",
                to_dataset="Dates",
                from_columns=["date_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        dimensions = injector.get_dimensions_for_fact("Orders", graph)

        assert set(dimensions) == {"Customers", "Products", "Dates"}


class TestDimensionInjectorCycles:
    """Test cycle detection and handling."""

    def test_diamond_pattern_visits_all_tables(self):
        """Test that diamond pattern visits all reachable tables.
        
        Orders (fact) --MANY_TO_ONE--> Customers
                    --MANY_TO_ONE--> Products
        Customers --MANY_TO_ONE--> Region
        Products --MANY_TO_ONE--> Region (converges at Region)
        
        DimensionInjector should visit all paths and include Region once.
        """
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
            SMLDataset(unique_name="Products"),
            SMLDataset(unique_name="Region"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Products",
                from_dataset="Orders",
                to_dataset="Products",
                from_columns=["product_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Customers_to_Region",
                from_dataset="Customers",
                to_dataset="Region",
                from_columns=["region_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Products_to_Region",
                from_dataset="Products",
                to_dataset="Region",
                from_columns=["region_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        # Should complete without hanging
        dimensions = injector.get_dimensions_for_fact("Orders", graph)

        # Should include all reachable dimensions
        assert set(dimensions) == {"Customers", "Products", "Region"}

    def test_max_depth_guard(self):
        """Test that max depth prevents traversing too-deep hierarchies.
        
        Creates a chain: Fact -> Dim1 -> Dim2 -> ... -> Dim11
        With max_depth=10, should stop at Dim10.
        """
        # Create a deep chain
        chain_length = 12
        datasets = [SMLDataset(unique_name=f"Table{i}") for i in range(chain_length)]
        relationships = []
        for i in range(chain_length - 1):
            relationships.append(
                SMLRelationship(
                    unique_name=f"Rel_{i}",
                    from_dataset=f"Table{i}",
                    to_dataset=f"Table{i+1}",
                    from_columns=["id"],
                    to_columns=["id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                    is_active=True,
                ),
            )
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector(max_depth=5)

        dimensions = injector.get_dimensions_for_fact("Table0", graph)

        # Should stop before reaching the deepest tables
        # With BFS from Table0 and depth 5, we'd reach Table1..Table5 but not beyond
        assert len(dimensions) <= 5
        assert "Table1" in dimensions


class TestDimensionInjectorDeterminism:
    """Test that results are deterministic."""

    def test_same_fact_and_graph_yields_same_order_twice(self):
        """Test that calling get_dimensions_for_fact twice returns identical ordered results."""
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Products"),
            SMLDataset(unique_name="Customers"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Products",
                from_dataset="Orders",
                to_dataset="Products",
                from_columns=["product_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        result1 = injector.get_dimensions_for_fact("Orders", graph)
        result2 = injector.get_dimensions_for_fact("Orders", graph)

        assert result1 == result2
        # Verify it's sorted order
        assert result1 == sorted(result1)

    def test_deterministic_order_alphabetical(self):
        """Test that dimensions are returned in deterministic alphabetical order."""
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Zebras"),
            SMLDataset(unique_name="Apples"),
            SMLDataset(unique_name="Bananas"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Zebras",
                from_dataset="Orders",
                to_dataset="Zebras",
                from_columns=["id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Apples",
                from_dataset="Orders",
                to_dataset="Apples",
                from_columns=["id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Bananas",
                from_dataset="Orders",
                to_dataset="Bananas",
                from_columns=["id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        dimensions = injector.get_dimensions_for_fact("Orders", graph)

        # Should be alphabetical by table name
        assert dimensions == ["Apples", "Bananas", "Zebras"]


class TestDimensionInjectorTraversal:
    """Test traversal trace metadata."""

    def test_get_traversal_trace_simple(self):
        """Test traversal trace includes path and depth information."""
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        injector.get_dimensions_for_fact("Orders", graph)
        trace = injector.get_traversal_trace("Orders")

        assert trace is not None
        assert "start_table" in trace
        assert trace["start_table"] == "Orders"
        assert "visited_tables" in trace
        assert "Customers" in trace["visited_tables"]
        assert "max_depth_reached" in trace

    def test_traversal_trace_tracks_multiple_paths(self):
        """Test that traversal trace records all visited tables in multi-path graph."""
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
            SMLDataset(unique_name="Products"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Products",
                from_dataset="Orders",
                to_dataset="Products",
                from_columns=["product_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        injector.get_dimensions_for_fact("Orders", graph)
        trace = injector.get_traversal_trace("Orders")

        assert trace is not None
        assert "visited_tables" in trace
        # Should have visited both dimensions
        assert set(trace["visited_tables"]) == {"Customers", "Products"}


class TestDimensionInjectorInactive:
    """Test handling of inactive relationships."""

    def test_inactive_relationships_ignored(self):
        """Test that inactive relationships are not traversed."""
        datasets = [
            SMLDataset(unique_name="Orders"),
            SMLDataset(unique_name="Customers"),
            SMLDataset(unique_name="Archived"),
        ]
        relationships = [
            SMLRelationship(
                unique_name="Orders_to_Customers",
                from_dataset="Orders",
                to_dataset="Customers",
                from_columns=["cust_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="Orders_to_Archived",
                from_dataset="Orders",
                to_dataset="Archived",
                from_columns=["archived_id"],
                to_columns=["id"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=False,  # Inactive
            ),
        ]
        model = SMLModel(
            unique_name="test",
            datasets=datasets,
            relationships=relationships,
        )
        graph = SemanticGraph(model)
        injector = DimensionInjector()

        dimensions = injector.get_dimensions_for_fact("Orders", graph)

        # Should include Customers but not Archived
        assert "Customers" in dimensions
        assert "Archived" not in dimensions
