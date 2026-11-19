"""Integration tests for Fabric (BIM) to Snowflake (Semantic View) synchronization."""

from __future__ import annotations

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
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


def test_sync_identity_bim_to_snowflake(sample_tmsl_json, snowflake_config, connector_behavior, monkeypatch):
    """
    Slice 1: Verify that a minimal Fabric BIM file correctly syncs to Snowflake DDL.
    Focus: TABLES and DIMENSIONS clauses for a single table.
    """
    # monkeypatch, not os.environ: the old direct write leaked into every
    # later test of the session.
    monkeypatch.setenv("SNOWFLAKE_SCHEMA", "TEST_SCHEMA")
    
    # 1. Convert TMSL to OSI
    converter = TMSLToOSIConverter()
    source_data = {
        "tmsl": sample_tmsl_json,
        "dataset_id": "SalesModel",
        "workspace_id": "test-workspace-id"
    }
    osi_model = converter.to_osi(source_data)
    
    # Use the real emitter's builder (real schema manager + DAX translator):
    # the MagicMock collaborators this test used to wire in by hand broke on
    # every new collaborator call (e.g. model= since 44ba7e0). No connection
    # is opened -- generate_ddls_from_osi only renders DDL.
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    builder = SnowflakeEmitter(config=snowflake_config, behavior=connector_behavior)

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

