"""Databricks naming single-rule tests.

Verifies:
1. Shared rule in ``utils/identifiers.py`` matches ``DatabricksPublisher``'s
   current naming methods (``_sanitize_identifier``, ``_normalize_metric_identifier``,
   and view-name construction) on a comprehensive table of tricky names.
2. Backend outputs match ``frontend/src/utils/databricksNamingTable.json``
   (also asserted on the JS side by ``frontend/src/utils/databricksNaming.test.js``).
3. ``project_mapping_engine._default_target_identifier`` and ``_validate_target_name``
   use the shared rule for Databricks targets.
4. ``GET /api/naming/preview`` supports ``target=databricks``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.utils.identifiers import (
    DATABRICKS_MAX_IDENTIFIER_LENGTH,
    DATABRICKS_RESERVED_WORDS,
    databricks_sanitize_identifier,
    databricks_target_name,
    databricks_view_name,
    format_databricks_fqn,
    is_databricks_reserved_word,
    quote_databricks_identifier,
)

_TABLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "utils"
    / "databricksNamingTable.json"
)


def _table():
    return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))


_TRICKY_NAMES = [
    "Date",
    "date",
    "Order",
    "Select",
    "Sales %",
    "% Margin",
    "Margin %",
    "2024 Sales",
    "Café Owner",
    "Région",
    "  Sales  Amount ",
    "Sales-Amt (USD)",
    "Net $ Rev",
    "A & B",
    "Q?",
    "x*y+z=1",
    "#Count",
    "# Orders",
    "e-mail@domain",
    "O'Brien",
    '"Quoted"',
    "`Backticked`",
    "a`b",
    "---",
    "日本語",
    "Units 😀 Sold",
    "a.b",
    "[Sales].[Amount]",
    "Tab\tName",
    "Sales_",
    "__x__",
    "9",
    "",
]


def _bare_publisher() -> DatabricksPublisher:
    """Create a DatabricksPublisher instance without touching network/config."""
    return object.__new__(DatabricksPublisher)


@pytest.mark.parametrize("name", _TRICKY_NAMES)
def test_shared_rule_matches_publisher_column_names(name):
    pub = _bare_publisher()
    assert databricks_target_name(name, entity_kind="column") == pub._sanitize_identifier(name)
    assert databricks_sanitize_identifier(name) == pub._sanitize_identifier(name)


@pytest.mark.parametrize("name", _TRICKY_NAMES)
def test_shared_rule_matches_publisher_metric_names(name):
    pub = _bare_publisher()
    assert databricks_target_name(name, entity_kind="metric") == pub._normalize_metric_identifier(name)


def test_databricks_view_name_matches_publisher_conventions():
    pub = _bare_publisher()
    model = "2024 Sales & Ops"
    ds = "Fact-Orders (USD)"

    # Standard metric view: {prefix}_{safe_model}_{safe_ds}
    expected_std = f"{pub._sanitize_identifier('mv')}_{pub._sanitize_identifier(model)}_{pub._sanitize_identifier(ds)}"
    assert databricks_view_name(model, ds) == expected_std

    # Per-fact metric view: {safe_model}_fact_{safe_ds}_metric_view
    expected_fact = f"{pub._sanitize_identifier(model)}_fact_{pub._sanitize_identifier(ds)}_metric_view"
    assert databricks_view_name(model, ds, per_fact=True) == expected_fact

    # Model-level identifier
    assert databricks_view_name(model) == pub._sanitize_identifier(model)


def test_databricks_max_length_255():
    long_name = "x" * 300
    assert len(databricks_target_name(long_name, entity_kind="column")) == 255
    assert len(databricks_target_name(long_name, entity_kind="metric")) == 255
    assert len(databricks_view_name("m" * 200, "d" * 200)) == 255


def test_databricks_backtick_quoting_and_reserved_words():
    assert quote_databricks_identifier("Order") == "`Order`"
    assert quote_databricks_identifier("a`b") == "`a``b`"
    assert format_databricks_fqn("main", "semabridge", "mv_sales") == "`main`.`semabridge`.`mv_sales`"
    assert is_databricks_reserved_word("SELECT") is True
    assert is_databricks_reserved_word("`order`") is True
    assert is_databricks_reserved_word("CustomerName") is False


@pytest.mark.parametrize("kind", ["column", "metric"])
def test_backend_matches_pinned_databricks_table(kind):
    for name, expected in _table()[kind].items():
        assert databricks_target_name(name, entity_kind=kind) == expected, (kind, name)


def test_pinned_databricks_table_pins_reserved_words_and_max_length():
    table = _table()
    assert table["reserved_words"] == sorted(DATABRICKS_RESERVED_WORDS)
    assert table["max_length"] == DATABRICKS_MAX_IDENTIFIER_LENGTH == 255


def test_default_target_identifier_for_databricks():
    from semabridge.api.services.project_mapping_engine import (
        _default_target_identifier,
        _validate_target_name,
    )

    for name in ("Date", "Order", "Sales %", "# Orders", "2024 Sales", "Customer Name"):
        assert _default_target_identifier(name, "databricks", "column") == databricks_target_name(
            name, entity_kind="column"
        )
        assert _default_target_identifier(name, "databricks", "metric") == databricks_target_name(
            name, entity_kind="metric"
        )

    ok = _validate_target_name(
        target_name="Sales_Pct",
        source_name="Sales %",
        collision_detected=False,
        target_connector="databricks",
        entity_kind="metric",
    )
    assert ok["validation_status"] == "valid"

    bad_chars = _validate_target_name(
        target_name="Sales %",
        source_name="Sales %",
        collision_detected=False,
        target_connector="databricks",
        entity_kind="metric",
    )
    assert bad_chars["validation_code"] == "UNSUPPORTED_CHARACTERS"
    assert bad_chars["suggested_target_name"] == "Sales_Pct"

    too_long = _validate_target_name(
        target_name="a" * 260,
        source_name="a" * 260,
        collision_detected=False,
        target_connector="databricks",
        entity_kind="column",
    )
    assert too_long["validation_code"] == "MAX_LENGTH_EXCEEDED"
    assert len(too_long["suggested_target_name"]) == 255


def test_naming_preview_endpoint_supports_databricks():
    from semabridge.api.routers.naming_router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    assert client.get(
        "/api/naming/preview",
        params={"name": "Sales %", "kind": "column", "target": "databricks"},
    ).json() == {"name": "Sales"}
    assert client.get(
        "/api/naming/preview",
        params={"name": "Sales %", "kind": "metric", "target": "databricks"},
    ).json() == {"name": "Sales_Pct"}
    assert client.get(
        "/api/naming/preview",
        params={"name": "# Orders", "kind": "metric", "target": "databricks"},
    ).json() == {"name": "Num_Orders"}
    assert client.get(
        "/api/naming/preview",
        params={"name": "Date", "kind": "column", "target": "databricks"},
    ).json() == {"name": "Date"}
    assert client.get(
        "/api/naming/preview",
        params={"name": "x", "kind": "column", "target": "unknown"},
    ).status_code == 422


@pytest.fixture(autouse=True)
def _publisher_naming(monkeypatch):
    """These tests describe the existing publisher's Databricks naming, used
    when behavior.databricks.use_planner_path is off."""
    from semabridge.target_rules import naming

    monkeypatch.setattr(naming, "databricks_planner_on", lambda: False)
    import semabridge.api.services.project_mapping_engine as pme

    monkeypatch.setattr(pme, "databricks_planner_on", lambda: False)
