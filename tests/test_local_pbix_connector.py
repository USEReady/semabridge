"""
Test suite for the local PBIX connector.

Tests the .pbix ZIP archive parsing, DataModelSchema extraction,
Connections.json parsing, and composite model resolution.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict

import pytest

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.core.exceptions import PBIXParsingError


# ---------------------------------------------------------------------------
# Fixtures — create realistic in-memory .pbix archives
# ---------------------------------------------------------------------------

def _create_data_model_schema() -> Dict[str, Any]:
    """Create a minimal but realistic DataModelSchema JSON."""
    return {
        "name": "TestSemanticModel",
        "description": "A test model for unit tests",
        "compatibilityLevel": 1550,
        "culture": "en-US",
        "tables": [
            {
                "name": "Sales",
                "description": "Sales fact table",
                "isHidden": False,
                "columns": [
                    {
                        "name": "OrderID",
                        "dataType": "int64",
                        "isHidden": False,
                        "sourceColumn": "OrderID",
                        "type": "data",
                    },
                    {
                        "name": "Amount",
                        "dataType": "decimal",
                        "isHidden": False,
                        "sourceColumn": "Amount",
                        "type": "data",
                    },
                ],
                "measures": [
                    {
                        "name": "Total Revenue",
                        "expression": "SUM(Sales[Amount])",
                        "formatString": "#,##0.00",
                        "description": "Sum of all sales",
                        "isHidden": False,
                        "displayFolder": "Measures",
                    }
                ],
                "partitions": [
                    {
                        "name": "Sales-Partition",
                        "source": {
                            "type": "m",
                            "expression": [
                                "let",
                                "    Source = Snowflake.Databases(\"account.snowflakecomputing.com\")",
                                "in",
                                "    Source"
                            ],
                        },
                    }
                ],
            },
            {
                "name": "Customer",
                "columns": [
                    {
                        "name": "CustomerID",
                        "dataType": "int64",
                        "sourceColumn": "CustomerID",
                    },
                    {
                        "name": "Name",
                        "dataType": "string",
                        "sourceColumn": "Name",
                    },
                ],
                "measures": [],
                "partitions": [],
            },
        ],
        "relationships": [
            {
                "name": "Sales_Customer",
                "fromTable": "Sales",
                "fromColumn": "CustomerID",
                "toTable": "Customer",
                "toColumn": "CustomerID",
                "crossFilteringBehavior": "oneDirection",
                "isActive": True,
                "fromCardinality": "many",
                "toCardinality": "one",
            }
        ],
    }


def _create_connections_json() -> Dict[str, Any]:
    """Create a realistic Connections.json for composite models."""
    return {
        "Connections": [
            {
                "Name": "UpstreamFinanceModel",
                "ConnectionString": "pbiservice://api.powerbi.com/v1.0/myorg/groups/12345678-abcd-ef01-1234-567890abcdef",
                "Provider": "MSOLAP",
            },
            {
                "Name": "ExternalHRModel",
                "ConnectionString": "powerbi://api.powerbi.com/v1.0/myorg/groups/abcdef12-3456-7890-abcd-ef1234567890",
                "Provider": "MSOLAP",
            },
        ]
    }


@pytest.fixture
def pbix_file(tmp_path: Path) -> Path:
    """Create a realistic .pbix (ZIP) archive with DataModelSchema."""
    pbix_path = tmp_path / "test_model.pbix"
    with zipfile.ZipFile(str(pbix_path), "w", zipfile.ZIP_DEFLATED) as zf:
        schema_json = json.dumps(_create_data_model_schema())
        zf.writestr("DataModelSchema", schema_json)

        connections_json = json.dumps(_create_connections_json())
        zf.writestr("Connections", connections_json)

    return pbix_path


@pytest.fixture
def pbix_no_schema(tmp_path: Path) -> Path:
    """Create a .pbix archive WITHOUT DataModelSchema."""
    pbix_path = tmp_path / "empty_model.pbix"
    with zipfile.ZipFile(str(pbix_path), "w") as zf:
        zf.writestr("Layout", "{}")
    return pbix_path


@pytest.fixture
def corrupt_file(tmp_path: Path) -> Path:
    """Create a non-ZIP file pretending to be .pbix."""
    pbix_path = tmp_path / "corrupt.pbix"
    pbix_path.write_text("This is not a ZIP file")
    return pbix_path


# ---------------------------------------------------------------------------
# Authentication Tests
# ---------------------------------------------------------------------------

class TestPBIXAuthentication:
    """Test file validation (our 'authentication' for offline connector)."""

    def test_authenticate_valid_pbix(self, pbix_file: Path) -> None:
        """Should accept a valid .pbix ZIP archive."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()  # Should not raise

    def test_authenticate_missing_file(self, tmp_path: Path) -> None:
        """Should raise PBIXParsingError for missing files."""
        connector = LocalPBIXConnector({"pbix_path": str(tmp_path / "missing.pbix")})
        with pytest.raises(PBIXParsingError, match="not found"):
            connector.authenticate()

    def test_authenticate_corrupt_file(self, corrupt_file: Path) -> None:
        """Should raise PBIXParsingError for non-ZIP files."""
        connector = LocalPBIXConnector({"pbix_path": str(corrupt_file)})
        with pytest.raises(PBIXParsingError, match="not a valid ZIP"):
            connector.authenticate()


# ---------------------------------------------------------------------------
# Discovery / Extraction Tests
# ---------------------------------------------------------------------------

class TestPBIXDiscovery:
    """Test semantic model extraction from .pbix archives."""

    def test_discover_extracts_tables(self, pbix_file: Path) -> None:
        """Should extract table definitions from DataModelSchema."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert len(result["tables"]) == 2
        sales = next(t for t in result["tables"] if t["name"] == "Sales")
        assert len(sales["columns"]) == 2
        assert sales["columns"][0]["name"] == "OrderID"

    def test_discover_extracts_measures(self, pbix_file: Path) -> None:
        """Should extract DAX measures from tables."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert len(result["measures"]) == 1
        measure = result["measures"][0]
        assert measure["name"] == "Total Revenue"
        assert "SUM" in measure["expression"]
        assert measure["table"] == "Sales"

    def test_discover_extracts_relationships(self, pbix_file: Path) -> None:
        """Should extract model relationships with cardinality."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert len(result["relationships"]) == 1
        rel = result["relationships"][0]
        assert rel["from_table"] == "Sales"
        assert rel["to_table"] == "Customer"
        assert "many-to-one" in rel["cardinality"]

    def test_discover_extracts_connections(self, pbix_file: Path) -> None:
        """Should extract external connection references (composite model)."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert len(result["connections"]) == 2
        first_conn = result["connections"][0]
        assert first_conn["name"] == "UpstreamFinanceModel"
        assert first_conn["type"] == "live_connect"
        assert first_conn["external_model_id"] is not None

    def test_discover_extracts_m_code(self, pbix_file: Path) -> None:
        """Should extract M Code (Power Query) expressions."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert len(result["m_code"]) == 1
        m = result["m_code"][0]
        assert m["table"] == "Sales"
        assert "Snowflake" in m["expression"]

    def test_discover_handles_missing_schema(self, pbix_no_schema: Path) -> None:
        """Should gracefully handle missing DataModelSchema."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_no_schema)})
        connector.authenticate()
        result = connector.discover()

        assert result["tables"] == []
        assert result["measures"] == []
        assert result["relationships"] == []

    def test_discover_includes_metadata(self, pbix_file: Path) -> None:
        """Should include file-level metadata in the result."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert result["metadata"]["source"] == "local_pbix"
        assert result["metadata"]["file_size_bytes"] > 0

    def test_discover_model_info(self, pbix_file: Path) -> None:
        """Should extract model-level information."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()

        assert len(result["models"]) == 1
        model = result["models"][0]
        assert model["name"] == "TestSemanticModel"
        assert model["compatibility_level"] == 1550

    def test_discover_uses_pbixray_fallback_when_json_parse_fails(self, pbix_file: Path, monkeypatch) -> None:
        """Should use pbixray fallback when DataModelSchema JSON parsing fails."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()

        def _raise_schema_error() -> Dict[str, Any]:
            raise PBIXParsingError("json parse failed", pbix_path=str(pbix_file))

        def _fallback_result() -> Dict[str, Any]:
            return {
                "models": [{"name": "FallbackModel", "description": "", "compatibility_level": 1600, "culture": "en-US"}],
                "tables": [{"name": "FactSales", "description": "", "is_hidden": False, "columns": [], "partitions": []}],
                "measures": [],
                "relationships": [],
                "m_code": [],
            }

        monkeypatch.setattr(connector, "_extract_data_model_schema", _raise_schema_error)
        monkeypatch.setattr(connector, "_extract_with_pbixray", _fallback_result)

        result = connector.discover()

        assert result["models"][0]["name"] == "FallbackModel"
        assert result["tables"][0]["name"] == "FactSales"
        assert result["metadata"]["parser"] == "pbixray"


# ---------------------------------------------------------------------------
# Connection Classification Tests
# ---------------------------------------------------------------------------

class TestConnectionClassification:
    """Test connection type classification helpers."""

    def test_classify_live_connect_pbi(self) -> None:
        """Should classify pbiservice:// as live_connect."""
        result = LocalPBIXConnector._classify_connection_type(
            "pbiservice://api.powerbi.com/v1.0/myorg/groups/12345"
        )
        assert result == "live_connect"

    def test_classify_live_connect_powerbi(self) -> None:
        """Should classify powerbi:// as live_connect."""
        result = LocalPBIXConnector._classify_connection_type(
            "powerbi://api.powerbi.com/v1.0/myorg"
        )
        assert result == "live_connect"

    def test_classify_direct_query(self) -> None:
        """Should classify Provider=MSOLAP as direct_query."""
        result = LocalPBIXConnector._classify_connection_type(
            "Provider=MSOLAP;Data Source=server"
        )
        assert result == "direct_query"

    def test_classify_unknown(self) -> None:
        """Should classify unrecognized strings as unknown."""
        result = LocalPBIXConnector._classify_connection_type(
            "some-other-provider://host"
        )
        assert result == "unknown"

    def test_extract_model_guid(self) -> None:
        """Should extract GUID from a Power BI connection string."""
        result = LocalPBIXConnector._extract_model_guid(
            "pbiservice://api.powerbi.com/v1.0/myorg/groups/12345678-abcd-ef01-1234-567890abcdef"
        )
        assert result == "12345678-abcd-ef01-1234-567890abcdef"

    def test_extract_model_guid_none(self) -> None:
        """Should return None when no GUID is present."""
        result = LocalPBIXConnector._extract_model_guid("no-guid-here")
        assert result is None
