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

    def test_extract_returns_fabric_like_raw_tmsl(self, pbix_file: Path) -> None:
        """Should expose the PBIX semantic model as a Fabric-like TMSL payload."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        raw_tmsl = connector.extract()

        assert "model" in raw_tmsl
        assert raw_tmsl["model"]["name"] == "TestSemanticModel"
        assert len(raw_tmsl["model"]["tables"]) == 2

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
        assert result["raw_tmsl"]["model"]["name"] == "TestSemanticModel"
        assert "\"model\"" in result["raw_tmsl_json"]

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
        assert result["raw_tmsl"]["model"]["name"] == "FallbackModel"
        assert len(result["raw_tmsl"]["model"]["tables"]) == 1
        assert result["raw_tmsl"]["model"]["tables"][0]["name"] == "FactSales"


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


class TestPBIXPresentationMetadata:
    """Test suite for report layout parsing and presentation metadata extraction."""

    def test_discover_defaults_when_layout_missing(self, pbix_file: Path) -> None:
        """Should return empty lists for new metadata keys if layout is absent."""
        connector = LocalPBIXConnector({"pbix_path": str(pbix_file)})
        connector.authenticate()
        result = connector.discover()
        assert "presentation_metadata" in result
        assert result["presentation_metadata"] == []
        assert "field_aliases" in result
        assert result["field_aliases"] == []

    def test_discover_malformed_layout(self, tmp_path: Path) -> None:
        """Should log a warning and continue if layout exists but is malformed."""
        pbix_path = tmp_path / "malformed_layout.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", "not a JSON string{")

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()
        assert result["presentation_metadata"] == []
        assert result["field_aliases"] == []

    def test_multiple_visuals_same_measure_and_order(self, tmp_path: Path) -> None:
        """Verify multiple visual aliases are extracted preserving discovery order."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Executive Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {
                                        "title": [
                                            {
                                                "properties": {
                                                    "text": {
                                                        "expr": {"Literal": {"Value": "'Total Revenue'"}}
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "barChart",
                                    "objects": {
                                        "title": [
                                            {
                                                "properties": {
                                                    "text": {
                                                        "expr": {"Literal": {"Value": "'Monthly Revenue'"}}
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "lineChart",
                                    "vcObjects": {
                                        "title": [
                                            {
                                                "properties": {
                                                    "text": {
                                                        "value": "Revenue Trend"
                                                    }
                                                }
                                            }
                                        ]
                                    }
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "multiple_visuals.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 3
        assert pm[0] == {"field": "Revenue", "field_type": "measure", "table": None, "title": "Total Revenue", "page": "Executive Dashboard", "visual_type": "Card"}
        assert pm[1] == {"field": "Revenue", "field_type": "measure", "table": None, "title": "Monthly Revenue", "page": "Executive Dashboard", "visual_type": "BarChart"}
        assert pm[2] == {"field": "Revenue", "field_type": "measure", "table": None, "title": "Revenue Trend", "page": "Executive Dashboard", "visual_type": "LineChart"}

        ma = result["field_aliases"]
        assert len(ma) == 1
        assert ma[0]["field"] == "Revenue"
        assert ma[0]["field_type"] == "measure"
        assert ma[0]["aliases"] == ["Total Revenue", "Monthly Revenue", "Revenue Trend"]

    def test_deduplication_and_page_independence(self, tmp_path: Path) -> None:
        """Verify page independence in presentation_metadata and deduplication in measure_aliases."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Page A",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        # Duplicate visual on same page
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        }
                    ]
                },
                {
                    "displayName": "Page B",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "dedup.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        # Duplicate on Page A is deduplicated, but Page B remains distinct
        assert len(pm) == 2
        assert pm[0] == {"field": "Revenue", "field_type": "measure", "table": None, "title": "Total Revenue", "page": "Page A", "visual_type": "Card"}
        assert pm[1] == {"field": "Revenue", "field_type": "measure", "table": None, "title": "Total Revenue", "page": "Page B", "visual_type": "Card"}

        ma = result["field_aliases"]
        assert len(ma) == 1
        assert ma[0]["field"] == "Revenue"
        assert ma[0]["aliases"] == ["Total Revenue"]  # Deduplicated across pages

    def test_exclude_measure_name_from_aliases(self, tmp_path: Path) -> None:
        """Verify that visual titles matching the measure name are excluded from aliases."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "exclude_measure_name.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 3

        ma = result["field_aliases"]
        assert len(ma) == 1
        assert ma[0]["field"] == "Revenue"
        # Only "Total Revenue" remains, "Revenue" and "revenue" are excluded
        assert ma[0]["aliases"] == ["Total Revenue"]

    def test_normalization_formats(self, tmp_path: Path) -> None:
        """Verify normalization of different measure reference structures."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Alias 1"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "[Revenue]"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Alias 2"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Sales[Revenue]"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Alias 3"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"queryRef": "Sales.Revenue"}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Alias 4"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"queryRef": "Model.Sales.Revenue"}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "normalization.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 4
        for item in pm:
            assert item["field"] == "Revenue"
            # First two came from an explicit Measure node ("measure"); the
            # last two are bare queryRef matches with no adjacent node to
            # type them ("unknown") — both still resolve to the same
            # field_aliases entry below since "unknown" is folded into the
            # measure bucket (measure names are globally unique, so this is
            # always safe).
            assert item["field_type"] in ("measure", "unknown")

        ma = result["field_aliases"]
        assert len(ma) == 1
        assert ma[0]["field"] == "Revenue"
        assert ma[0]["field_type"] == "measure"
        assert ma[0]["aliases"] == ["Alias 1", "Alias 2", "Alias 3", "Alias 4"]

    def test_optional_fields_and_empty_titles(self, tmp_path: Path) -> None:
        """Verify that visuals with empty titles are skipped and optional fields default properly."""
        layout_dict = {
            "sections": [
                {
                    # Page name is empty/missing
                    "visualContainers": [
                        # Visual with empty title (should be skipped)
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": ""}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        # Visual with missing title key (should be skipped)
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card"
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        # Visual with missing type and page (should parse with defaults)
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Valid Title"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "optional.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 1
        assert pm[0] == {
            "field": "Revenue",
            "field_type": "measure",
            "table": None,
            "title": "Valid Title",
            "page": "Unknown",
            "visual_type": "Unknown"
        }

    def test_resiliency_malformed_json_fields(self, tmp_path: Path) -> None:
        """Malformed JSON fields inside a visual container should not fail discovery."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Resiliency Page",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Partial Success"}}}]}
                                }
                            }),
                            # query is malformed JSON, filters is valid and
                            # carries its field reference inside a Select
                            # clause, matching real PBIX query shape.
                            "query": "malformed JSON {",
                            "filters": json.dumps({
                                "Select": [{"Measure": {"Property": "Revenue"}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "resiliency.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 1
        assert pm[0]["field"] == "Revenue"
        assert pm[0]["title"] == "Partial Success"

    def test_large_layout_stress_test(self, tmp_path: Path) -> None:
        """Verify performance and scaling characteristics with a layout of 100 visuals and 50 measures."""
        import time

        visuals = []
        for i in range(100):
            measure_index = i % 50
            measure_name = f"Measure_{measure_index}"
            alias_name = f"Alias_{i}"
            visuals.append({
                "config": json.dumps({
                    "singleVisual": {
                        "visualType": "card",
                        "vcObjects": {"title": [{"properties": {"text": {"value": alias_name}}}]}
                    }
                }),
                "query": json.dumps({
                    "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": measure_name}}]}}]
                })
            })

        layout_dict = {
            "sections": [
                {
                    "displayName": "Stress Dashboard",
                    "visualContainers": visuals
                }
            ]
        }

        pbix_path = tmp_path / "stress.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            # Create a model with these 50 measures
            model_schema = _create_data_model_schema()
            zf.writestr("DataModelSchema", json.dumps(model_schema))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()

        start_time = time.perf_counter()
        result = connector.discover()
        end_time = time.perf_counter()

        pm = result["presentation_metadata"]
        assert len(pm) == 100
        for i, item in enumerate(pm):
            measure_index = i % 50
            assert item["field"] == f"Measure_{measure_index}"
            assert item["title"] == f"Alias_{i}"

        ma = result["field_aliases"]
        assert len(ma) == 50
        # Check that the aliases for each measure are collected correctly
        for item in ma:
            measure_num = int(item["field"].split("_")[1])
            expected_aliases = [f"Alias_{measure_num}", f"Alias_{measure_num + 50}"]
            assert item["aliases"] == expected_aliases

        elapsed = end_time - start_time
        print(f"Stress test completed in {elapsed:.4f} seconds")

    def test_column_bound_visual_produces_table_scoped_field_alias(self, tmp_path: Path) -> None:
        """A column-bound visual (Column node, not Measure) must produce a
        field_aliases entry with field_type='column' and its resolved
        source table, mirroring the real PBIX Select-item shape:
        {"Column": {"Expression": {"SourceRef": {"Source": "p"}}, "Property": "isVanArsdel"}}
        """
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Is Premium Product"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "p", "Entity": "Product", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "p"}}, "Property": "isPremium"}}],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "column_alias.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 1
        assert pm[0]["field"] == "isPremium"
        assert pm[0]["field_type"] == "column"
        assert pm[0]["table"] == "Product"

        fa = result["field_aliases"]
        assert len(fa) == 1
        assert fa[0]["field"] == "isPremium"
        assert fa[0]["field_type"] == "column"
        assert fa[0]["table"] == "Product"
        assert fa[0]["aliases"] == ["Is Premium Product"]

    def test_zero_column_bound_visuals_yields_empty_result_no_error(self, tmp_path: Path) -> None:
        """A layout with visuals present but none binding any column must
        produce an empty result — no error, no spurious column entries."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Revenue"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "no_columns.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        fa = result["field_aliases"]
        assert all(item["field_type"] != "column" for item in fa)

    def test_same_name_measure_and_column_produce_distinct_field_alias_entries(self, tmp_path: Path) -> None:
        """A measure and a column sharing a name (legal per TOM — measure
        names are model-global, column names are per-table) must produce
        two SEPARATE field_aliases entries, not one merged entry."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Column Alias Label"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "a", "Entity": "TableA", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "a"}}, "Property": "SharedName"}}],
                                }}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Measure Alias Label"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "b", "Entity": "TableB", "Type": 0}],
                                    "Select": [{"Measure": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": "SharedName"}}],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "measure_column_collision.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        fa = result["field_aliases"]
        assert len(fa) == 2
        column_entry = next(f for f in fa if f["field_type"] == "column")
        measure_entry = next(f for f in fa if f["field_type"] == "measure")
        assert column_entry["table"] == "TableA"
        assert column_entry["aliases"] == ["Column Alias Label"]
        assert measure_entry["aliases"] == ["Measure Alias Label"]

    def test_same_column_name_different_tables_produce_distinct_table_scoped_entries(self, tmp_path: Path) -> None:
        """Two different tables each having a column with the same name
        (legal — TMSL only requires column-name uniqueness within a table)
        must produce two SEPARATE, table-scoped field_aliases entries."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Alias For A"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "a", "Entity": "TableA", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "a"}}, "Property": "SharedName"}}],
                                }}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Alias For B"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "b", "Entity": "TableB", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": "SharedName"}}],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "column_column_collision.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        fa = result["field_aliases"]
        assert len(fa) == 2
        by_table = {f["table"]: f["aliases"] for f in fa}
        assert by_table["TableA"] == ["Alias For A"]
        assert by_table["TableB"] == ["Alias For B"]

    def test_column_with_unresolvable_table_still_extracted_with_none_table(self, tmp_path: Path) -> None:
        """A column-bound visual whose SourceRef alias isn't defined in any
        From entry must still be extracted (not dropped), with table=None —
        matching behavior confirmed against real PBIX data, where bare
        queryRef-style references never resolve a table either."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Unresolvable Alias"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "x", "Entity": "SomeOtherTable", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "z"}}, "Property": "OrphanColumn"}}],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "unresolvable_table.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        fa = result["field_aliases"]
        assert len(fa) == 1
        assert fa[0]["field"] == "OrphanColumn"
        assert fa[0]["field_type"] == "column"
        assert fa[0]["table"] is None
        assert fa[0]["aliases"] == ["Unresolvable Alias"]

    def test_single_field_card_title_becomes_alias_but_multi_field_chart_title_does_not(self, tmp_path: Path) -> None:
        """A card bound to exactly one measure legitimately inherits its
        title as an alias (e.g. a card titled "R1" bound only to "Revenue").
        A chart combining a measure with a dimension (e.g. titled
        "Total Sales by Channel", bound to both a "Total Sales" measure and
        a "Channel" column) must NOT propagate its title to either field —
        the title describes their combination, not one field alone."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        # Single-field card: title -> alias for Revenue only.
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "R1"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        # Multi-field bar chart: measure + dimension. Title
                        # must not become an alias for either field.
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "barChart",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Sales by Channel"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "c", "Entity": "Sales", "Type": 0}],
                                    "Select": [
                                        {"Measure": {"Property": "Total Sales"}},
                                        {"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "Channel"}},
                                    ],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "single_vs_multi_field.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        # Only the single-field card contributes an entry; the multi-field
        # chart's two field refs are both skipped.
        assert len(pm) == 1
        assert pm[0]["field"] == "Revenue"
        assert pm[0]["title"] == "R1"

        fa = result["field_aliases"]
        aliases_by_field = {(item["field"], item["field_type"]): item["aliases"] for item in fa}
        assert aliases_by_field.get(("Revenue", "measure")) == ["R1"]
        # Neither "Total Sales" nor "Channel" gets the chart title as an alias.
        assert ("Total Sales", "measure") not in aliases_by_field
        assert ("Channel", "column") not in aliases_by_field

    def test_unresolved_field_reference_flags_renamed_column_and_measure(self, tmp_path: Path) -> None:
        """A report can keep querying a field by a name that no longer
        exists in the model (the column/measure was renamed and the
        visual's saved query was never rewritten). This must be surfaced as
        an unresolved reference, scoped to its table where resolvable, and
        must NOT be raised for fields that still exist or for a
        table-less column reference (which can't be trusted)."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            # References "Discount" in Sales -- no such
                            # column exists (Sales has OrderID, Amount).
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Discount Slicer"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Discount"}}],
                                }}]
                            })
                        },
                        {
                            # References "Discount" again on a different page
                            # -- same unresolved key, count accumulates.
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Discount Card"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Discount"}}],
                                }}]
                            })
                        },
                        {
                            # References a measure that no longer exists.
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Old Measure Card"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Old Revenue Measure"}}]}}]
                            })
                        },
                        {
                            # References the real "Amount" column and real
                            # "Total Revenue" measure -- must NOT be flagged.
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Real Fields"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "s", "Entity": "Sales", "Type": 0}],
                                    "Select": [
                                        {"Column": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Amount"}},
                                        {"Measure": {"Property": "Total Revenue"}},
                                    ],
                                }}]
                            })
                        },
                        {
                            # Column reference with no resolvable table --
                            # must NOT be flagged (can't be trusted).
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Orphan Slicer"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "z"}}, "Property": "Untraceable"}}],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "unresolved_refs.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        unresolved = result["unresolved_report_field_references"]
        by_key = {(u["field"], u["field_type"], u["table"]): u for u in unresolved}

        assert by_key.get(("Discount", "column", "Sales"))["occurrences"] == 2
        # Sales only has OrderID/Amount -- neither resembles "Discount", so
        # no confident target is guessed rather than picking one at random.
        assert by_key.get(("Discount", "column", "Sales"))["resolved_target_field"] is None
        assert by_key.get(("Old Revenue Measure", "measure", None))["resolved_target_field"] is None
        assert by_key.get(("Old Revenue Measure", "measure", None))["occurrences"] == 1
        assert ("Amount", "column", "Sales") not in by_key
        assert ("Total Revenue", "measure", None) not in by_key
        assert ("Untraceable", "column", None) not in by_key
        assert len(unresolved) == 2

    def test_best_matching_field_only_resolves_when_unambiguous(self) -> None:
        """The name-similarity matcher behind resolved_target_field must
        resolve a clear single prefix match, but refuse to guess when zero
        or multiple candidates are equally plausible."""
        best_matching_field = LocalPBIXConnector._best_matching_field

        # "Ch" is an unambiguous prefix of "Channel" among these candidates.
        assert best_matching_field("Channel", ["Category", "Ch", "Sort"]) == "Ch"
        # No candidate resembles "Channel" at all.
        assert best_matching_field("Channel", ["Category", "Sort"]) is None
        # Two candidates both prefix-match -- genuinely ambiguous, no guess.
        assert best_matching_field("Channel", ["Ch", "Chan"]) is None
        # Works in the other direction too (candidate longer than stale name).
        assert best_matching_field("Mo", ["Month", "Year"]) == "Month"

    def test_unresolved_reference_resolves_to_single_matching_column(self, tmp_path: Path) -> None:
        """When exactly one column in the resolved table is name-similar to
        the stale reference, it must be surfaced as resolved_target_field
        -- and NOT be broadcast against unrelated columns in the same
        table that share no name resemblance."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "slicer",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Cust Slicer"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {
                                    "From": [{"Name": "c", "Entity": "Customer", "Type": 0}],
                                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "c"}}, "Property": "Cust"}}],
                                }}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "resolved_match.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        unresolved = result["unresolved_report_field_references"]
        assert len(unresolved) == 1
        entry = unresolved[0]
        assert entry["field"] == "Cust"
        assert entry["table"] == "Customer"
        # Customer has CustomerID and Name -- "Cust" is a prefix of
        # CustomerID only, so that's the sole confident match.
        assert entry["resolved_target_field"] == "CustomerID"

    def test_alias_claimed_by_two_different_fields_is_excluded_from_both(self, tmp_path: Path) -> None:
        """A caption that coincidentally lands on two different measures
        (e.g. copy-pasted cards, or a rename that left a stale duplicate)
        must NOT be kept as a synonym for either -- Cortex Analyst would
        have no way to tell which field a business user querying that term
        actually meant. Both fields must lose the alias, and the conflict
        must be reported in ambiguous_report_aliases."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Metric"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Revenue"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Metric"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Profit"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "ambiguous_alias.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        # Both fields' only alias was the ambiguous one -- neither keeps a
        # (now-empty) entry in field_aliases.
        assert result["field_aliases"] == []

        ambiguous = result["ambiguous_report_aliases"]
        assert len(ambiguous) == 1
        assert ambiguous[0]["alias"] == "Total Metric"
        assert ambiguous[0]["fields"] == [
            {"field": "Profit", "field_type": "measure", "table": None},
            {"field": "Revenue", "field_type": "measure", "table": None},
        ]

    def test_ambiguous_alias_excluded_case_insensitively_leaves_other_aliases_intact(
        self, tmp_path: Path
    ) -> None:
        """Collision detection must be case-insensitive (matching how
        Cortex Analyst/NL matching treats synonyms), and excluding the
        ambiguous alias must not disturb a field's OTHER, non-conflicting
        aliases."""
        layout_dict = {
            "sections": [
                {
                    "displayName": "Dashboard",
                    "visualContainers": [
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Total Metric"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Alpha"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "Extra Name"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Alpha"}}]}}]
                            })
                        },
                        {
                            "config": json.dumps({
                                "singleVisual": {
                                    "visualType": "card",
                                    "vcObjects": {"title": [{"properties": {"text": {"value": "total metric"}}}]}
                                }
                            }),
                            "query": json.dumps({
                                "Commands": [{"SemanticQuery": {"Select": [{"Measure": {"Property": "Beta"}}]}}]
                            })
                        }
                    ]
                }
            ]
        }

        pbix_path = tmp_path / "ambiguous_alias_case.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/Layout", json.dumps(layout_dict))

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        fa = result["field_aliases"]
        # Beta's only alias was the ambiguous one -- it disappears entirely.
        assert len(fa) == 1
        assert fa[0]["field"] == "Alpha"
        # "Total Metric" is stripped, but Alpha's other alias survives.
        assert fa[0]["aliases"] == ["Extra Name"]

        ambiguous = result["ambiguous_report_aliases"]
        assert len(ambiguous) == 1
        assert ambiguous[0]["alias"] == "Total Metric"
        assert ambiguous[0]["fields"] == [
            {"field": "Alpha", "field_type": "measure", "table": None},
            {"field": "Beta", "field_type": "measure", "table": None},
        ]

    def test_pbir_format_layout_extracts_the_same_aliases_as_legacy_layout(
        self, tmp_path: Path
    ) -> None:
        """Recent Power BI Desktop versions can save a report using the
        newer PBIR (enhanced report) format -- Report/definition/pages/
        with one page.json/visual.json per page/visual -- instead of a
        single Report/Layout file, even inside a plain .pbix container.
        The same single-field-visual title must still surface as a
        field_aliases entry via the PBIR fallback path."""
        pages_json = {"pageOrder": ["PageA"]}
        page_json = {"displayName": "Revenue Page"}
        visual_json = {
            "visual": {
                "visualType": "card",
                "query": {
                    "queryState": {
                        "Values": {
                            "projections": [
                                {
                                    "field": {
                                        "Measure": {
                                            "Expression": {"SourceRef": {"Entity": "Sales"}},
                                            "Property": "Total Revenue",
                                        }
                                    },
                                    "queryRef": "Sales.Total Revenue",
                                }
                            ]
                        }
                    }
                },
                "visualContainerObjects": {
                    "title": [
                        {"properties": {"text": {"expr": {"Literal": {"Value": "'Revenue Snapshot'"}}}}}
                    ]
                },
            }
        }

        pbix_path = tmp_path / "pbir_format.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/definition/pages/pages.json", json.dumps(pages_json))
            zf.writestr("Report/definition/pages/PageA/page.json", json.dumps(page_json))
            zf.writestr(
                "Report/definition/pages/PageA/visuals/v1/visual.json",
                json.dumps(visual_json),
            )

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        pm = result["presentation_metadata"]
        assert len(pm) == 1
        assert pm[0]["field"] == "Total Revenue"
        assert pm[0]["field_type"] == "measure"
        assert pm[0]["title"] == "Revenue Snapshot"
        assert pm[0]["page"] == "Revenue Page"

        fa = result["field_aliases"]
        assert len(fa) == 1
        assert fa[0]["field"] == "Total Revenue"
        assert fa[0]["aliases"] == ["Revenue Snapshot"]

    def test_pbir_format_multi_field_visual_is_not_trusted_for_aliasing(
        self, tmp_path: Path
    ) -> None:
        """The single-field-visual trust rule must apply identically under
        PBIR: a visual binding a Measure AND a Column (e.g. Category plus
        Values roles on a chart) must not attribute its title to either
        field, matching the legacy-format behavior."""
        pages_json = {"pageOrder": ["PageA"]}
        page_json = {"displayName": "Chart Page"}
        visual_json = {
            "visual": {
                "visualType": "barChart",
                "query": {
                    "queryState": {
                        "Category": {
                            "projections": [
                                {
                                    "field": {
                                        "Column": {
                                            "Expression": {"SourceRef": {"Entity": "Customer"}},
                                            "Property": "Name",
                                        }
                                    },
                                    "queryRef": "Customer.Name",
                                }
                            ]
                        },
                        "Values": {
                            "projections": [
                                {
                                    "field": {
                                        "Measure": {
                                            "Expression": {"SourceRef": {"Entity": "Sales"}},
                                            "Property": "Total Revenue",
                                        }
                                    },
                                    "queryRef": "Sales.Total Revenue",
                                }
                            ]
                        },
                    }
                },
                "visualContainerObjects": {
                    "title": [
                        {"properties": {"text": {"expr": {"Literal": {"Value": "'Revenue by Customer'"}}}}}
                    ]
                },
            }
        }

        pbix_path = tmp_path / "pbir_multi_field.pbix"
        with zipfile.ZipFile(str(pbix_path), "w") as zf:
            zf.writestr("DataModelSchema", json.dumps(_create_data_model_schema()))
            zf.writestr("Report/definition/pages/pages.json", json.dumps(pages_json))
            zf.writestr("Report/definition/pages/PageA/page.json", json.dumps(page_json))
            zf.writestr(
                "Report/definition/pages/PageA/visuals/v1/visual.json",
                json.dumps(visual_json),
            )

        connector = LocalPBIXConnector({"pbix_path": str(pbix_path)})
        connector.authenticate()
        result = connector.discover()

        assert result["presentation_metadata"] == []
        assert result["field_aliases"] == []

