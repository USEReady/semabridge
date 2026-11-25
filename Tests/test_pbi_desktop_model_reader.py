"""Unit tests for Power BI Desktop model reader replaying recorded fixtures."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from semabridge.sources.pbi_desktop.model_reader import (
    assemble_tmsl_from_entities,
    read_model_entities,
    read_tmsl_from_engine,
    _query_entity_with_fallback,
)

RECORDED_DIR = Path(__file__).resolve().parent.parent / "Scripts" / "poc_pbi_desktop" / "recorded"


@pytest.fixture
def recorded_fixtures():
    fixtures = {}
    for f in RECORDED_DIR.glob("*.json"):
        if f.stem in ("assembled_tmsl", "generated.osi", "generated.sml"):
            continue
        fixtures[f.stem] = json.loads(f.read_text(encoding="utf-8"))
    return fixtures


def test_assemble_tmsl_from_recorded_fixtures(recorded_fixtures):
    entities = {
        "tables": recorded_fixtures.get("tmschema_tables") or recorded_fixtures.get("info_tables"),
        "columns": recorded_fixtures.get("tmschema_columns") or recorded_fixtures.get("info_columns"),
        "measures": recorded_fixtures.get("tmschema_measures") or recorded_fixtures.get("info_measures"),
        "partitions": recorded_fixtures.get("tmschema_partitions") or recorded_fixtures.get("info_partitions"),
        "relationships": recorded_fixtures.get("tmschema_relationships") or recorded_fixtures.get("info_relationships"),
        "hierarchies": recorded_fixtures.get("tmschema_hierarchies", []),
        "levels": recorded_fixtures.get("tmschema_levels", []),
        "model": recorded_fixtures.get("tmschema_model", [{}]),
    }

    tmsl = assemble_tmsl_from_entities(entities)
    assert "model" in tmsl
    model = tmsl["model"]

    # Verify model tables
    tables = model["tables"]
    assert len(tables) > 0
    table_names = [t["name"] for t in tables]
    assert "Product" in table_names
    assert "Manufacturer" in table_names
    assert "SalesFact" in table_names

    # Auto-generated tables should be excluded
    for name in table_names:
        assert not name.startswith("LocalDateTable_")
        assert not name.startswith("DateTableTemplate_")

    # Tables are deterministically sorted
    assert table_names == sorted(table_names)

    # Verify relationships
    relationships = model["relationships"]
    assert len(relationships) > 0
    for rel in relationships:
        assert not rel["fromTable"].startswith("LocalDateTable_")
        assert not rel["toTable"].startswith("LocalDateTable_")
        assert "fromTable" in rel and "toTable" in rel
        assert "fromColumn" in rel and "toColumn" in rel
        assert "crossFilteringBehavior" in rel

    # Check Product table columns and hierarchies
    product_table = next(t for t in tables if t["name"] == "Product")
    prod_cols = {c["name"]: c for c in product_table["columns"]}
    assert "ProductID" in prod_cols
    assert prod_cols["ProductID"]["dataType"] == "int64"
    assert prod_cols["ProductID"].get("summarizeBy") == "none"

    assert "Product" in prod_cols
    assert prod_cols["Product"]["dataType"] == "string"

    # Hierarchies
    assert "hierarchies" in product_table
    hierarchies = product_table["hierarchies"]
    assert any(h["name"] == "ProductRollup" for h in hierarchies)


def test_query_entity_with_fallback():
    # When INFO succeeds, returns INFO rows
    exec_success = lambda q: [{"ID": 1, "Name": "TestTable"}] if "INFO" in q else []
    rows = _query_entity_with_fallback(exec_success, "EVALUATE INFO.TABLES()", "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES", "tables")
    assert len(rows) == 1
    assert rows[0]["Name"] == "TestTable"

    # When INFO raises an exception, falls back to TMSCHEMA
    def exec_fallback(q):
        if "INFO" in q:
            raise RuntimeError("INFO.TABLES() syntax error or unsupported")
        return [{"ID": 2, "Name": "FallbackTable"}]

    rows_fb = _query_entity_with_fallback(exec_fallback, "EVALUATE INFO.TABLES()", "SELECT * FROM $SYSTEM.TMSCHEMA_TABLES", "tables")
    assert len(rows_fb) == 1
    assert rows_fb[0]["Name"] == "FallbackTable"


def test_read_tmsl_from_engine_with_mocked_executor(recorded_fixtures):
    def mock_executor(query: str):
        if "TABLES" in query:
            return recorded_fixtures["info_tables"]
        elif "COLUMNS" in query:
            return recorded_fixtures["info_columns"]
        elif "MEASURES" in query:
            return recorded_fixtures["info_measures"]
        elif "PARTITIONS" in query:
            return recorded_fixtures["info_partitions"]
        elif "RELATIONSHIPS" in query:
            return recorded_fixtures["info_relationships"]
        elif "HIERARCHIES" in query:
            return recorded_fixtures.get("tmschema_hierarchies", [])
        elif "LEVELS" in query:
            return recorded_fixtures.get("tmschema_levels", [])
        elif "MODEL" in query:
            return recorded_fixtures.get("tmschema_model", [])
        return []

    tmsl = read_tmsl_from_engine(port=61470, database_name="test_db", query_executor=mock_executor)
    assert "model" in tmsl
    assert len(tmsl["model"]["tables"]) > 0
