import pytest
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import OSIModel, OSIDataset, OSIMetric, OSIDimension, OSIDataType, OSICardinality
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
