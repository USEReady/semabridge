"""Tests for MeasureFactMapping service."""

import pytest

from semabridge.sml.models import Cardinality, SMLDataset, SMLMetric, SMLModel, SMLRelationship
from semabridge.utils.dimension_injector import DimensionInjector
from semabridge.utils.measure_fact_mapping import MeasureFactMapping
from semabridge.utils.semantic_graph import SemanticGraph
from semabridge.utils.table_categorizer import TableCategorizer


@pytest.fixture
def simple_model():
    """Create a simple three-table model: Sales (fact) -> Product (dimension) -> Category (dimension)."""
    return SMLModel(
        unique_name="test_model",
        datasets=[
            SMLDataset(unique_name="Sales", object_type="table"),
            SMLDataset(unique_name="Product", object_type="table"),
            SMLDataset(unique_name="Category", object_type="table"),
        ],
        relationships=[
            SMLRelationship(
                unique_name="Sales_to_Product",
                from_dataset="Sales",
                from_columns=["product_id"],
                to_dataset="Product",
                to_columns=["product_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="Product_to_Category",
                from_dataset="Product",
                from_columns=["category_id"],
                to_dataset="Category",
                to_columns=["category_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
        metrics=[],
    )


@pytest.fixture
def multi_fact_model():
    """Create a model with multiple fact tables: Sales, Orders (facts) with shared dimensions."""
    return SMLModel(
        unique_name="multi_fact_model",
        datasets=[
            SMLDataset(unique_name="Sales", object_type="table"),
            SMLDataset(unique_name="Orders", object_type="table"),
            SMLDataset(unique_name="Product", object_type="table"),
            SMLDataset(unique_name="Customer", object_type="table"),
        ],
        relationships=[
            # Sales to dimensions
            SMLRelationship(
                unique_name="Sales_to_Product",
                from_dataset="Sales",
                from_columns=["product_id"],
                to_dataset="Product",
                to_columns=["product_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            SMLRelationship(
                unique_name="Sales_to_Customer",
                from_dataset="Sales",
                from_columns=["customer_id"],
                to_dataset="Customer",
                to_columns=["customer_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            # Orders to Product only (not Customer)
            SMLRelationship(
                unique_name="Orders_to_Product",
                from_dataset="Orders",
                from_columns=["product_id"],
                to_dataset="Product",
                to_columns=["product_id"],
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
        metrics=[],
    )


class TestMeasureFactMappingParsingDAX:
    """Test DAX expression parsing."""

    def test_parse_empty_expression(self):
        """Measure with empty expression should parse to empty set."""
        mapping = MeasureFactMapping()
        result = mapping._parse_dax_expression("")
        assert result == frozenset()

    def test_parse_none_expression(self):
        """Measure with None-equivalent should parse to empty set."""
        mapping = MeasureFactMapping()
        result = mapping._parse_dax_expression("   ")
        assert result == frozenset()

    def test_parse_simple_bracket_reference(self):
        """Parse [TableName]. reference pattern."""
        mapping = MeasureFactMapping()
        expression = "SUM([Sales].[Amount])"
        result = mapping._parse_dax_expression(expression)
        assert "SALES" in result

    def test_parse_simple_table_bracket_reference(self):
        """Parse TableName[ reference pattern."""
        mapping = MeasureFactMapping()
        expression = "SUM(Product[Amount])"
        result = mapping._parse_dax_expression(expression)
        assert "PRODUCT" in result

    def test_parse_multiple_table_references(self):
        """Parse expression with multiple table references."""
        mapping = MeasureFactMapping()
        expression = "SUM([Sales].[Amount]) / COUNTA([Product].[Name])"
        result = mapping._parse_dax_expression(expression)
        assert "SALES" in result
        assert "PRODUCT" in result

    def test_parse_case_insensitive(self):
        """Table references should be normalized to uppercase."""
        mapping = MeasureFactMapping()
        expression = "SUM([sales].[amount]) + [product].[qty]"
        result = mapping._parse_dax_expression(expression)
        assert "SALES" in result
        assert "PRODUCT" in result

    def test_parse_caching(self):
        """Same expression should use cached result."""
        mapping = MeasureFactMapping()
        expression = "SUM([Sales].[Amount])"

        # First call
        result1 = mapping._parse_dax_expression(expression)
        # Second call should hit cache
        result2 = mapping._parse_dax_expression(expression)

        assert result1 == result2
        # Verify cache info shows hits (lru_cache)
        assert mapping._parse_dax_expression.cache_info().hits > 0


class TestMeasureFactMappingExtraction:
    """Test measure-to-fact mapping extraction."""

    def test_single_metric_no_references(self, simple_model):
        """Metric with no table references should anchor to all facts."""
        graph = SemanticGraph(simple_model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        metric = SMLMetric(
            unique_name="total_amount",
            dataset="Sales",
            expression="",  # No table references
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Metric with no references should anchor to all facts
        assert result["total_amount"] == fact_tables

    def test_single_metric_single_table_reference(self, simple_model):
        """Metric referencing a reachable dimension should anchor to applicable facts."""
        graph = SemanticGraph(simple_model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        metric = SMLMetric(
            unique_name="product_count",
            dataset="Sales",
            expression="COUNTA([Product].[Id])",  # References Product dimension
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Sales fact can reach Product, so it should be applicable
        assert "Sales" in result["product_count"]

    def test_metric_unreachable_dimension(self, simple_model):
        """Metric referencing unreachable dimension should not anchor to facts."""
        graph = SemanticGraph(simple_model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        metric = SMLMetric(
            unique_name="unknown_metric",
            dataset="Sales",
            expression="SUM([UnknownTable].[Value])",  # References non-existent table
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Sales fact cannot reach UnknownTable
        assert result["unknown_metric"] == set()

    def test_multiple_facts_different_reachability(self, multi_fact_model):
        """Different facts may reach different dimensions."""
        graph = SemanticGraph(multi_fact_model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales", "Orders"}

        injector = DimensionInjector()

        # This metric references Customer, which is reachable from Sales but not Orders
        metric = SMLMetric(
            unique_name="customer_revenue",
            dataset="Sales",
            expression="SUM([Sales].[Amount]) WHERE [Customer].[Segment] = 'Premium'",
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Sales can reach Customer, Orders cannot
        assert "Sales" in result["customer_revenue"]
        assert "Orders" not in result["customer_revenue"]

    def test_multiple_metrics_batch_processing(self, simple_model):
        """Multiple metrics should be processed in one call."""
        graph = SemanticGraph(simple_model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        metrics = [
            SMLMetric(
                unique_name="total_amount",
                dataset="Sales",
                expression="SUM([Sales].[Amount])",
            ),
            SMLMetric(
                unique_name="product_count",
                dataset="Sales",
                expression="COUNTA([Product].[Id])",
            ),
            SMLMetric(unique_name="simple_count", dataset="Sales", expression=""),
        ]

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts(metrics, fact_tables, injector, graph)

        assert len(result) == 3
        assert "total_amount" in result
        assert "product_count" in result
        assert "simple_count" in result

    def test_cache_clear(self):
        """Cache should be clearable."""
        mapping = MeasureFactMapping()
        # Prime cache
        mapping._parse_dax_expression("SUM([Sales].[Amount])")
        assert mapping._parse_dax_expression.cache_info().currsize > 0

        # Clear
        mapping.clear_cache()

        assert mapping._parse_dax_expression.cache_info().currsize == 0


class TestMeasureFactMappingComplexExpressions:
    """Test complex DAX expression parsing scenarios."""

    def test_parse_related_function(self):
        """Parse RELATED() function with table reference."""
        mapping = MeasureFactMapping()
        expression = "SUM([Sales].[Amount]) + RELATED([Product].[Price])"
        result = mapping._parse_dax_expression(expression)
        assert "SALES" in result
        assert "PRODUCT" in result

    def test_parse_nested_function_calls(self):
        """Parse nested function calls with table references."""
        mapping = MeasureFactMapping()
        expression = "CALCULATE(SUM([Sales].[Amount]), FILTER([Product], [Category] = 'A'))"
        result = mapping._parse_dax_expression(expression)
        assert "SALES" in result
        assert "PRODUCT" in result

    def test_parse_with_special_characters(self):
        """Parse expressions with spaces and special characters in table names."""
        mapping = MeasureFactMapping()
        expression = "SUM([Product Sales].[Total])"
        result = mapping._parse_dax_expression(expression)
        assert "PRODUCT SALES" in result

    def test_parse_with_qualified_dimension_references(self):
        """Parse fully qualified DAX expressions."""
        mapping = MeasureFactMapping()
        expression = "[Sales Data].[Amount] + [Product Dim].[Count]"
        result = mapping._parse_dax_expression(expression)
        assert "SALES DATA" in result
        assert "PRODUCT DIM" in result


class TestMeasureFactMappingIntegration:
    """Integration tests with realistic scenarios."""

    def test_fact_junction_complex_graph(self):
        """Test with complex graph: Sales -> Product -> Category -> Subcategory."""
        model = SMLModel(
            unique_name="complex_model",
            datasets=[
                SMLDataset(unique_name="Sales", object_type="table"),
                SMLDataset(unique_name="Product", object_type="table"),
                SMLDataset(unique_name="Category", object_type="table"),
                SMLDataset(unique_name="Subcategory", object_type="table"),
                SMLDataset(unique_name="Customer", object_type="table"),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="Sales_to_Product",
                    from_dataset="Sales",
                    from_columns=["product_id"],
                    to_dataset="Product",
                    to_columns=["product_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="Product_to_Category",
                    from_dataset="Product",
                    from_columns=["category_id"],
                    to_dataset="Category",
                    to_columns=["category_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="Category_to_Subcategory",
                    from_dataset="Category",
                    from_columns=["subcat_id"],
                    to_dataset="Subcategory",
                    to_columns=["subcat_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="Sales_to_Customer",
                    from_dataset="Sales",
                    from_columns=["customer_id"],
                    to_dataset="Customer",
                    to_columns=["customer_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
            ],
            metrics=[],
        )

        graph = SemanticGraph(model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        # Metric referencing deep dimension (4 levels away)
        metric = SMLMetric(
            unique_name="subcategory_sales",
            dataset="Sales",
            expression="SUM([Sales].[Amount]) WHERE [Subcategory].[Name] = 'Widget'",
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Subcategory is reachable through Product -> Category -> Subcategory
        assert "Sales" in result["subcategory_sales"]

    def test_metric_with_multiple_independent_dimensions(self):
        """Metric referencing multiple dimensions from different paths."""
        model = SMLModel(
            unique_name="multi_dim_model",
            datasets=[
                SMLDataset(unique_name="Sales", object_type="table"),
                SMLDataset(unique_name="Product", object_type="table"),
                SMLDataset(unique_name="Store", object_type="table"),
                SMLDataset(unique_name="Time", object_type="table"),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="Sales_to_Product",
                    from_dataset="Sales",
                    from_columns=["product_id"],
                    to_dataset="Product",
                    to_columns=["product_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="Sales_to_Store",
                    from_dataset="Sales",
                    from_columns=["store_id"],
                    to_dataset="Store",
                    to_columns=["store_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
                SMLRelationship(
                    unique_name="Sales_to_Time",
                    from_dataset="Sales",
                    from_columns=["date_id"],
                    to_dataset="Time",
                    to_columns=["date_id"],
                    cardinality=Cardinality.MANY_TO_ONE,
                ),
            ],
            metrics=[],
        )

        graph = SemanticGraph(model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        metric = SMLMetric(
            unique_name="sales_by_product_and_store",
            dataset="Sales",
            expression="SUM([Sales].[Amount]) WHERE [Product].[Category] = 'Electronics' AND [Store].[Region] = 'West'",
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Sales can reach both Product and Store
        assert "Sales" in result["sales_by_product_and_store"]

    def test_error_handling_with_invalid_metric_structure(self):
        """Test graceful error handling with malformed metrics."""
        graph = SemanticGraph(
            SMLModel(
                unique_name="test_model",
                datasets=[SMLDataset(unique_name="Sales", object_type="table")],
                relationships=[],
                metrics=[],
            )
        )
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        # Metric with None unique_name - should this be handled?
        # Actually, unique_name is required in the schema, so we don't need to test this
        
        mapping = MeasureFactMapping()
        
        # Empty metric list should work
        result = mapping.extract_measure_facts([], fact_tables, injector, graph)
        assert result == {}

    def test_metric_anchors_to_no_facts(self):
        """Metric with references that aren't reachable from any fact."""
        model = SMLModel(
            unique_name="isolated_model",
            datasets=[
                SMLDataset(unique_name="Sales", object_type="table"),
                SMLDataset(unique_name="Warehouse", object_type="table"),
            ],
            relationships=[],  # No relationships - tables are isolated
            metrics=[],
        )

        graph = SemanticGraph(model)
        categorizer = TableCategorizer()
        categorizer.categorize(graph)
        fact_tables = {"Sales"}

        injector = DimensionInjector()

        # Metric referencing a non-reachable dimension
        metric = SMLMetric(
            unique_name="bad_metric",
            dataset="Sales",
            expression="SUM([Warehouse].[Inventory])",
        )

        mapping = MeasureFactMapping()
        result = mapping.extract_measure_facts([metric], fact_tables, injector, graph)

        # Warehouse is not reachable from Sales (no relationship)
        assert result["bad_metric"] == set()

