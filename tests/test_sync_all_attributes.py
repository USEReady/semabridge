import pytest
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from pydantic import SecretStr

def test_sync_all_attributes_logic():
    # 1. Mock TMSL with hidden and measure-like columns
    tmsl = {
        "model": {
            "name": "TestModel",
            "tables": [{
                "name": "Fact_Sales",
                "columns": [
                    {"name": "Revenue", "dataType": "double", "summarizeBy": "sum"},
                    {"name": "SecretID", "dataType": "string", "isHidden": True},
                    {"name": "Date", "dataType": "dateTime"}
                ],
                "measures": [
                    {"name": "Total Revenue", "expression": "SUM([Revenue])"}
                ]
            }]
        }
    }
    
    # 2. Test TMSL Transformation with sync_all_attributes=True (Default)
    behavior = ConnectorBehavior()
    behavior.semantic_model.sync_all_attributes = True
    
    transformer = TMSLTransformer()
    sml = transformer.transform(tmsl, "ws", "ds", behavior=behavior)
    
    # Hidden and measure candidates should be in dimensions
    dim = sml.dimensions[0]
    attr_names = [a.unique_name for a in dim.attributes]
    assert "Revenue" in attr_names
    assert "SecretID" in attr_names
    
    # 3. Test Snowflake DDL Generation
    config = SnowflakeConfig(
        account="acc", user="usr", password=SecretStr("pwd"), 
        warehouse="wh", database="db", schema_name="sc", role="rl"
    )
    emitter = SnowflakeEmitter(config, behavior=behavior)
    ddl = emitter._generate_semantic_view(sml)
    
    # Aliases are now lowercase (centralized naming: Fact_Sales → factsales)
    assert 'factsales."REVENUE" AS "REVENUE"' in ddl
    assert 'factsales."SECRETID" AS "SECRETID"' in ddl

def test_sync_all_attributes_disabled():
    tmsl = {
        "model": {
            "name": "TestModel",
            "tables": [{
                "name": "Fact_Sales",
                "columns": [
                    {"name": "Revenue", "dataType": "double", "summarizeBy": "sum"},
                    {"name": "SecretID", "dataType": "string", "isHidden": True},
                    {"name": "Date", "dataType": "dateTime"}
                ]
            }]
        }
    }
    
    behavior = ConnectorBehavior()
    behavior.semantic_model.sync_all_attributes = False
    
    transformer = TMSLTransformer()
    sml = transformer.transform(tmsl, "ws", "ds", behavior=behavior)
    
    # They should be filtered out
    dim = sml.dimensions[0]
    attr_names = [a.unique_name for a in dim.attributes]
    assert "Revenue" not in attr_names
    assert "SecretID" not in attr_names
