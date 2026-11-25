"""Phase 3.4: Unity Catalog table matching, inline stub plans, real column
names in measure SQL (Step 6b) and the settings that drive them.

Offline: Unity Catalog is faked; matching uses the Snowflake path's resolver
unchanged. The stub form (inline SQL vs physical table) is not decided here:
a stub is only a plan entry (columns + types); nothing is created.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from semabridge.core.engine.exceptions import ConversionError
from semabridge.target_backends.databricks.tables import (
    LIVE_SCHEMA_UNAVAILABLE,
    TableMatchingError,
    UcTable,
    parse_source_schemas,
    plan_tables,
    read_unity_catalog,
    rewrite_measure_columns,
)


def col(name, dtype="string", calculated=False, expr=None):
    return SimpleNamespace(unique_name=name, data_type=SimpleNamespace(value=dtype), is_calculated=calculated,
                           source_expression=expr)


def dataset(name, *cols):
    return SimpleNamespace(unique_name=name, columns=list(cols))


def model(datasets, metrics=()):
    return SimpleNamespace(unique_name="Store Sales", label="Store Sales", datasets=list(datasets),
                           metrics=[SimpleNamespace(unique_name=f"m{i}", sql_expression=s) for i, s in enumerate(metrics)])


def uc(catalog, schema, name, *columns, ttype="MANAGED"):
    t = UcTable(catalog, schema, name, ttype)
    for c in columns:
        t.columns[c.upper()] = (c, "string")
    return t


def meta(*tables):
    out: Dict[Any, Dict[str, UcTable]] = {}
    for t in tables:
        out.setdefault((t.catalog, t.schema), {})[t.name.upper()] = t
    return out


SALES = dataset("Sales", col("LocationID", "integer"), col("ScenarioID", "integer"),
                col("Sum_Regular_Sales_Dollars", "float"), col("Region"))
SRC = [("workspace", "sb_tests")]


# --- settings ---------------------------------------------------------------------------


def test_parse_source_schemas() -> None:
    assert parse_source_schemas("workspace.sb_tests, other", "workspace", "dev") == [("workspace", "sb_tests"), ("workspace", "other")]
    assert parse_source_schemas(["a.b", "a.b", "`c`.`d`"], None, None) == [("a", "b"), ("c", "d")]
    assert parse_source_schemas(None, "workspace", "semabridge_dev") == [("workspace", "semabridge_dev")]
    assert parse_source_schemas("", None, None) == []


# --- reading Unity Catalog ------------------------------------------------------------------


class FakeClient:
    def __init__(self, tables, columns):
        self.sql: List[str] = []
        self._answers = [tables, columns]

    def execute(self, sql):
        self.sql.append(sql)
        return SimpleNamespace(rows=self._answers[len(self.sql) - 1])


def test_read_unity_catalog_skips_our_own_objects() -> None:
    client = FakeClient(
        tables=[
            {"table_schema": "sb_tests", "table_name": "sales", "table_type": "MANAGED"},
            {"table_schema": "SB_TESTS", "table_name": "store_sales_dbx__sbstg_ab12", "table_type": "VIEW"},
            {"table_schema": "sb_tests", "table_name": "store_sales_dbx", "table_type": "METRIC_VIEW"},
        ],
        columns=[
            {"table_schema": "sb_tests", "table_name": "sales", "column_name": "LocationID", "data_type": "int"},
            {"table_schema": "sb_tests", "table_name": "store_sales_dbx", "column_name": "x", "data_type": "int"},
        ],
    )
    out = read_unity_catalog(client, SRC)
    assert list(out[("workspace", "sb_tests")]) == ["SALES"]
    assert out[("workspace", "sb_tests")]["SALES"].columns == {"LOCATIONID": ("LocationID", "int")}
    assert all(s.startswith("SELECT") and "information_schema" in s for s in client.sql)
    assert "'sb_tests'" in client.sql[0]


# --- matching ---------------------------------------------------------------------------------


def test_binds_same_name_case_insensitively_with_real_column_names() -> None:
    table = uc("workspace", "sb_tests", "sales", "locationid", "scenarioid", "sum_regular_sales_dollars", "region")
    plan = plan_tables(model([SALES]), meta(table), source_schemas=SRC)
    b = plan.bindings[0]
    assert (b.action, b.table.full_name, b.alias) == ("bind", "workspace.sb_tests.sales", "SALES")
    assert b.column_map["SUM_REGULAR_SALES_DOLLARS"] == "sum_regular_sales_dollars"
    assert b.missing_columns == []


def test_binds_by_columns_to_a_differently_named_table() -> None:
    table = uc("workspace", "sb_tests", "fact_store_sales", "LocationID", "ScenarioID", "Sum_Regular_Sales_Dollars", "Region")
    b = plan_tables(model([SALES]), meta(table), source_schemas=SRC).bindings[0]
    assert b.action == "bind" and b.table.name == "fact_store_sales"


def test_same_name_but_columns_differ_becomes_a_stub_with_a_note() -> None:
    table = uc("workspace", "sb_tests", "sales", "order_id", "product_id", "amount")
    plan = plan_tables(model([SALES]), meta(table), source_schemas=SRC)
    assert plan.bindings[0].action == "stub"
    assert any("exists but only" in w and "workspace.sb_tests.sales" in w for w in plan.warnings)


def test_stub_columns_have_spark_types_and_numeric_aggregates_are_never_strings() -> None:
    sql = ["SUM(SALES.`REGION`::DOUBLE)", "COUNT(DISTINCT SALES.`LOCATIONID`)"]
    plan = plan_tables(model([SALES], sql), {}, source_schemas=SRC)
    stub = dict(plan.bindings[0].stub_columns)
    # naming v2: stub columns get lowercase snake_case names; the converter's references map to them
    assert stub == {"location_id": "BIGINT", "scenario_id": "BIGINT", "sum_regular_sales_dollars": "DOUBLE", "region": "DOUBLE"}
    assert plan.bindings[0].column_map["LOCATIONID"] == "location_id"
    assert plan.rewrite_map()["SALES"]["REGION"] == "region"


def test_search_order_first_schema_wins() -> None:
    a = uc("workspace", "first", "sales", "LocationID", "ScenarioID", "Sum_Regular_Sales_Dollars", "Region")
    b = uc("workspace", "second", "sales", "LocationID", "ScenarioID", "Sum_Regular_Sales_Dollars", "Region")
    plan = plan_tables(model([SALES]), meta(a, b), source_schemas=[("workspace", "first"), ("workspace", "second")])
    assert plan.bindings[0].table.schema == "first"
    plan = plan_tables(model([SALES]), meta(a, b), source_schemas=[("workspace", "second"), ("workspace", "first")])
    assert plan.bindings[0].table.schema == "second"


def test_ambiguous_match_stops() -> None:
    """Two tables that both look like 'Sales' (same letters) and match on columns."""
    cols = ("LocationID", "ScenarioID", "Sum_Regular_Sales_Dollars", "Region")
    a = uc("workspace", "sb_tests", "s_ales", *cols)
    b = uc("workspace", "sb_tests", "sa_les", *cols)
    with pytest.raises(TableMatchingError, match="Ambiguous table binding"):
        plan_tables(model([SALES]), meta(a, b), source_schemas=SRC)


def test_column_only_ties_with_unrelated_names_count_as_missing() -> None:
    """Same rule as Snowflake: equal column matches with no name similarity
    are not guessed between; the table counts as missing (stub)."""
    cols = ("LocationID", "ScenarioID", "Sum_Regular_Sales_Dollars", "Region")
    a = uc("workspace", "sb_tests", "fact_a", *cols)
    b = uc("workspace", "sb_tests", "fact_b", *cols)
    assert plan_tables(model([SALES]), meta(a, b), source_schemas=SRC).bindings[0].action == "stub"


def test_explicit_mapping_wins_and_a_missing_one_is_reported() -> None:
    target = uc("workspace", "sales_data", "fact", "LocationID")
    other = uc("workspace", "sb_tests", "sales", "LocationID", "ScenarioID", "Sum_Regular_Sales_Dollars", "Region")
    plan = plan_tables(model([SALES]), meta(target, other), source_schemas=SRC,
                       table_mapping={"Sales": "workspace.sales_data.fact"})
    b = plan.bindings[0]
    assert (b.table.full_name, b.matched_by) == ("workspace.sales_data.fact", "mapping")
    assert "SCENARIOID" not in b.column_map and "ScenarioID" in b.missing_columns

    plan = plan_tables(model([SALES]), meta(other), source_schemas=SRC, table_mapping={"Sales": "workspace.x.nope"})
    assert any("workspace.x.nope was not found" in w for w in plan.warnings)
    assert plan.bindings[0].action == "bind"  # falls back to normal matching


def test_on_missing_source_modes() -> None:
    assert plan_tables(model([SALES]), {}, source_schemas=SRC, on_missing_source="skip").bindings[0].action == "skip"
    with pytest.raises(TableMatchingError, match="on_missing_source=fail"):
        plan_tables(model([SALES]), {}, source_schemas=SRC, on_missing_source="fail")
    with pytest.raises(TableMatchingError, match="must be one of"):
        plan_tables(model([SALES]), {}, source_schemas=SRC, on_missing_source="create")


def test_datasets_sharing_a_cleaned_name_are_reported() -> None:
    plan = plan_tables(model([dataset("Sales Fact", col("a")), dataset("Sales_Fact", col("b"))]), {}, source_schemas=SRC)
    assert any("SALES_FACT" in w for w in plan.warnings)


def test_plan_dict_for_the_dry_run_panel() -> None:
    table = uc("workspace", "sb_tests", "sales", "locationid", "scenarioid", "sum_regular_sales_dollars", "region")
    store = dataset("Store", col("StoreType"))
    d = plan_tables(model([SALES, store]), meta(table), source_schemas=SRC).as_dict()
    assert d["platform"] == "databricks" and d["source_schemas"] == ["workspace.sb_tests"]
    actions = {t["dataset"]: t["action"] for t in d["tables"]}
    assert actions == {"Sales": "bind", "Store": "stub"}
    stub = next(t for t in d["tables"] if t["dataset"] == "Store")
    assert stub["stub_columns"] == [{"name": "store_type", "type": "STRING"}]


# --- measure SQL rewrite ----------------------------------------------------------------------


def test_rewrite_only_known_columns_of_bound_tables() -> None:
    rmap = {"SALES": {"SUM_REGULAR_SALES_DOLLARS": "sum_regular_sales_dollars", "ODD": "odd`name"}}
    sql = ("SUM(SALES.`SUM_REGULAR_SALES_DOLLARS`::DOUBLE) / NULLIF(SUM(STORE.`SUM_REGULAR_SALES_DOLLARS`), 0)"
           " + MAX(SALES.`ODD`) + MAX(SALES.`UNKNOWN`)")
    assert rewrite_measure_columns(sql, rmap) == (
        "SUM(SALES.`sum_regular_sales_dollars`::DOUBLE) / NULLIF(SUM(STORE.`SUM_REGULAR_SALES_DOLLARS`), 0)"
        " + MAX(SALES.`odd``name`) + MAX(SALES.`UNKNOWN`)"
    )
    assert rewrite_measure_columns(None, rmap) is None
    assert rewrite_measure_columns("SUM(X)", {}) == "SUM(X)"


# --- Step 6b in the engine --------------------------------------------------------------------


def _context(metrics: List[str], target: Dict[str, Any]):
    return SimpleNamespace(
        sml_model=model([SALES], metrics),
        config=SimpleNamespace(target=SimpleNamespace(**target)),
        project_id="p", run_id="r", table_binding_plan=None, databricks_table_plan=None,
    )


def _engine():
    from semabridge.core.execution_engine import ExecutionEngine

    return ExecutionEngine()


@pytest.fixture(autouse=True)
def _step6b_in_temp_folder(request, monkeypatch, tmp_path):
    """Step 6b writes the view plan under output/; keep test runs out of the repo."""
    if "step6b" in request.node.name:
        monkeypatch.chdir(tmp_path)


def test_step6b_without_a_connection_reports_unavailable(monkeypatch) -> None:
    from semabridge.core.engine.targets import databricks as step

    monkeypatch.setattr(step, "_databricks_connection_for_run", lambda ctx: None)
    ctx = _context(["SUM(SALES.`SUM_REGULAR_SALES_DOLLARS`::DOUBLE)"], {})
    _engine()._step6b_databricks_tables(ctx)
    assert "table plan unavailable" in ctx.table_binding_plan["warnings"][0]
    assert ctx.sml_model.metrics[0].sql_expression == "SUM(SALES.`SUM_REGULAR_SALES_DOLLARS`::DOUBLE)"


def test_step6b_runs_the_planner_and_writes_the_plan(monkeypatch, tmp_path) -> None:
    from semabridge.core.engine.targets import databricks as step

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(step, "_databricks_connection_for_run", lambda ctx: None)
    ctx = _context(["SUM(SALES.`SUM_REGULAR_SALES_DOLLARS`::DOUBLE)"], {})
    _engine()._step6b_databricks_tables(ctx)
    assert ctx.databricks_view_plan["platform"] == "databricks"
    out = tmp_path / "output" / "databricks" / "p"
    assert (out / "databricks_view_plan.json").exists()
    assert (out / "databricks_view_plan.md").read_text(encoding="utf-8").startswith("# Databricks metric-view plan")


def _connected(monkeypatch, catalog_meta=None, error=None):
    from semabridge.core.engine.targets import databricks as step
    from semabridge.target_backends.databricks import tables
    from semabridge.target_backends.databricks.auth import DatabricksConnectionInfo

    info = DatabricksConnectionInfo(host="dbc-1.cloud.databricks.com", token="t", warehouse_id="w",
                                    catalog="workspace", schema="semabridge_dev")
    monkeypatch.setattr(step, "_databricks_connection_for_run", lambda ctx: info)

    def fake_read(client, schemas):
        if error:
            raise error
        return catalog_meta or {}

    monkeypatch.setattr(tables, "read_unity_catalog", fake_read)


def test_step6b_rewrites_measures_and_sets_the_plan(monkeypatch) -> None:
    table = uc("workspace", "sb_tests", "sales", "locationid", "scenarioid", "sum_regular_sales_dollars", "region")
    _connected(monkeypatch, meta(table))
    ctx = _context(["SUM(SALES.`SUM_REGULAR_SALES_DOLLARS`::DOUBLE)"], {"source_schemas": "workspace.sb_tests"})
    _engine()._step6b_databricks_tables(ctx)
    assert ctx.sml_model.metrics[0].sql_expression == "SUM(SALES.`sum_regular_sales_dollars`::DOUBLE)"
    assert ctx.table_binding_plan["tables"][0]["table"] == "workspace.sb_tests.sales"
    assert ctx.databricks_table_plan.bindings[0].action == "bind"


def test_step6b_stops_when_unity_catalog_cannot_be_read(monkeypatch) -> None:
    from semabridge.target_backends.databricks.client import DatabricksSqlError

    _connected(monkeypatch, error=DatabricksSqlError("HTTP 403"))
    with pytest.raises(ConversionError, match=LIVE_SCHEMA_UNAVAILABLE):
        _engine()._step6b_databricks_tables(_context([], {}))


def test_step6b_stops_on_missing_tables_when_fail(monkeypatch) -> None:
    _connected(monkeypatch, {})
    with pytest.raises(ConversionError, match="on_missing_source=fail"):
        _engine()._step6b_databricks_tables(_context([], {"on_missing_source": "fail"}))


# --- on a real model ---------------------------------------------------------------------------

PBIX = Path(__file__).resolve().parents[2] / "Examples" / "Competitive Marketing Analysis (More Synonyms) (1).pbix"


@pytest.fixture
def hermetic(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if any(m in key.upper() for m in ("OPENAI", "GEMINI", "FEATHERLESS", "ANTHROPIC", "LLM", "API_KEY")):
            monkeypatch.delenv(key, raising=False)
    from semabridge.dax_translation.tier5.service import Tier5Service

    monkeypatch.setattr(Tier5Service, "translate", lambda self, request: None)
    monkeypatch.setattr(Tier5Service, "translate_batch", lambda self, requests: [None] * len(requests))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(OSError("network blocked")))


def test_real_model_binds_and_every_bound_reference_uses_real_names(hermetic) -> None:
    """Competitive Marketing converted with the Databricks rules; a fake Unity
    Catalog holds every table with lower-case column names."""
    import re

    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.core.source_format import from_pbix_tmsl
    from semabridge.target_backends.databricks.tables import physical_columns
    from semabridge.target_rules import DATABRICKS_RULES
    from semabridge.utils.naming import sanitize_column

    if not PBIX.exists():
        pytest.skip("Competitive Marketing .pbix not available")
    connector = LocalPBIXConnector({"pbix_path": str(PBIX)})
    discovered = connector.discover()
    tmsl = connector.extract() if discovered.get("raw_tmsl") is None else discovered["raw_tmsl"]
    sf = from_pbix_tmsl(project_id="g", run_id="g", tmsl=tmsl, pbix_path=str(PBIX))
    osi = TMSLToOSIConverter().to_osi({"tmsl": sf.tmsl_definition, "workspace_id": "local", "dataset_id": "g",
                                       "display_name": "g", "project_id": "g", "field_aliases": [],
                                       "source_file": None, "infer_relationships": None})
    sml = OSIToSMLConverter(rules=DATABRICKS_RULES).from_osi(osi, row_counts=sf.row_counts)

    tables = []
    for ds in sml.datasets:
        cols = [sanitize_column(c.unique_name).lower() for c in physical_columns(ds)]
        if cols:
            tables.append(uc("workspace", "sb_tests", ds.unique_name.lower(), *cols))
    plan = plan_tables(sml, meta(*tables), source_schemas=SRC)
    bound = {b.alias.upper() for b in plan.bindings if b.action == "bind"}
    assert bound, "tables bind"
    rmap = plan.rewrite_map()
    for m in sml.metrics:
        out = rewrite_measure_columns(m.sql_expression, rmap)
        for alias, name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\.`((?:[^`]|``)+)`", out or ""):
            if alias.upper() in bound and name.upper() in rmap[alias.upper()]:
                assert name == name.lower(), (m.unique_name, alias, name)
