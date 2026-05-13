
import pytest
from semabridge.intermediate.models import OSIModel, OSIDataset, OSIColumn, OSIMetric, OSIDataType, OSIAggregationType
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.sml.models import DataType

def test_osi_to_sml_synonym_propagation():
    # Create OSI with synonyms
    col = OSIColumn(
        unique_name="Sales",
        data_type=OSIDataType.DECIMAL,
        synonyms=["Revenue", "Takings"]
    )
    metric = OSIMetric(
        unique_name="Total_Sales",
        label="Total Sales",
        dataset="SalesTable",
        expression="SUM([Sales])",
        aggregation=OSIAggregationType.SUM,
        synonyms=["Gross Revenue", "Total Income"]
    )
    ds = OSIDataset(
        unique_name="SalesTable",
        source_table="SALES_DATA",
        columns=[col]
    )
    osi = OSIModel(
        unique_name="TestModel",
        datasets=[ds],
        metrics=[metric]
    )
    
    # Convert
    converter = OSIToSMLConverter()
    sml = converter.from_osi(osi)
    
    # Verify column synonyms
    sml_col = sml.datasets[0].columns[0]
    assert sml_col.unique_name == "Sales"
    assert "Revenue" in sml_col.synonyms
    assert "Takings" in sml_col.synonyms
    
    # Verify metric synonyms
    sml_metric = sml.get_metric("Total_Sales")
    assert sml_metric is not None
    assert "Gross Revenue" in sml_metric.synonyms
    assert "Total Income" in sml_metric.synonyms
