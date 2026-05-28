import pytest
import tempfile
import yaml
from pathlib import Path

from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIAggregationType,
    OSIDataType,
    OSICardinality,
    OSICrossFilterDirection,
)
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.synonyms import merge_synonyms, load_domain_glossary, match_column_patterns


def test_inactive_relationships_warning_generation():
    """Verify that skipped inactive relationships generate detailed warnings on metrics using USERELATIONSHIP."""
    # 1. Create a model with 1 active and 1 inactive relationship
    ds1 = OSIDataset(unique_name="Sales", label="Sales", columns=[
        OSIColumn(unique_name="OrderDate", data_type=OSIDataType.DATE),
        OSIColumn(unique_name="ShipDate", data_type=OSIDataType.DATE),
        OSIColumn(unique_name="Revenue", data_type=OSIDataType.DECIMAL),
    ])
    ds2 = OSIDataset(unique_name="Calendar", label="Calendar", columns=[
        OSIColumn(unique_name="Date", data_type=OSIDataType.DATE, is_key=True),
    ])
    
    # Active relation on OrderDate
    rel_active = OSIRelationship(
        unique_name="REL_Sales_OrderDate__Calendar_Date",
        from_dataset="Sales",
        from_columns=["OrderDate"],
        to_dataset="Calendar",
        to_columns=["Date"],
        cardinality=OSICardinality.MANY_TO_ONE,
        cross_filter_direction=OSICrossFilterDirection.SINGLE,
        is_active=True,
    )
    
    # Inactive relation on ShipDate
    rel_inactive = OSIRelationship(
        unique_name="REL_Sales_ShipDate__Calendar_Date",
        from_dataset="Sales",
        from_columns=["ShipDate"],
        to_dataset="Calendar",
        to_columns=["Date"],
        cardinality=OSICardinality.MANY_TO_ONE,
        cross_filter_direction=OSICrossFilterDirection.SINGLE,
        is_active=False,
    )
    
    # Measure 1 uses the inactive relationship via USERELATIONSHIP
    m1 = OSIMetric(
        unique_name="ShippedRevenue",
        label="Shipped Revenue",
        dataset="Sales",
        expression="CALCULATE(SUM(Sales[Revenue]), USERELATIONSHIP(Sales[ShipDate], Calendar[Date]))",
        aggregation=OSIAggregationType.SUM,
        source_column="Revenue",
    )
    
    # Measure 2 uses the active relationship or regular aggregation
    m2 = OSIMetric(
        unique_name="TotalRevenue",
        label="Total Revenue",
        dataset="Sales",
        expression="SUM(Sales[Revenue])",
        aggregation=OSIAggregationType.SUM,
        source_column="Revenue",
    )
    
    osi = OSIModel(
        unique_name="TestModel",
        label="Test Model",
        datasets=[ds1, ds2],
        relationships=[rel_active, rel_inactive],
        metrics=[m1, m2],
    )
    
    # 2. Convert to SML
    converter = OSIToSMLConverter()
    sml = converter.from_osi(osi)
    
    # 3. Verify m1 has the skipped relationship warning and m2 does not
    metric_map = {m.unique_name: m for m in sml.metrics}
    
    assert "ShippedRevenue" in metric_map
    assert "TotalRevenue" in metric_map
    
    m1_sml = metric_map["ShippedRevenue"]
    m2_sml = metric_map["TotalRevenue"]
    
    assert len(m1_sml.translation_warning) > 0
    assert any("Inactive relationship skipped: Sales[ShipDate] -> Calendar[Date]" in w for w in m1_sml.translation_warning)
    assert not any("Inactive relationship skipped" in w for w in m2_sml.translation_warning)


def test_snowflake_emitter_strict_deployment():
    """Verify that strict mode aborts deployment when translation warnings exist."""
    # 1. Create a behavior with strict mode enabled
    behavior = ConnectorBehavior()
    behavior.snowflake.strict_inactive_relationships = True
    
    config = SnowflakeConfig(
        account="test_account",
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="test_db",
        schema_name="test_schema",
    )
    
    emitter = SnowflakeEmitter(config, behavior=behavior)
    
    # 2. Mock a model with a metric containing a translation warning
    ds1 = OSIDataset(unique_name="Sales", label="Sales", columns=[
        OSIColumn(unique_name="Revenue", data_type=OSIDataType.DECIMAL),
    ])
    m1 = OSIMetric(
        unique_name="TestMetric",
        dataset="Sales",
        source_column="Revenue",
        translation_warning=["Test translation warning"],
    )
    osi = OSIModel(
        unique_name="StrictTestModel",
        label="Strict Test Model",
        datasets=[ds1],
        metrics=[m1],
    )
    
    # Verify it returns False and registers the error in last_deployment_error
    success = emitter._execute_deployment_pipeline(osi, is_osi=True)
    
    assert success is False
    assert emitter.last_deployment_error is not None
    assert "Strict deployment aborted" in emitter.last_deployment_error
    assert "Test translation warning" in emitter.last_deployment_error


def test_layered_synonyms_merging():
    """Verify the layered synonym generation strategy matches user behavior.yaml decisions."""
    # 1. Test Domain Glossary Loader
    with tempfile.TemporaryDirectory() as tmpdir:
        glossary_path = Path(tmpdir) / "glossary.yaml"
        glossary_data = {
            "pt_adm_dt": ["Patient Admission Date", "Admission Date"],
            "amt": "Amount",
        }
        with open(glossary_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(glossary_data, f)
            
        glossary = load_domain_glossary(str(glossary_path))
        assert glossary["pt_adm_dt"] == ["Patient Admission Date", "Admission Date"]
        assert glossary["amt"] == ["Amount"]
        
        # 2. Test Pattern Rules Matching
        patterns = [
            {"match": "^amt$|_amt$", "label": "Amount"},
            {"match": "^dt$|_dt$", "label": "Date"},
        ]
        matched = match_column_patterns("total_amt", patterns)
        assert matched == ["Amount"]
        
        matched_dt = match_column_patterns("ship_dt", patterns)
        assert matched_dt == ["Date"]

        # 3. Test Layered Synonyms Merging
        behavior = ConnectorBehavior()
        behavior.synonyms.max_per_field = 5
        behavior.synonyms.preserve_auto = True
        behavior.synonyms.domain_glossary = str(glossary_path)
        behavior.synonyms.column_patterns = [
            {"match": "^amt$|_amt$", "label": "Amount Override"},
        ]
        
        # Priority order: UI overrides > Domain Glossary > Column Patterns > User defined (TMSL) > Auto-generated (Fallback)
        merged = merge_synonyms(
            ui_overrides=["UI Custom Label"],
            user_defined=["TMSL Default"],
            auto_generated=["Auto Title"],
            field_name="pt_adm_dt",
            behavior=behavior,
        )
        
        # Should include: UI override, Glossary items, TMSL default, Auto-generated (Fallback)
        assert merged[0] == "UI Custom Label"
        assert merged[1] == "Patient Admission Date"
        assert merged[2] == "Admission Date"
        assert merged[3] == "TMSL Default"
        assert merged[4] == "Auto Title"
        assert len(merged) == 5
        
        # Test max_per_field capping (max 3)
        behavior.synonyms.max_per_field = 3
        merged_capped = merge_synonyms(
            ui_overrides=["UI Custom Label"],
            user_defined=["TMSL Default"],
            auto_generated=["Auto Title"],
            field_name="pt_adm_dt",
            behavior=behavior,
        )
        assert len(merged_capped) == 3
        assert merged_capped == ["UI Custom Label", "Patient Admission Date", "Admission Date"]
        
        # Test preserve_auto=False
        behavior.synonyms.max_per_field = 5
        behavior.synonyms.preserve_auto = False
        merged_no_auto = merge_synonyms(
            ui_overrides=["UI Custom Label"],
            user_defined=["TMSL Default"],
            auto_generated=["Auto Title"],
            field_name="pt_adm_dt",
            behavior=behavior,
        )
        assert "Auto Title" not in merged_no_auto
        assert merged_no_auto == ["UI Custom Label", "Patient Admission Date", "Admission Date", "TMSL Default"]
