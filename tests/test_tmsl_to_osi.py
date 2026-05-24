import pytest
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import OSIModel, OSIDataset, OSIMetric, OSIDimension, OSIDataType, OSICardinality, OSIAggregationType
from semabridge.core.exceptions import ConversionError

class TestTMSLToOSI:
    
    @pytest.fixture
    def converter(self):
        return TMSLToOSIConverter()

    def test_basic_conversion(self, converter, sample_tmsl_json):
        """Test converting a basic TMSL structure to OSI."""
        source = {
            "tmsl": sample_tmsl_json,
            "workspace_id": "ws-123",
            "dataset_id": "ds-456"
        }
        
        osi = converter.to_osi(source)
        
        # 1. Check Model Meta
        assert isinstance(osi, OSIModel)
        assert osi.unique_name == "ds-456"
        assert osi.label == "SalesModel"
        assert osi.metadata["workspace_id"] == "ws-123"
        assert osi.source_platform == "fabric"
        
        # 2. Check Datasets
        assert len(osi.datasets) == 2
        
        sales_ds = next(d for d in osi.datasets if d.unique_name == "Sales")
        assert len(sales_ds.columns) == 3
        
        # Check column type logic
        rev_col = next(c for c in sales_ds.columns if c.unique_name == "Revenue")
        assert rev_col.data_type == OSIDataType.FLOAT # double -> float (or decimal if mapped so)
        
        qty_col = next(c for c in sales_ds.columns if c.unique_name == "Quantity")
        assert qty_col.data_type == OSIDataType.INTEGER # int64
        
        # Check source table override heuristic
        table_ds = OSIDataset(unique_name="Table", source_table="Table")
        # In our converter, we override source_table for "Table" to "DEVICE_INVENTORY"
        ds_t = converter._parse_dataset({"name": "Table"})
        assert ds_t.source_table == "DEVICE_INVENTORY"

        # 3. Check Metrics
        assert len(osi.metrics) == 1
        metric = osi.metrics[0]
        assert metric.unique_name == "Total Revenue"
        assert metric.dataset == "Sales"
        assert metric.expression == "SUM([Revenue])"

        # 4. Check Relationships
        assert len(osi.relationships) == 1
        rel = osi.relationships[0]
        assert rel.from_dataset == "Sales"
        assert rel.to_dataset == "Customer"
        assert rel.cardinality == OSICardinality.MANY_TO_ONE
        
        # 5. Check Dimensions (Implicit creation)
        assert len(osi.dimensions) == 2
        dim_sales = next(d for d in osi.dimensions if d.unique_name == "Sales")
        assert dim_sales is not None
        assert len(dim_sales.attributes) == 3

    def test_missing_input(self, converter):
        """Test error handling for missing inputs."""
        with pytest.raises(ConversionError) as exc:
            converter.to_osi({})
        assert "Missing 'tmsl' or 'dataset_id'" in str(exc.value)

    def test_malformed_tmsl(self, converter):
        """Test specific malformed input cases (if any specific checks exist)."""
        source = {
            "tmsl": {"model": {"tables": [{"name": "BadTable", "columns": "invalid_list"}]}}, 
            "workspace_id": "ws",
            "dataset_id": "ds"
        }
        # This will likely raise a KeyError or TypeError inside _parse_column iteration
        # Ensure it is wrapped in ConversionError
        with pytest.raises(ConversionError):
            converter.to_osi(source)

    def test_extended_fabric_datatypes_map_to_osi(self, converter):
        """Ensure common Fabric type variants map to non-string OSI datatypes."""
        source = {
            "tmsl": {
                "model": {
                    "name": "TypedModel",
                    "tables": [{
                        "name": "TypedTable",
                        "columns": [
                            {"name": "OrderDate", "dataType": "Date"},
                            {"name": "EventTime", "dataType": "Time"},
                            {"name": "IsActive", "dataType": "Bool"},
                            {"name": "Amount", "dataType": "Currency"},
                        ],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-typed",
        }

        osi = converter.to_osi(source)
        ds = next(d for d in osi.datasets if d.unique_name == "TypedTable")

        assert next(c for c in ds.columns if c.unique_name == "OrderDate").data_type == OSIDataType.DATE
        assert next(c for c in ds.columns if c.unique_name == "EventTime").data_type == OSIDataType.TIME
        assert next(c for c in ds.columns if c.unique_name == "IsActive").data_type == OSIDataType.BOOLEAN
        assert next(c for c in ds.columns if c.unique_name == "Amount").data_type == OSIDataType.DECIMAL

    def test_blank_measure_is_retained(self, converter):
        """Blank/error-state measures should still appear in OSI output."""
        source = {
            "tmsl": {
                "model": {
                    "name": "BrokenMeasureModel",
                    "tables": [{
                        "name": "Sales",
                        "columns": [],
                        "measures": [{
                            "name": "Broken Measure",
                            "expression": "",
                        }],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-broken",
        }

        osi = converter.to_osi(source)

        assert len(osi.metrics) == 1
        metric = osi.metrics[0]
        assert metric.unique_name == "Broken Measure"
        assert metric.expression == "[Broken Measure]"

    def test_summarize_by_columns_become_metrics_not_dimensions(self, converter):
        """Fabric Aggregation columns should emit as metrics and be excluded from dimensions."""
        source = {
            "tmsl": {
                "model": {
                    "name": "AggregationModel",
                    "tables": [{
                        "name": "SalesFact",
                        "columns": [
                            {"name": "ProductID", "dataType": "int64", "summarizeBy": "none"},
                            {"name": "Units", "dataType": "int64", "summarizeBy": "sum"},
                            {"name": "Revenue", "dataType": "double", "summarizeBy": "sum"},
                            {"name": "Score", "dataType": "double", "summarizeBy": "average"},
                        ],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-agg",
        }

        osi = converter.to_osi(source)
        ds = next(d for d in osi.datasets if d.unique_name == "SalesFact")
        units_col = next(c for c in ds.columns if c.unique_name == "Units")

        assert units_col.is_measure_candidate is True
        assert units_col.default_aggregation == OSIAggregationType.SUM

        metric_by_name = {m.unique_name: m for m in osi.metrics}
        assert metric_by_name["Units"].source_column == "Units"
        assert metric_by_name["Units"].aggregation == OSIAggregationType.SUM
        assert metric_by_name["Revenue"].source_column == "Revenue"
        assert metric_by_name["Score"].aggregation == OSIAggregationType.AVG

        dim = next(d for d in osi.dimensions if d.unique_name == "SalesFact")
        assert [attr.unique_name for attr in dim.attributes] == ["ProductID"]

    def test_skip_auto_hidden_date_tables(self, converter):
        """Auto-generated Power BI date tables should be skipped in OSI conversion."""
        source = {
            "tmsl": {
                "model": {
                    "name": "DateModel",
                    "tables": [
                        {"name": "Sales", "columns": [{"name": "Id", "dataType": "int64"}]},
                        {"name": "LocalDateTable_123", "isHidden": True, "columns": []},
                        {"name": "DateTableTemplate_abc", "isHidden": True, "columns": []},
                    ],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-date",
        }

        osi = converter.to_osi(source)

        assert [ds.unique_name for ds in osi.datasets] == ["Sales"]
