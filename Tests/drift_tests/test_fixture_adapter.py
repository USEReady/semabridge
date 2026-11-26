"""Unit tests for RecordedFixtureAdapter and Mock mode registry integration."""

import os
from pathlib import Path
import pytest

from semabridge.governance.drift.adapters.fixture import RecordedFixtureAdapter
from semabridge.governance.drift.adapters.registry import get_adapter, is_mock_allowed


def test_fixture_adapter_initialization(tmp_path: Path):
    sample_file = tmp_path / "fact_view.sql"
    sample_file.write_text("CREATE OR REPLACE SEMANTIC VIEW fact_view TABLES (T AS DB.SCH.T) FACTS (T.VAL AS VAL);", encoding="utf-8")

    adapter = RecordedFixtureAdapter(platform="SNOWFLAKE", fixture_dir=str(tmp_path))
    assert adapter.platform == "SNOWFLAKE"
    assert adapter.platform_name == "FIXTURE_SNOWFLAKE"

    views = adapter.list_views("project_test")
    assert "fact_view" in views

    text = adapter.fetch_definition_text("fact_view")
    assert "CREATE OR REPLACE SEMANTIC VIEW" in text


def test_fixture_adapter_not_found(tmp_path: Path):
    adapter = RecordedFixtureAdapter(platform="SNOWFLAKE", fixture_dir=str(tmp_path))
    with pytest.raises(RuntimeError, match="Fixture definition not found"):
        adapter.fetch_definition_text("missing_view")


def test_fixture_adapter_parse_definition(tmp_path: Path):
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW fact_view
    TABLES (
        T AS DB.SCH.T
    )
    FACTS (
        T.AMOUNT AS AMOUNT
    )
    METRICS (
        T.SUM(AMOUNT) AS TOTAL_ITEMS
    );
    """
    sample_file = tmp_path / "fact_view.sql"
    sample_file.write_text(ddl, encoding="utf-8")

    adapter = RecordedFixtureAdapter(platform="SNOWFLAKE", fixture_dir=str(tmp_path))
    view_def = adapter.get_definition("fact_view")
    assert view_def.source == "fixture"
    assert view_def.view_name == "fact_view"
    assert len(view_def.objects) >= 1
    assert any(obj.name.upper() == "TOTAL_ITEMS" for obj in view_def.objects)


def test_fixture_adapter_databricks_yaml(tmp_path: Path):
    yaml_text = """version: 1.1
source: "catalog.schema.fact_table"
measures:
  - name: "total_items"
    expr: "sum(amount)"
"""
    sample_file = tmp_path / "metric_view.yaml"
    sample_file.write_text(yaml_text, encoding="utf-8")

    adapter = RecordedFixtureAdapter(platform="DATABRICKS", fixture_dir=str(tmp_path))
    view_def = adapter.get_definition("metric_view")
    assert view_def.source == "fixture"
    assert view_def.platform == "DATABRICKS"
    assert any(obj.name.lower() == "total_items" for obj in view_def.objects)


def test_registry_resolves_mock_mode():
    adapter = get_adapter("SNOWFLAKE", mode="mock")
    assert isinstance(adapter, RecordedFixtureAdapter)
    assert adapter.platform == "SNOWFLAKE"


def test_registry_raises_when_mock_disabled(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DRIFT_MOCK_ALLOWED", "false")
    assert not is_mock_allowed()
    with pytest.raises(PermissionError, match="Mock mode is disabled"):
        get_adapter("SNOWFLAKE", mode="mock")
