"""Integration tests for progressive disclosure graph API endpoints.

Verifies that graph API endpoints return correct node and edge data at each
disclosure level: model-only, table-level, and column-level.

Progressive disclosure enables efficient rendering of large semantic models by
loading detail incrementally based on user interactions.
"""

from __future__ import annotations

import json
import pytest
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from semabridge.api.services.project_projects_impl import _snapshot_graph_payload


@pytest.fixture
def sample_snapshot_with_tables():
    """Create a mock snapshot with multiple tables and columns."""
    return MagicMock(
        snapshot_id="snap-123",
        project_id="test-project",
        sml_blob={
            "model_name": "SalesMetrics",
            "workspace_id": "workspace-1",
            "description": "Sales metrics model",
            "datasets": [
                {
                    "name": "FACT_SALES",
                    "table": "FACT_SALES",
                    "schema": "ANALYTICS",
                    "source_type": "table",
                    "columns": [
                        {"name": "SALE_ID", "data_type": "int64"},
                        {"name": "REVENUE", "data_type": "double"},
                        {"name": "QUANTITY", "data_type": "int64"},
                        {"name": "CUSTOMER_ID", "data_type": "int64"},
                        {"name": "DATE", "data_type": "date"},
                    ],
                },
                {
                    "name": "DIM_CUSTOMER",
                    "table": "DIM_CUSTOMER",
                    "schema": "ANALYTICS",
                    "source_type": "table",
                    "columns": [
                        {"name": "ID", "data_type": "int64"},
                        {"name": "NAME", "data_type": "string"},
                        {"name": "CITY", "data_type": "string"},
                        {"name": "COUNTRY", "data_type": "string"},
                    ],
                },
                {
                    "name": "DIM_PRODUCT",
                    "table": "DIM_PRODUCT",
                    "schema": "ANALYTICS",
                    "source_type": "table",
                    "columns": [
                        {"name": "ID", "data_type": "int64"},
                        {"name": "NAME", "data_type": "string"},
                        {"name": "CATEGORY", "data_type": "string"},
                    ],
                },
            ],
        },
    )


def test_progressive_disclosure_model_level_only():
    """Verify model-level disclosure returns only model node, no tables/columns.
    
    This is the coarsest disclosure level, used for rapid model list views
    where full lineage is not needed.
    """
    snapshot = MagicMock(
        snapshot_id="snap-100",
        project_id="proj-model-only",
        sml_blob={
            "model_name": "HighLevelMetrics",
            "workspace_id": "ws-1",
            "description": "Top-level metrics",
            "datasets": [],  # Empty datasets
        },
    )
    
    # Query with no tables or columns
    payload = _snapshot_graph_payload(
        snapshot,
        model_name="HighLevelMetrics",
        include_system_tables=False,
        include_column_lineage=False,
    )
    
    # Verify structure
    assert "nodes" in payload
    assert "edges" in payload
    
    # Should have exactly 1 node (model only)
    assert len(payload["nodes"]) == 1
    assert payload["nodes"][0]["type"] == "modelNode"
    assert payload["nodes"][0]["data"]["label"] == "HighLevelMetrics"
    assert payload["nodes"][0]["data"]["nodeType"] == "model"
    
    # Should have no edges
    assert len(payload["edges"]) == 0


def test_progressive_disclosure_table_level():
    """Verify table-level disclosure includes model and tables, but no columns.
    
    This is the typical level for understanding data flow across models and
    identifying shared tables and dependencies.
    """
    snapshot = MagicMock(
        snapshot_id="snap-101",
        project_id="proj-table-level",
        sml_blob={
            "model_name": "SalesModel",
            "workspace_id": "ws-2",
            "description": "Sales semantic model",
            "datasets": [
                {
                    "name": "FACT_SALES",
                    "table": "FACT_SALES",
                    "schema": "DATA",
                    "columns": [
                        {"name": "SALE_ID", "data_type": "int"},
                        {"name": "AMOUNT", "data_type": "decimal"},
                    ],
                },
                {
                    "name": "DIM_CUSTOMER",
                    "table": "DIM_CUSTOMER",
                    "schema": "DATA",
                    "columns": [
                        {"name": "CUST_ID", "data_type": "int"},
                        {"name": "CUST_NAME", "data_type": "string"},
                    ],
                },
            ],
        },
    )
    
    # Query without column lineage
    payload = _snapshot_graph_payload(
        snapshot,
        model_name="SalesModel",
        include_system_tables=False,
        include_column_lineage=False,
    )
    
    # Verify structure
    assert "nodes" in payload
    assert "edges" in payload
    
    # Should have 1 model node + 2 table nodes = 3 nodes
    assert len(payload["nodes"]) == 3
    
    # Verify node types
    node_types = [node["type"] for node in payload["nodes"]]
    assert node_types.count("modelNode") == 1
    assert node_types.count("tableNode") == 2
    assert node_types.count("columnNode") == 0  # No columns at this level
    
    # Should have edges from tables to model
    assert len(payload["edges"]) == 2
    for edge in payload["edges"]:
        assert edge["source"].startswith("table-")
        assert edge["target"].startswith("model-")
    
    # Verify table names in nodes
    table_names = [
        node["data"]["label"] for node in payload["nodes"]
        if node["type"] == "tableNode"
    ]
    assert "DATA.FACT_SALES" in table_names
    assert "DATA.DIM_CUSTOMER" in table_names


def test_progressive_disclosure_full_column_level(sample_snapshot_with_tables):
    """Verify column-level disclosure includes model, tables, and all columns.
    
    This is the full disclosure level used for detailed lineage inspection and
    impact analysis of specific column changes.
    """
    # Query with full column lineage
    payload = _snapshot_graph_payload(
        sample_snapshot_with_tables,
        model_name="SalesMetrics",
        include_system_tables=False,
        include_column_lineage=True,
    )
    
    # Verify structure
    assert "nodes" in payload
    assert "edges" in payload
    
    # Should have model + 3 tables + all columns
    node_types = [node["type"] for node in payload["nodes"]]
    assert node_types.count("modelNode") == 1
    assert node_types.count("tableNode") == 3
    assert node_types.count("columnNode") > 0
    
    # Verify column nodes exist for FACT_SALES
    column_nodes = [node for node in payload["nodes"] if node["type"] == "columnNode"]
    column_labels = [node["data"]["label"] for node in column_nodes]
    
    # FACT_SALES has 5 columns
    expected_columns = ["SALE_ID", "REVENUE", "QUANTITY", "CUSTOMER_ID", "DATE"]
    for col in expected_columns:
        # At least some columns should be present (lowercase for comparison)
        assert any(col.lower() in label.lower() for label in column_labels)
    
    # Verify edges include table-to-model and column-to-table
    table_to_model_edges = [e for e in payload["edges"] if e["source"].startswith("table-") and e["target"].startswith("model-")]
    
    assert len(table_to_model_edges) == 3  # 3 tables to model


def test_progressive_disclosure_system_tables_excluded():
    """Verify system tables are excluded when include_system_tables=False."""
    snapshot = MagicMock(
        snapshot_id="snap-102",
        project_id="proj-system-exclusion",
        sml_blob={
            "model_name": "SystemTestModel",
            "workspace_id": "ws-3",
            "datasets": [
                {
                    "name": "USER_DATA",
                    "table": "USER_DATA",
                    "schema": "PUBLIC",
                    "columns": [{"name": "ID", "data_type": "int"}],
                },
                {
                    "name": "pg_tables",
                    "table": "pg_tables",
                    "schema": "pg_catalog",
                    "columns": [],
                },
            ],
        },
    )
    
    # Query excluding system tables
    payload = _snapshot_graph_payload(
        snapshot,
        model_name="SystemTestModel",
        include_system_tables=False,
        include_column_lineage=False,
    )
    
    # Should have 1 model + 1 user table (pg_tables should be excluded)
    assert len(payload["nodes"]) >= 2  # At least model + user table
    
    table_nodes = [n for n in payload["nodes"] if n["type"] == "tableNode"]
    # Verify user table is present
    user_tables = [n for n in table_nodes if "USER_DATA" in n["data"]["label"]]
    assert len(user_tables) >= 1
    # Verify pg_tables is excluded
    pg_tables = [n for n in table_nodes if "pg_" in n["data"]["label"].lower()]
    assert len(pg_tables) == 0


def test_progressive_disclosure_system_tables_included():
    """Verify system tables are included when include_system_tables=True."""
    snapshot = MagicMock(
        snapshot_id="snap-103",
        project_id="proj-system-inclusion",
        sml_blob={
            "model_name": "DebugModel",
            "workspace_id": "ws-4",
            "datasets": [
                {
                    "name": "pg_tables",
                    "table": "pg_tables",
                    "schema": "pg_catalog",
                    "columns": [],
                },
            ],
        },
    )
    
    # Query including system tables
    payload = _snapshot_graph_payload(
        snapshot,
        model_name="DebugModel",
        include_system_tables=True,
        include_column_lineage=False,
    )
    
    # Should have 1 model + 1 system table
    assert len(payload["nodes"]) >= 2
    
    table_nodes = [n for n in payload["nodes"] if n["type"] == "tableNode"]
    assert any("pg_" in node["data"]["label"].lower() for node in table_nodes)


def test_progressive_disclosure_payload_size_reduction():
    """Verify payload size reduces significantly with reduced disclosure.
    
    Column-level disclosure adds substantial payload size for large models.
    This test verifies the payload size difference between disclosure levels.
    """
    snapshot = MagicMock(
        snapshot_id="snap-104",
        project_id="proj-size-test",
        sml_blob={
            "model_name": "LargeModel",
            "workspace_id": "ws-5",
            "datasets": [
                {
                    "name": f"TABLE_{i}",
                    "table": f"TABLE_{i}",
                    "schema": "DATA",
                    "columns": [
                        {"name": f"COL_{j}", "data_type": "string"}
                        for j in range(20)
                    ],
                }
                for i in range(10)
            ],
        },
    )
    
    # Table-level payload (without columns)
    table_level = _snapshot_graph_payload(
        snapshot,
        model_name="LargeModel",
        include_system_tables=False,
        include_column_lineage=False,
    )
    
    # Full column-level payload
    full_disclosure = _snapshot_graph_payload(
        snapshot,
        model_name="LargeModel",
        include_system_tables=False,
        include_column_lineage=True,
    )
    
    # Verify sizes and node counts
    assert len(full_disclosure["nodes"]) > len(table_level["nodes"])
    
    # Column-level should have significantly more nodes
    full_columns = len([n for n in full_disclosure["nodes"] if n["type"] == "columnNode"])
    assert full_columns == 200  # 10 tables * 20 columns
    
    # Table-level should have model + tables only
    table_level_tables = len([n for n in table_level["nodes"] if n["type"] == "tableNode"])
    assert table_level_tables == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
