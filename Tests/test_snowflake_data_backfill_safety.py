"""Tests for the opt-in data-backfill feature (options.load_source_data).

Covers the acceptance criteria from the feature request directly:
  1. An empty/broken table (no real data in its non-key columns) is
     eligible for backfill, and the loaded columns use the REAL
     IdentifierSanitizer's output (including reserved-word remapping like
     Date -> COL_DATE and Order -> COL_ORDER), never a hand-rolled remap.
  2. A table that already has real data in even one non-key column is
     skipped entirely -- never truncated/overwritten.
  3. A failure loading one table (missing pbixray, a bad PBIX, a
     write_pandas failure) is reported as status="failed" for that table
     and never aborts the rest of the backfill pass or the deploy.
  4. A table's data state is never auto-decided when it's genuinely
     ambiguous (below the row-count floor, but carrying real-looking
     non-null values that aren't the known placeholder pattern) -- it
     lands in a distinct "ambiguous_needs_review" status with evidence
     (row count, which columns, sample values), not silently skipped or
     auto-backfilled either way.

No live Snowflake connection or real PBIX file is used -- cursor,
PBIXRay, and write_pandas are all faked/mocked, following the existing
no-live-connection pattern in test_snowflake_emitter_partial_deploy.py.
"""
from __future__ import annotations

import re
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import SMLColumn, SMLDataset, DataType
from semabridge.utils.identifiers import IdentifierSanitizer


def _build_schema_manager(connection_manager=None) -> SnowflakeSchemaManager:
    config = SnowflakeConfig(
        account="test.local", user="test_user", password="test_password",
        warehouse="test_wh", database="test_db", schema_name="test_schema",
        role="test_role",
    )
    behavior = ConnectorBehavior()
    identifier_sanitizer = IdentifierSanitizer()
    return SnowflakeSchemaManager(
        config=config,
        behavior=behavior,
        identifier_sanitizer=identifier_sanitizer,
        connection_manager=connection_manager or MagicMock(),
        dup_name_repo=None,
    )


def _product_dataset() -> SMLDataset:
    """Mirrors the real PRODUCT incident's shape: a key column (ProductID)
    plus several non-key columns, one of which ("Order") is a Snowflake
    reserved word -- the exact class of column the reference script's
    one-entry remap dict gets wrong."""
    return SMLDataset(
        unique_name="Product",
        label="Product",
        source_table="Product",
        columns=[
            SMLColumn(unique_name="ProductID", label="ProductID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="Product", label="Product", data_type=DataType.STRING),
            SMLColumn(unique_name="Manufacturer", label="Manufacturer", data_type=DataType.STRING),
            SMLColumn(unique_name="Order", label="Order", data_type=DataType.STRING),
        ],
    )


def _fake_cursor(row_result):
    """A MagicMock cursor whose fetchone() returns `row_result` for the
    safety-check query, and whose .connection is itself a MagicMock (what
    write_pandas would receive). Only suitable for classification outcomes
    that never need a follow-up sample-values query ("real_data", or
    "empty" via row_count==0 / all-null / above-floor-but-unpopulated) --
    use _fake_cursor_for_classification for anything that reaches the
    ambiguous/placeholder-check path."""
    cursor = MagicMock()
    cursor.fetchone.return_value = row_result
    cursor.connection = MagicMock()
    return cursor


class _RecordingConnectionManager:
    """Stands in for the real SnowflakeConnectionManager: records the SQL
    text it's asked to run onto the cursor, so a fake fetchall() can answer
    based on which columns a given query actually requested (needed once a
    test exercises more than one query shape, e.g. the COUNT safety-check
    query followed by a sample-values SELECT)."""

    @staticmethod
    def _execute_sql(cursor, sql, params=None, context=""):
        cursor._last_sql = sql


def _fake_cursor_for_classification(row_count, non_key_counts, sample_row_values=None):
    """Build a fake cursor that answers BOTH the COUNT safety-check query
    (fetchone) and any follow-up sample-values SELECT
    _classify_table_data_state/_is_known_placeholder_pattern may issue
    (fetchall), by inspecting the executed SQL text for which columns are
    being requested and looking each one up in `sample_row_values` (a
    single representative row -- real per-row detail doesn't matter here,
    only "does this column have a real, non-placeholder value").

    `non_key_counts` must be an ordered dict matching the exact
    non_key_safe_names order the code under test will use (dict insertion
    order = the order _collect_physical_source_columns produced them in).
    Must be paired with a schema manager built via
    _build_schema_manager(connection_manager=_RecordingConnectionManager()).
    """
    cursor = MagicMock()
    cursor.connection = MagicMock()
    cursor.fetchone.return_value = (row_count,) + tuple(non_key_counts.values())

    sample_row_values = sample_row_values or {}

    def fake_fetchall():
        sql = getattr(cursor, "_last_sql", "") or ""
        match = re.search(r"SELECT\s+(.*?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
        cols = re.findall(r'"([^"]+)"', match.group(1)) if match else []
        row = tuple(sample_row_values.get(c) for c in cols)
        return [row] * max(min(row_count, 3), 1)

    cursor.fetchall.side_effect = fake_fetchall
    return cursor


def _physical_cols(names_and_types) -> dict:
    """names_and_types: dict {safe_col_name: (DataType, original_unique_name)}
    or a plain list/tuple of names (defaults to STRING, unique_name=name)."""
    if isinstance(names_and_types, (list, tuple, set)):
        names_and_types = {n: (DataType.STRING, n) for n in names_and_types}
    return {
        name: SimpleNamespace(data_type=dtype, unique_name=uname)
        for name, (dtype, uname) in names_and_types.items()
    }


def _fake_pbix_model(df: pd.DataFrame):
    model = MagicMock()
    model.get_table.return_value = df
    return model


# ---------------------------------------------------------------------------
# 1. Classification: empty / all-non-key-null tables -> "empty"
# ---------------------------------------------------------------------------

def test_empty_table_is_classified_empty():
    sm = _build_schema_manager()
    cursor = _fake_cursor((0,))  # row_count=0, no non-key columns even queried
    physical_cols = _physical_cols(["PRODUCT", "MANUFACTURER"])
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])
    assert result["state"] == "empty"
    assert result["row_count"] == 0


def test_table_with_rows_but_all_non_key_columns_null_is_empty():
    sm = _build_schema_manager()
    # row_count=20 (above the floor), but every non-key column's non-null
    # COUNT is 0 -- exactly the real PRODUCT incident's shape for its
    # non-key columns.
    cursor = _fake_cursor((20, 0, 0))
    physical_cols = _physical_cols(["PRODUCT", "MANUFACTURER"])
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])
    assert result["state"] == "empty"
    assert result["row_count"] == 20


def test_single_all_null_garbage_row_is_empty_real_sales_incident_shape():
    """Reproduces the real live SALES table's exact observed state at the
    time this was reported: ROW_COUNT=1, every non-key column (PRODUCTID,
    STOREID, STATUS, ID, UNIT, AMOUNT, COL_DATE) = 0 non-null. Confidently
    empty (no populated columns at all) -- never ambiguous."""
    sm = _build_schema_manager()
    cursor = _fake_cursor((1, 0, 0, 0, 0, 0, 0))
    physical_cols = _physical_cols(["PRODUCTID", "STOREID", "STATUS", "ID", "UNIT", "AMOUNT", "COL_DATE"])
    result = sm._classify_table_data_state(
        cursor, "SALES", physical_cols, ["PRODUCTID", "STOREID", "STATUS", "ID", "UNIT", "AMOUNT", "COL_DATE"],
    )
    assert result["state"] == "empty"
    assert result["row_count"] == 1


def test_sparse_noise_in_large_table_is_empty_not_real_data():
    """A single stray non-null value out of hundreds of rows (manual test
    insert, partial/aborted prior load) must not block backfill just
    because SOME value exists somewhere -- caught by the fraction
    threshold, since row_count is well above the floor here."""
    sm = _build_schema_manager()
    cursor = _fake_cursor((500, 1, 0))  # 500 rows, only 1 non-null in PRODUCT (0.2%)
    physical_cols = _physical_cols(["PRODUCT", "MANUFACTURER"])
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])
    assert result["state"] == "empty"
    assert result["row_count"] == 500


# ---------------------------------------------------------------------------
# 2. Classification: real data (above the floor, above the fraction) -> "real_data"
# ---------------------------------------------------------------------------

def test_majority_populated_column_in_large_table_is_real_data():
    """The real STORE table's actual observed shape: 14 rows, several
    columns 100% populated -- must still be correctly recognized as real
    and left untouched (this refinement must not regress the original
    protection)."""
    sm = _build_schema_manager()
    cursor = _fake_cursor((14, 14, 14, 14, 14, 0))  # STOREID/STORE/TYPE/LONGITUDE/LATITUDE=14, IMAGE=0
    physical_cols = _physical_cols(["STOREID", "STORE", "TYPE", "LONGITUDE", "LATITUDE", "IMAGE"])
    result = sm._classify_table_data_state(
        cursor, "STORE", physical_cols, ["STOREID", "STORE", "TYPE", "LONGITUDE", "LATITUDE", "IMAGE"],
    )
    assert result["state"] == "real_data"
    assert result["row_count"] == 14


def test_table_with_majority_non_key_data_above_floor_is_real_data():
    sm = _build_schema_manager()
    # row_count=20 (above the floor), PRODUCT column 100% non-null -- must
    # never be touched, matching the real incident this feature exists to
    # prevent.
    cursor = _fake_cursor((20, 20, 0))
    physical_cols = _physical_cols(["PRODUCT", "MANUFACTURER"])
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])
    assert result["state"] == "real_data"
    assert result["row_count"] == 20


# ---------------------------------------------------------------------------
# 3. Classification: below the floor, real-looking (non-placeholder) data
#    -> "ambiguous" (never auto-decided either way)
# ---------------------------------------------------------------------------

def test_small_real_looking_row_below_floor_is_ambiguous():
    """A single row with real-looking (non-placeholder) values below the
    floor -- e.g. Corporate Spend's real "Range"/"Business Area" tables --
    must be neither auto-backfilled nor silently skipped. Evidence (which
    columns, sample values) must be present for a human to decide."""
    sm = _build_schema_manager(connection_manager=_RecordingConnectionManager())
    physical_cols = _physical_cols({
        "PRODUCT": (DataType.STRING, "Product"),
        "MANUFACTURER": (DataType.STRING, "Manufacturer"),
    })
    cursor = _fake_cursor_for_classification(
        row_count=1,
        non_key_counts={"PRODUCT": 1, "MANUFACTURER": 1},
        sample_row_values={"PRODUCT": "Maximus UC-01", "MANUFACTURER": "Acme"},
    )
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])

    assert result["state"] == "ambiguous"
    assert result["row_count"] == 1
    assert set(result["populated_columns"]) == {"PRODUCT", "MANUFACTURER"}
    assert result["samples"]["PRODUCT"] == ["Maximus UC-01"]
    assert result["samples"]["MANUFACTURER"] == ["Acme"]


def test_ambiguous_reason_text_includes_row_count_columns_and_samples():
    """The formatted reason a human reads in the run report must include
    enough detail to decide without re-running diagnostics -- row count,
    which columns, and the actual sample values."""
    sm = _build_schema_manager()
    reason = sm._format_ambiguous_reason(
        row_count=7,
        populated_columns=["BUSINESS_AREA"],
        samples={"BUSINESS_AREA": ["Marketing", "Sales"]},
    )
    assert "7" in reason
    assert "BUSINESS_AREA" in reason
    assert "Marketing" in reason
    assert "Sales" in reason
    assert "manual review" in reason.lower()


# ---------------------------------------------------------------------------
# 4. Classification: the known generate_sample_insert() placeholder
#    pattern is confidently garbage, not ambiguous -- still auto-heals.
# ---------------------------------------------------------------------------

def test_placeholder_sample_row_is_classified_empty_not_ambiguous():
    """generate_sample_insert()'s exact literal 'Sample_<column>'
    placeholder pattern is confidently garbage, not ambiguous, and must
    still auto-heal even though it's a single, fully-populated row below
    the floor -- the old binary check would have (wrongly) treated
    100%-non-null as obviously real data; the floor alone would have
    (also wrongly) made this indistinguishable from a genuinely small
    real table. The placeholder-pattern carve-out resolves both."""
    sm = _build_schema_manager(connection_manager=_RecordingConnectionManager())
    physical_cols = _physical_cols({
        "PRODUCT": (DataType.STRING, "Product"),
        "MANUFACTURER": (DataType.STRING, "Manufacturer"),
    })
    cursor = _fake_cursor_for_classification(
        row_count=1,
        non_key_counts={"PRODUCT": 1, "MANUFACTURER": 1},
        sample_row_values={"PRODUCT": "Sample_Product", "MANUFACTURER": "Sample_Manufacturer"},
    )
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])

    assert result["state"] == "empty"
    assert result["row_count"] == 1


def test_partial_placeholder_match_is_still_ambiguous():
    """If only SOME populated string columns match the placeholder
    pattern (the other has a real-looking value), this must NOT be
    confidently classified as the known placeholder -- stays ambiguous."""
    sm = _build_schema_manager(connection_manager=_RecordingConnectionManager())
    physical_cols = _physical_cols({
        "PRODUCT": (DataType.STRING, "Product"),
        "MANUFACTURER": (DataType.STRING, "Manufacturer"),
    })
    cursor = _fake_cursor_for_classification(
        row_count=1,
        non_key_counts={"PRODUCT": 1, "MANUFACTURER": 1},
        sample_row_values={"PRODUCT": "Sample_Product", "MANUFACTURER": "Acme"},
    )
    result = sm._classify_table_data_state(cursor, "PRODUCT", physical_cols, ["PRODUCT", "MANUFACTURER"])
    assert result["state"] == "ambiguous"


# ---------------------------------------------------------------------------
# 5. End-to-end _backfill_one_table: loads with REAL sanitized column names
# ---------------------------------------------------------------------------

def test_backfill_loads_empty_table_with_real_sanitized_column_names(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()

    # Safety check: empty table (row_count=0).
    cursor = _fake_cursor((0,))

    raw_df = pd.DataFrame({
        "Product": ["Maximus UC-01", "Pirum UC-05"],
        "Manufacturer": ["Acme", "Acme"],
        "Order": ["1", "2"],  # reserved word -- must become COL_ORDER, not ORDER
    })
    pbix_model = _fake_pbix_model(raw_df)

    live_schema_metadata = {"PRODUCT": {"PRODUCT", "MANUFACTURER", "COL_ORDER", "PRODUCTID"}}

    captured = {}

    def fake_write_pandas(conn, df, table_name, schema=None, auto_create_table=None, quote_identifiers=None, use_logical_type=None):
        captured["df"] = df
        captured["table_name"] = table_name
        captured["schema"] = schema
        captured["auto_create_table"] = auto_create_table
        return True, 1, len(df), None

    fake_module = types.SimpleNamespace(write_pandas=fake_write_pandas)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, live_schema_metadata)

    assert result["status"] == "loaded"
    assert result["row_count"] == 2
    assert result["table"] == "PRODUCT"

    # The real IdentifierSanitizer's actual output, not the reference
    # script's one-entry remap dict.
    loaded_columns = set(captured["df"].columns)
    assert loaded_columns == {"PRODUCT", "MANUFACTURER", "COL_ORDER"}
    assert captured["auto_create_table"] is False  # explicit, load-bearing per the plan
    pbix_model.get_table.assert_called_once_with("Product")


def test_backfill_narrows_date_columns_to_plain_date_objects(monkeypatch):
    """Regression test for a real bug found and fixed against a live load
    of Sales & Returns Sample's Date column: write_pandas stages a pandas
    datetime64 column as a raw variant value Snowflake's COPY INTO cannot
    implicitly cast into a strict DATE-typed target column -- first
    "Failed to cast variant value <ns-epoch> to DATE" (use_logical_type
    defaulted off), then still "Failed to cast variant value
    '<timestamp-with-00:00:00.000>' to DATE" even with
    use_logical_type=True, since a full timestamp string isn't a bare
    DATE string either. The fix narrows any column whose SML data_type is
    DataType.DATE (not DATETIME/TIMESTAMP) to plain Python date objects
    before staging, and passes use_logical_type=True to write_pandas."""
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="Sales", label="Sales", source_table="Sales",
        columns=[
            SMLColumn(unique_name="ID", label="ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="Amount", label="Amount", data_type=DataType.INTEGER),
            SMLColumn(unique_name="Date", label="Date", data_type=DataType.DATE),
        ],
    )
    cursor = _fake_cursor((0,))  # empty table

    raw_df = pd.DataFrame({
        "Amount": [40, 25],
        "Date": pd.to_datetime(["2019-01-13", "2019-01-14"]),  # real dtype PBIXRay returns
    })
    assert pd.api.types.is_datetime64_any_dtype(raw_df["Date"])  # sanity-check the fixture itself
    pbix_model = _fake_pbix_model(raw_df)
    live_schema_metadata = {"SALES": {"AMOUNT", "COL_DATE", "ID"}}

    captured = {}

    def fake_write_pandas(conn, df, table_name, schema=None, auto_create_table=None, quote_identifiers=None, use_logical_type=None):
        captured["df"] = df
        captured["use_logical_type"] = use_logical_type
        return True, 1, len(df), None

    fake_module = types.SimpleNamespace(write_pandas=fake_write_pandas)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, live_schema_metadata)

    assert result["status"] == "loaded"
    assert captured["use_logical_type"] is True

    loaded_date_col = captured["df"]["COL_DATE"]
    assert not pd.api.types.is_datetime64_any_dtype(loaded_date_col), (
        "DATE-typed column must be narrowed away from pandas datetime64 before staging"
    )
    import datetime as _dt
    assert all(isinstance(v, _dt.date) and not isinstance(v, _dt.datetime) for v in loaded_date_col), (
        "expected plain date objects, not datetime/Timestamp instances"
    )
    assert list(loaded_date_col) == [_dt.date(2019, 1, 13), _dt.date(2019, 1, 14)]


def test_backfill_auto_heals_placeholder_row_end_to_end(monkeypatch):
    """The placeholder carve-out exercised through the full public
    _backfill_one_table path, not just the classification function
    directly."""
    sm = _build_schema_manager(connection_manager=_RecordingConnectionManager())
    dataset = _product_dataset()

    cursor = _fake_cursor_for_classification(
        row_count=1,
        non_key_counts={"PRODUCT": 1, "MANUFACTURER": 1, "COL_ORDER": 1},
        sample_row_values={
            "PRODUCT": "Sample_Product",
            "MANUFACTURER": "Sample_Manufacturer",
            "COL_ORDER": "Sample_Order",
        },
    )

    raw_df = pd.DataFrame({"Product": ["Maximus UC-01"], "Manufacturer": ["Acme"], "Order": ["1"]})
    pbix_model = _fake_pbix_model(raw_df)
    live_schema_metadata = {"PRODUCT": {"PRODUCT", "MANUFACTURER", "COL_ORDER", "PRODUCTID"}}

    def fake_write_pandas(conn, df, table_name, schema=None, auto_create_table=None, quote_identifiers=None, use_logical_type=None):
        return True, 1, len(df), None

    fake_module = types.SimpleNamespace(write_pandas=fake_write_pandas)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, live_schema_metadata)

    assert result["status"] == "loaded"
    pbix_model.get_table.assert_called_once_with("Product")


def test_backfill_reports_ambiguous_needs_review_with_evidence(monkeypatch):
    """The full public _backfill_one_table path for a genuinely ambiguous
    table: never loaded, never silently skipped -- a distinct status with
    enough evidence in the reason text for a human to decide."""
    sm = _build_schema_manager(connection_manager=_RecordingConnectionManager())
    dataset = _product_dataset()

    cursor = _fake_cursor_for_classification(
        row_count=1,
        non_key_counts={"PRODUCT": 1, "MANUFACTURER": 0, "COL_ORDER": 0},
        sample_row_values={"PRODUCT": "Maximus UC-01"},
    )

    pbix_model = _fake_pbix_model(pd.DataFrame({"Product": ["should never be read"]}))
    write_pandas_called = MagicMock()
    fake_module = types.SimpleNamespace(write_pandas=write_pandas_called)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "ambiguous_needs_review"
    assert result["row_count"] == 1
    assert "PRODUCT" in result["reason"]
    assert "Maximus UC-01" in result["reason"]
    pbix_model.get_table.assert_not_called()
    write_pandas_called.assert_not_called()


# ---------------------------------------------------------------------------
# 6. End-to-end _backfill_one_table: already-populated table is skipped,
#    never truncated (write_pandas must never be called).
# ---------------------------------------------------------------------------

def test_backfill_skips_already_populated_table_without_touching_it(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()

    # Safety check: 20 rows (above the floor), PRODUCT column 100% non-null.
    cursor = _fake_cursor((20, 20, 0, 0))

    pbix_model = _fake_pbix_model(pd.DataFrame({"Product": ["should never be read"]}))

    write_pandas_called = MagicMock()
    fake_module = types.SimpleNamespace(write_pandas=write_pandas_called)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "skipped"
    assert result["row_count"] == 20
    assert "already has real data" in result["reason"]
    pbix_model.get_table.assert_not_called()
    write_pandas_called.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Failure isolation: one table's extraction raising doesn't crash the
#    whole backfill pass, and other datasets still get processed.
# ---------------------------------------------------------------------------

def test_one_table_failure_does_not_abort_the_rest(monkeypatch):
    sm = _build_schema_manager()
    broken_dataset = _product_dataset()
    healthy_dataset = SMLDataset(
        unique_name="Category",
        label="Category",
        source_table="Category",
        columns=[
            SMLColumn(unique_name="CategoryID", label="CategoryID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="Category", label="Category", data_type=DataType.STRING),
        ],
    )

    model = MagicMock()
    model.datasets = [broken_dataset, healthy_dataset]

    def fake_get_table(name):
        if name == "Product":
            raise RuntimeError("simulated corrupt table in PBIX")
        return pd.DataFrame({"Category": ["Electronics", "Furniture"]})

    pbix_model = MagicMock()
    pbix_model.get_table.side_effect = fake_get_table

    # Cursor always reports an empty table for the safety check.
    cursor = _fake_cursor((0,))

    def fake_write_pandas(conn, df, table_name, schema=None, auto_create_table=None, quote_identifiers=None, use_logical_type=None):
        return True, 1, len(df), None

    fake_module = types.SimpleNamespace(write_pandas=fake_write_pandas)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)

    # _maybe_backfill_source_data does a lazy `from pbixray import PBIXRay`
    # inside the function body -- patch the module it imports from directly.
    fake_pbixray_module = types.SimpleNamespace(PBIXRay=lambda path: pbix_model)
    monkeypatch.setitem(sys.modules, "pbixray", fake_pbixray_module)

    results = sm._maybe_backfill_source_data(cursor, model, "/fake/path.pbix", {})

    assert len(results) == 2
    by_dataset = {r["dataset"]: r for r in results}
    assert by_dataset["Product"]["status"] == "failed"
    assert "simulated corrupt table" in by_dataset["Product"]["reason"]
    assert by_dataset["Category"]["status"] == "loaded"
    assert by_dataset["Category"]["row_count"] == 2


# ---------------------------------------------------------------------------
# 8. Missing pbixray dependency is reported cleanly, not raised.
# ---------------------------------------------------------------------------

def test_missing_pbixray_dependency_is_reported_not_raised(monkeypatch):
    sm = _build_schema_manager()
    model = MagicMock()
    model.datasets = []

    monkeypatch.setitem(sys.modules, "pbixray", None)  # simulate ImportError

    results = sm._maybe_backfill_source_data(MagicMock(), model, "/fake/path.pbix", {})

    assert len(results) == 1
    assert results[0]["status"] == "failed"
    assert "pbixray" in results[0]["reason"].lower()
