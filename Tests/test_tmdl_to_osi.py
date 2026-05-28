import pytest
from semabridge.converter.tmdl_to_osi import TMDLToOSIConverter
from semabridge.intermediate.models import OSIModel, OSIDataset, OSIMetric, OSIDimension, OSIDataType, OSICardinality, OSIAggregationType, OSICrossFilterDirection
from semabridge.core.exceptions import ConversionError


class TestTMDLToOSI:

    @pytest.fixture
    def converter(self):
        return TMDLToOSIConverter()

    def test_basic_conversion(self, converter):
        """Test converting a basic TMDL structure to OSI."""
        tmdl_files = {
            "definition/model.tmdl": (
                "model 'SalesModel'\n"
                "\n"
                "relationship 'SalesToCustomer'\n"
                "\tfromTable: Sales\n"
                "\tfromColumn: CustomerID\n"
                "\ttoTable: Customer\n"
                "\ttoColumn: CustomerID\n"
            ),
            "definition/tables/Sales.tmdl": (
                "table Sales\n"
                "\n"
                "column ProductID\n"
                "\tdataType: int64\n"
                "\tsummarizeBy: none\n"
                "\tsourceColumn: ProductID\n"
                "\n"
                "column Quantity\n"
                "\tdataType: int64\n"
                "\tsummarizeBy: sum\n"
                "\tsourceColumn: Qty\n"
                "\n"
                "column 'Revenue'\n"
                "\tdataType: double\n"
                "\tsummarizeBy: average\n"
                "\tsourceColumn: Rev\n"
                "\tformatString: $#,0.00\n"
                "\n"
                "measure Amount = TOTALYTD(SUM([Value]), 'Date'[Date])*.3\n"
                "\tformatString: $#,0.00\n"
                "\n"
                "measure 'Actual' =\n"
                "\tCALCULATE(\n"
                "\t\t[Amount],\n"
                "\t\tScenario[ScenarioDescription]=\"Actual\"\n"
                "\t)\n"
                "\tformatString: $#,0.00\n"
                "\tdescription: \"Actual sales amount\"\n"
            ),
            "definition/tables/Customer.tmdl": (
                "table 'Customer'\n"
                "\n"
                "column CustomerID\n"
                "\tdataType: string\n"
                "\tsummarizeBy: none\n"
            )
        }

        source = {
            "tmdl_files": tmdl_files,
            "workspace_id": "ws-123",
            "dataset_id": "ds-456",
            "display_name": "SalesModel"
        }

        osi = converter.to_osi(source)

        # 1. Model Metadata
        assert isinstance(osi, OSIModel)
        assert osi.unique_name == "SalesModel"
        assert osi.label == "SalesModel"
        assert osi.metadata["workspace_id"] == "ws-123"
        assert osi.source_platform == "fabric"

        # 2. Datasets
        assert len(osi.datasets) == 2
        sales_ds = next(d for d in osi.datasets if d.unique_name == "Sales")
        assert len(sales_ds.columns) == 3

        # Check Column attributes
        prod_col = next(c for c in sales_ds.columns if c.unique_name == "ProductID")
        assert prod_col.data_type == OSIDataType.INTEGER
        assert prod_col.is_key is True

        qty_col = next(c for c in sales_ds.columns if c.unique_name == "Quantity")
        assert qty_col.data_type == OSIDataType.INTEGER
        assert qty_col.is_measure_candidate is True
        assert qty_col.default_aggregation == OSIAggregationType.SUM

        rev_col = next(c for c in sales_ds.columns if c.unique_name == "Revenue")
        assert rev_col.data_type == OSIDataType.FLOAT
        assert rev_col.format_string == "$#,0.00"

        # 3. Metrics (Explicit measures + summarizeBy auto-metrics)
        # SummarizeBy columns are 'Quantity' (SUM) and 'Revenue' (AVG)
        # Explicit measures are 'Amount' and 'Actual'
        # Total metrics: 4
        assert len(osi.metrics) == 4
        metrics_by_name = {m.unique_name: m for m in osi.metrics}

        assert "Amount" in metrics_by_name
        assert metrics_by_name["Amount"].expression == "TOTALYTD(SUM([Value]), 'Date'[Date])*.3"
        assert metrics_by_name["Amount"].format_string == "$#,0.00"

        assert "Actual" in metrics_by_name
        assert "CALCULATE" in metrics_by_name["Actual"].expression
        assert "Actual sales amount" in metrics_by_name["Actual"].description
        assert metrics_by_name["Actual"].format_string == "$#,0.00"

        assert "Quantity" in metrics_by_name
        assert metrics_by_name["Quantity"].aggregation == OSIAggregationType.SUM

        # 4. Relationships
        assert len(osi.relationships) == 1
        rel = osi.relationships[0]
        assert rel.from_dataset == "Sales"
        assert rel.to_dataset == "Customer"
        assert rel.from_columns == ["CustomerID"]
        assert rel.to_columns == ["CustomerID"]
        assert rel.cardinality == OSICardinality.MANY_TO_ONE

        # 5. Dimensions
        assert len(osi.dimensions) == 2
        sales_dim = next(d for d in osi.dimensions if d.unique_name == "Sales")
        # Only ProductID is not is_measure_candidate
        assert [a.unique_name for a in sales_dim.attributes] == ["ProductID"]

    def test_missing_input(self, converter):
        """Test error handling for missing inputs."""
        with pytest.raises(ConversionError) as exc:
            converter.to_osi({})
        assert "Missing 'tmdl_files' or 'dataset_id'" in str(exc.value)

    def test_malformed_tmdl(self, converter):
        """Test malformed input wrapping."""
        source = {
            "tmdl_files": {"definition/tables/BadTable.tmdl": "table BadTable\ncolumn Column\n\tdataType: invalid_type"},
            "workspace_id": "ws",
            "dataset_id": "ds"
        }
        osi = converter.to_osi(source)
        # Invalid data type falls back to STRING
        ds = osi.datasets[0]
        assert ds.columns[0].data_type == OSIDataType.STRING

    def test_system_table_filtering_and_relationship_parsing(self, converter):
        """Test that system tables are filtered out and native TMDL relationships are correctly parsed and mapped."""
        tmdl_files = {
            "definition/model.tmdl": (
                "model 'SalesModel'\n"
                "\n"
                "relationship 'FactToDate'\n"
                "\tfromColumn: Fact.Date\n"
                "\ttoColumn: Date.Date\n"
            ),
            "definition/tables/Fact.tmdl": (
                "table Fact\n"
                "\n"
                "column Date\n"
                "\tdataType: dateTime\n"
                "\tsummarizeBy: none\n"
            ),
            "definition/tables/Date.tmdl": (
                "table Date\n"
                "\n"
                "column Date\n"
                "\tdataType: dateTime\n"
                "\tsummarizeBy: none\n"
            ),
            "definition/tables/DateTableTemplate_xyz.tmdl": (
                "table DateTableTemplate_xyz\n"
                "\tisHidden\n"
                "\n"
                "column Date\n"
                "\tdataType: dateTime\n"
            ),
            "definition/tables/LocalDateTable_123.tmdl": (
                "table LocalDateTable_123\n"
                "\n"
                "column Date\n"
                "\tdataType: dateTime\n"
            )
        }

        source = {
            "tmdl_files": tmdl_files,
            "workspace_id": "ws-123",
            "dataset_id": "ds-456",
            "display_name": "SalesModel"
        }

        osi = converter.to_osi(source)

        # Verify tables list: template and local date tables are filtered out
        datasets = {ds.unique_name for ds in osi.datasets}
        assert "Fact" in datasets
        assert "Date" in datasets
        assert "DateTableTemplate_xyz" not in datasets
        assert "LocalDateTable_123" not in datasets
        assert len(osi.datasets) == 2

        # Verify relationship was correctly parsed and kept
        assert len(osi.relationships) == 1
        rel = osi.relationships[0]
        assert rel.from_dataset == "Fact"
        assert rel.to_dataset == "Date"
        assert rel.from_columns == ["Date"]
        assert rel.to_columns == ["Date"]

    def test_advanced_relationship_parsing_and_helpers(self, converter):
        """Test parsing relationships with advanced attributes and using join helpers."""
        tmdl_files = {
            "definition/model.tmdl": (
                "model 'SalesModel'\n"
                "\n"
                "relationship 'FactToCustomer'\n"
                "\tfromColumn: Fact.CustomerID\n"
                "\ttoColumn: Customer.CustomerID\n"
                "\tcardinality: manyToOne\n"
                "\tcrossFiltering: bothDirections\n"
                "\tisActive: false\n"
                "\n"
                "relationship 'FactToDate'\n"
                "\tfromColumn: Fact.Date\n"
                "\ttoColumn: Date.Date\n"
                "\tjoinOnDateBehavior: datePartOnly\n"
            ),
            "definition/tables/Fact.tmdl": (
                "table Fact\n"
                "\n"
                "column CustomerID\n"
                "\tdataType: int64\n"
                "\tsummarizeBy: none\n"
            ),
            "definition/tables/Customer.tmdl": (
                "table Customer\n"
                "\n"
                "column CustomerID\n"
                "\tdataType: int64\n"
                "\tsummarizeBy: none\n"
            ),
            "definition/tables/Date.tmdl": (
                "table Date\n"
                "\n"
                "column Date\n"
                "\tdataType: dateTime\n"
            )
        }

        source = {
            "tmdl_files": tmdl_files,
            "workspace_id": "ws-123",
            "dataset_id": "ds-456",
            "display_name": "SalesModel"
        }

        osi = converter.to_osi(source)
        # 1 relationship parsed (FactToCustomer), while FactToDate is skipped
        assert len(osi.relationships) == 1
        rel = osi.relationships[0]

        # Verify parsed properties
        assert rel.relationship_id == "FactToCustomer"
        assert rel.from_dataset == "Fact"
        assert rel.to_dataset == "Customer"
        assert rel.from_columns == ["CustomerID"]
        assert rel.to_columns == ["CustomerID"]
        assert rel.cardinality == OSICardinality.MANY_TO_ONE
        assert rel.cross_filter_direction == OSICrossFilterDirection.BOTH
        assert not rel.join_on_date_behavior
        assert rel.is_active is False

        # Verify join helper methods
        assert rel.generate_semantic_join("f", "c") == 'f."CustomerID" = c."CustomerID"'
        assert rel.generate_sql_join("f", "c", "LEFT") == 'LEFT JOIN "Customer" c ON f."CustomerID" = c."CustomerID"'

        # Verify graph helper method
        found_rel = osi.get_relationship_between("Fact", "Customer")
        assert found_rel == rel

        found_rel_rev = osi.get_relationship_between("Customer", "Fact")
        assert found_rel_rev == rel

        assert osi.get_relationship_between("Fact", "NonExistent") is None


    def test_get_all_user_tables_robust_discovery(self):
        """Test that get_all_user_tables successfully filters system/template tables while keeping all valid lookup/fact tables."""
        from semabridge.converter.tmdl_to_osi import get_all_user_tables
        tmdl_files = {
            "definition/tables/Fact.tmdl": "table Fact\ncolumn Col\n\tdataType: int\n",
            "definition/tables/Scenario.tmdl": "table Scenario\ncolumn Col\n\tdataType: int\n",
            "definition/tables/Range.tmdl": "table Range\ncolumn Col\n\tdataType: int\n",
            "definition/tables/DateTableTemplate_abc.tmdl": "table DateTableTemplate_abc\n\tisHidden\ncolumn Col\n\tdataType: int\n",
            "definition/tables/LocalDateTable_123.tmdl": "table LocalDateTable_123\n\tshowAsVariationsOnly\ncolumn Col\n\tdataType: int\n",
        }
        user_tables = get_all_user_tables(tmdl_files)
        assert len(user_tables) == 3
        assert set(user_tables) == {"Fact", "Scenario", "Range"}

    def test_dax_dependency_aware_translator(self):
        """Test that DAXTranslator builds dependency graphs, sorts them, and translates complex/nested measures correctly."""
        from semabridge.converter.dax_translator import DAXTranslator
        from types import SimpleNamespace
        
        translator = DAXTranslator()
        
        metrics = [
            SimpleNamespace(unique_name="Amount", expression="SUM([Value])", sql_expression=None),
            SimpleNamespace(unique_name="Actual", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Actual\")", sql_expression=None),
            SimpleNamespace(unique_name="Plan", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Plan\")", sql_expression=None),
            SimpleNamespace(unique_name="Var Plan", expression="[Actual]-[Plan]", sql_expression=None),
            SimpleNamespace(unique_name="Var Plan %", expression="DIVIDE([Var Plan],[Plan], BLANK())", sql_expression=None),
        ]
        
        # 1. Test build_dependency_graph
        graph = translator.build_dependency_graph(metrics)
        assert graph["Amount"] == []
        assert graph["Actual"] == ["Amount"]
        assert graph["Plan"] == ["Amount"]
        assert set(graph["Var Plan"]) == {"Actual", "Plan"}
        assert set(graph["Var Plan %"]) == {"Var Plan", "Plan"}
        
        # 2. Test get_translation_order
        order = translator.get_translation_order(graph)
        assert order.index("Amount") < order.index("Actual")
        assert order.index("Amount") < order.index("Plan")
        assert order.index("Actual") < order.index("Var Plan")
        assert order.index("Plan") < order.index("Var Plan")
        assert order.index("Var Plan") < order.index("Var Plan %")
        
        # 3. Test translate_with_dependencies
        translator.translate_with_dependencies(metrics, "Fact", "Fact")
        
        amount_sql = next(m for m in metrics if m.unique_name == "Amount").sql_expression
        actual_sql = next(m for m in metrics if m.unique_name == "Actual").sql_expression
        plan_sql = next(m for m in metrics if m.unique_name == "Plan").sql_expression
        var_plan_sql = next(m for m in metrics if m.unique_name == "Var Plan").sql_expression
        var_plan_pct_sql = next(m for m in metrics if m.unique_name == "Var Plan %").sql_expression
        
        assert amount_sql == 'SUM(Fact."VALUE")'
        assert actual_sql.replace("(", "").replace(")", "") == "CASE WHEN SCENARIO.SCENARIODESCRIPTION = 'Actual' THEN SUMFact.\"VALUE\" ELSE NULL END".replace("(", "").replace(")", "")
        assert plan_sql.replace("(", "").replace(")", "") == "CASE WHEN SCENARIO.SCENARIODESCRIPTION = 'Plan' THEN SUMFact.\"VALUE\" ELSE NULL END".replace("(", "").replace(")", "")
        assert var_plan_sql is not None
        assert var_plan_pct_sql.strip("() ").startswith("CASE WHEN")

    def test_dax_dynamic_calendar_translator(self):
        """Test that DAXTranslator dynamically extracts and translates calendar tables like 'DimDate' instead of assuming hardcoded 'CALENDAR'."""
        from semabridge.converter.dax_translator import DAXTranslator
        from types import SimpleNamespace
        
        translator = DAXTranslator()
        
        # Test 1: get_required_dimensions dynamically parses 'DimDate'
        ytd_dax = "TOTALYTD(SUM([Sales]), 'DimDate'[DateKey])"
        dims = translator.get_required_dimensions(ytd_dax)
        assert dims == ["'DimDate'[DateKey]"]
        
        # Test 2: translate_expr dynamically translates using 'DIMDATE' table name
        metrics = [
            SimpleNamespace(unique_name="Sales", expression="SUM([Sales])", sql_expression=None)
        ]
        sql = translator.translate_expr(ytd_dax, metrics, "Fact")
        # Should use DIMDATE instead of CALENDAR
        assert "DIMDATE.YEAR" in sql.upper()
        assert "DIMDATE.PERIOD" in sql.upper()
        assert "CALENDAR" not in sql.upper()
