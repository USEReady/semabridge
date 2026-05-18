"""Integration tests for Fabric (BIM) to Snowflake (Semantic View) synchronization."""

from __future__ import annotations

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from semabridge.converter.tmsl_to_osi import TMDLToOSIConverter
from semabridge.connectors.ddl_builder import SemanticViewBuilder
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig


@pytest.fixture
def snowflake_config():
    return SnowflakeConfig(
        account="test.local",
        user="test_user",
        password="test_password",
        warehouse="test_wh",
        database="TEST_DB",
        SNOWFLAKE_SCHEMA="TEST_SCHEMA",
        role="test_role",
    )



@pytest.fixture
def connector_behavior():
    return ConnectorBehavior()


def test_sync_identity_bim_to_snowflake(sample_tmsl_json, snowflake_config, connector_behavior):
    """
    Slice 1: Verify that a minimal Fabric BIM file correctly syncs to Snowflake DDL.
    Focus: TABLES and DIMENSIONS clauses for a single table.
    """
    import os
    os.environ["SNOWFLAKE_SCHEMA"] = "TEST_SCHEMA"
    
    # 1. Convert TMSL to OSI
    converter = TMDLToOSIConverter()
    source_data = {
        "tmdl": sample_tmsl_json,
        "dataset_id": "SalesModel",
        "workspace_id": "test-workspace-id"
    }
    osi_model = converter.to_osi(source_data)
    
    from unittest.mock import MagicMock
    from semabridge.utils.identifiers import IdentifierSanitizer
    
    mock_schema_manager = MagicMock()
    mock_translator = MagicMock()
    # Mock some basic behaviors if needed
    mock_schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: col
    mock_translator._normalize_metric_column_references.side_effect = lambda expr, *args, **kwargs: expr
    mock_translator._build_safe_sum_sql.side_effect = lambda expr, *args, **kwargs: f"SUM({expr}::FLOAT)"
    
    builder = SemanticViewBuilder(
        config=snowflake_config,
        behavior=connector_behavior,
        identifier_sanitizer=IdentifierSanitizer(),
        live_schema_metadata={},
        schema_manager=mock_schema_manager,
        translator=mock_translator
    )


    
    ddls = builder.generate_ddls_from_osi(osi_model)
    
    # 3. Assertions
    assert len(ddls) >= 1
    semantic_view_ddl = ddls[-1]
    
    # Verify TABLES clause
    assert "TABLES (" in semantic_view_ddl
    assert 'SALES AS "TEST_DB"."TEST_SCHEMA"."SALES"' in semantic_view_ddl
    
    # Verify DIMENSIONS clause
    assert "DIMENSIONS (" in semantic_view_ddl
    assert 'SALES."REVENUE" AS SALES."REVENUE"' in semantic_view_ddl
    assert 'SALES."QUANTITY" AS SALES."QUANTITY"' in semantic_view_ddl
    
    # Verify METRICS clause
    assert "METRICS (" in semantic_view_ddl
    assert 'SALES."TOTAL_REVENUE" AS' in semantic_view_ddl


