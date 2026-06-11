import pytest
from datetime import datetime

from semabridge.intermediate.models import OSIModel, OSIDataset, OSIColumn, OSIMetric, OSIDataType, OSIAggregationType
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType as SMLDataType, AggregationType as SMLAggregationType
from semabridge.csm.models import CSMModel, CSMMetric
from semabridge.converter.adapters.osi_to_csm import OSIToCSMConverter
from semabridge.converter.adapters.csm_to_osi import CSMToOSIConverter
from semabridge.converter.adapters.sml_to_csm import SMLToCSMConverter
from semabridge.converter.adapters.csm_to_sml import CSMToSMLConverter

def test_osi_to_csm_to_osi():
    # Setup test OSI Model
    osi = OSIModel(
        unique_name="TestOSI",
        label="Test OSI Model",
        version="1.0.0",
        datasets=[
            OSIDataset(
                unique_name="Sales",
                label="Sales Data",
                source_table="SALES_FACT",
                columns=[
                    OSIColumn(unique_name="Id", label="Id", data_type=OSIDataType.INTEGER, is_key=True),
                    OSIColumn(unique_name="Units", label="Units", data_type=OSIDataType.INTEGER)
                ],
                is_fact=True
            )
        ],
        metrics=[
            OSIMetric(
                unique_name="Total Units",
                label="Total Units",
                dataset="Sales",
                expression="SUM(Sales[Units])",
                aggregation=OSIAggregationType.SUM,
                source_column="Units"
            )
        ],
        metadata={"vendor": "fabric"}
    )
    
    # Convert to CSM
    csm = OSIToCSMConverter().convert(osi)
    assert csm.unique_name == "TestOSI"
    assert "vendor" in csm.extensions
    assert len(csm.metrics) == 1
    assert csm.metrics[0].expression_dialects["DAX"] == "SUM(Sales[Units])"
    
    # Convert back to OSI
    roundtrip = CSMToOSIConverter().convert(csm)
    
    # Verify
    assert roundtrip.unique_name == osi.unique_name
    assert roundtrip.metadata == osi.metadata
    assert len(roundtrip.datasets) == 1
    assert roundtrip.datasets[0].unique_name == "Sales"
    assert len(roundtrip.metrics) == 1
    assert roundtrip.metrics[0].expression == "SUM(Sales[Units])"

def test_sml_to_csm_to_sml():
    sml = SMLModel(
        unique_name="TestSML",
        label="Test SML Model",
        version="1.0.0",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                label="Sales Data",
                source_table="SALES_FACT",
                columns=[
                    SMLColumn(unique_name="Id", label="Id", data_type=SMLDataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Units", label="Units", data_type=SMLDataType.INTEGER)
                ],
                is_fact=True
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Units SQL",
                label="Total Units SQL",
                dataset="Sales",
                sql_expression="SUM(SALES_FACT.UNITS)",
                expression="SUM(Sales[Units])",
                aggregation=SMLAggregationType.SUM,
                source_column="Units",
                sync_enabled=True
            )
        ]
    )
    
    # Convert to CSM
    csm = SMLToCSMConverter().convert(sml)
    assert csm.unique_name == "TestSML"
    assert csm.metrics[0].expression_dialects["SNOWFLAKE_SQL"] == "SUM(SALES_FACT.UNITS)"
    
    # Convert back to SML
    roundtrip = CSMToSMLConverter().convert(csm)
    
    assert roundtrip.unique_name == sml.unique_name
    assert roundtrip.metrics[0].sql_expression == "SUM(SALES_FACT.UNITS)"
    assert roundtrip.metrics[0].expression == "SUM(Sales[Units])"
    assert roundtrip.metrics[0].sync_enabled == True

def test_expression_dialects_preserved():
    m = CSMMetric(unique_name="test", label="test", dataset="ds")
    m.expression_dialects["DAX"] = "SUM(x)"
    assert m.expression_dialects["DAX"] == "SUM(x)"
