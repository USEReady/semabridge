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
        assert "measure_aliases" in result
        assert result["measure_aliases"] == []

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
        assert result["measure_aliases"] == []

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
        assert pm[0] == {"measure": "Revenue", "title": "Total Revenue", "page": "Executive Dashboard", "visual_type": "Card"}
        assert pm[1] == {"measure": "Revenue", "title": "Monthly Revenue", "page": "Executive Dashboard", "visual_type": "BarChart"}
        assert pm[2] == {"measure": "Revenue", "title": "Revenue Trend", "page": "Executive Dashboard", "visual_type": "LineChart"}

        ma = result["measure_aliases"]
        assert len(ma) == 1
        assert ma[0]["measure"] == "Revenue"
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
        assert pm[0] == {"measure": "Revenue", "title": "Total Revenue", "page": "Page A", "visual_type": "Card"}
        assert pm[1] == {"measure": "Revenue", "title": "Total Revenue", "page": "Page B", "visual_type": "Card"}

        ma = result["measure_aliases"]
        assert len(ma) == 1
        assert ma[0]["measure"] == "Revenue"
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

        ma = result["measure_aliases"]
        assert len(ma) == 1
        assert ma[0]["measure"] == "Revenue"
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
            assert item["measure"] == "Revenue"

        ma = result["measure_aliases"]
        assert len(ma) == 1
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
            "measure": "Revenue",
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
                            # query is malformed JSON, filters is valid
                            "query": "malformed JSON {",
                            "filters": json.dumps({
                                "Measure": {"Property": "Revenue"}
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
        assert pm[0]["measure"] == "Revenue"
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
            assert item["measure"] == f"Measure_{measure_index}"
            assert item["title"] == f"Alias_{i}"

        ma = result["measure_aliases"]
        assert len(ma) == 50
        # Check that the aliases for each measure are collected correctly
        for item in ma:
            measure_num = int(item["measure"].split("_")[1])
            expected_aliases = [f"Alias_{measure_num}", f"Alias_{measure_num + 50}"]
            assert item["aliases"] == expected_aliases

        elapsed = end_time - start_time
        print(f"Stress test completed in {elapsed:.4f} seconds")

