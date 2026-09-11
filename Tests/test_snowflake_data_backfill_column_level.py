"""Tests for the column-level data-backfill fix.

Incident this covers: SEMABRIDGE_WORKSPACE.PRODUCT already had 2412 real
rows (SEGMENT/PRODUCT/PRODUCTID all correctly populated) when a newer,
wider PBIX schema introduced ISVANARSDEL/ISCOMPETEHIDE/ISCOMPETE as brand
new columns. _ensure_source_tables_exist ALTER-TABLE-ADD-COLUMN'd them
(unavoidably NULL for every existing row -- that's just SQL), and the
existing whole-table backfill safety check (_classify_table_data_state)
saw real data in SEGMENT/PRODUCT/PRODUCTID, classified the table
"real_data", and skipped the ENTIRE table -- silently leaving the
brand-new columns permanently null.

The fix: _ensure_source_tables_exist now records which columns it just
ALTER-TABLE-ADDed (self._newly_added_columns). When a table classifies
"real_data" overall but has one or more such columns still 100% null,
_backfill_columns_for_existing_rows patches ONLY those columns on the
existing rows via a keyed MERGE against a staged copy of the source PBIX
data -- never touching any other column, and refusing (ambiguous_needs_
review) rather than guessing whenever a safe key-based match isn't
possible.

Scoped narrowly, on purpose: a column that already existed before this
run and is (for whatever reason) still all-null is explicitly NOT covered
here, even though it looks identical from the table's data alone -- only
columns THIS deploy's _ensure_source_tables_exist call just added are
eligible. That's a separate, more ambiguous question this fix does not
answer.

No live Snowflake connection or real PBIX file is used -- cursor,
PBIXRay, and write_pandas are all faked/mocked, following the existing
no-live-connection pattern in test_snowflake_data_backfill_safety.py.
"""
from __future__ import annotations

import sys
import types
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
    """Mirrors the real PRODUCT incident: a key column (ProductID), two
    long-existing populated columns (Product, Segment), and one column
    that this run's _ensure_source_tables_exist just ALTER-TABLE-ADDed
    (IsVanArsdel)."""
    return SMLDataset(
        unique_name="Product",
        label="Product",
        source_table="Product",
        columns=[
            SMLColumn(unique_name="ProductID", label="ProductID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="Product", label="Product", data_type=DataType.STRING),
            SMLColumn(unique_name="Segment", label="Segment", data_type=DataType.STRING),
            SMLColumn(unique_name="IsVanArsdel", label="IsVanArsdel", data_type=DataType.STRING),
        ],
    )


class _RecordingConnectionManager:
    """Records every SQL statement executed (in order) so a test can
    inspect the exact MERGE/DROP text, and answers fetchone() from a
    caller-supplied queue keyed by call order (only the safety-check COUNT
    query needs a real answer here; MERGE/DROP/staging DDL don't)."""

    def __init__(self, fetchone_results=None):
        self.executed: list[str] = []
        self._fetchone_results = list(fetchone_results or [])

    def _execute_sql(self, cursor, sql, params=None, context=""):
        self.executed.append(sql)
        cursor._last_sql = sql


def _fake_cursor(fetchone_sequence):
    """fetchone_sequence: list of return values, one per fetchone() call
    in order (only the COUNT safety-check query calls fetchone here)."""
    cursor = MagicMock()
    cursor.fetchone.side_effect = list(fetchone_sequence)
    cursor.connection = MagicMock()
    cursor.rowcount = None
    return cursor


def _fake_pbix_model(df: pd.DataFrame):
    model = MagicMock()
    model.get_table.return_value = df
    return model


def _install_fake_write_pandas(monkeypatch, captured: dict, success=True):
    def fake_write_pandas(conn, df, table_name, schema=None, auto_create_table=None,
                           table_type=None, quote_identifiers=None, use_logical_type=None):
        captured["df"] = df
        captured["table_name"] = table_name
        captured["auto_create_table"] = auto_create_table
        captured["table_type"] = table_type
        return success, 1, len(df), None

    fake_module = types.SimpleNamespace(write_pandas=fake_write_pandas)
    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", fake_module)


# ---------------------------------------------------------------------------
# 1. _ensure_source_tables_exist records which columns it just added.
# ---------------------------------------------------------------------------

def test_ensure_source_tables_exist_records_newly_added_columns():
    sm = _build_schema_manager(connection_manager=_RecordingConnectionManager())
    dataset = _product_dataset()

    cursor = MagicMock()
    # 1) SHOW TABLES -> PRODUCT already exists.
    # 2) SHOW VIEWS -> none.
    # 3) DESC TABLE (via _verify_table_columns) -> missing ISVANARSDEL.
    cursor.fetchall.side_effect = [
        [(None, "PRODUCT")],
        [],
        [("PRODUCTID",), ("PRODUCT",), ("SEGMENT",)],
    ]

    sm._ensure_source_tables_exist(cursor, types_namespace(datasets=[dataset]))

    assert sm._newly_added_columns.get("PRODUCT") == {"ISVANARSDEL"}


def types_namespace(**kwargs):
    from types import SimpleNamespace
    return SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# 2. Core fix: a newly-added, still-empty column on an otherwise-populated
#    table gets patched via MERGE -- only that column, only on matching
#    existing rows.
# ---------------------------------------------------------------------------

def test_newly_added_empty_column_is_patched_via_merge_not_skipped(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()
    sm._newly_added_columns["PRODUCT"] = {"ISVANARSDEL"}

    # Safety check: 2412 rows, PRODUCT/SEGMENT fully populated, ISVANARSDEL 0.
    cursor = _fake_cursor([(2412, 2412, 2412, 0)])
    recorder = _RecordingConnectionManager()
    sm.connection_manager = recorder

    raw_df = pd.DataFrame({
        "ProductID": list(range(1, 2413)),
        "Product": [f"Item {i}" for i in range(1, 2413)],
        "Segment": ["Retail"] * 2412,
        "IsVanArsdel": (["Yes"] * 302) + (["No"] * 2110),
    })
    pbix_model = _fake_pbix_model(raw_df)

    captured: dict = {}
    _install_fake_write_pandas(monkeypatch, captured)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "columns_backfilled"
    assert result["columns"] == ["ISVANARSDEL"]
    assert result["row_count"] == 2412

    # Only key + target column(s) were staged -- Product/Segment (already
    # real, untouched columns) must never be part of the staged payload.
    assert set(captured["df"].columns) == {"PRODUCTID", "ISVANARSDEL"}
    assert captured["auto_create_table"] is True
    assert captured["table_type"] == "temporary"

    # The MERGE must only SET the target column, keyed on PRODUCTID, and
    # never touch PRODUCT/SEGMENT.
    merge_statements = [s for s in recorder.executed if s.strip().upper().startswith("MERGE")]
    assert len(merge_statements) == 1
    merge_sql = merge_statements[0]
    assert 'ON tgt."PRODUCTID" = src."PRODUCTID"' in merge_sql
    assert 'tgt."ISVANARSDEL" = src."ISVANARSDEL"' in merge_sql
    assert "SEGMENT" not in merge_sql
    assert '"PRODUCT"."PRODUCT"' not in merge_sql  # no accidental SET on the Product column

    # Staging table must be cleaned up.
    drop_statements = [s for s in recorder.executed if s.strip().upper().startswith("DROP TABLE")]
    assert len(drop_statements) == 1


# ---------------------------------------------------------------------------
# 3. Regression guard: a column that already existed before this run and
#    has real data is left completely untouched -- unchanged from today.
# ---------------------------------------------------------------------------

def test_existing_populated_table_with_no_newly_added_columns_is_still_skipped(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()
    # Nothing was added this run.
    assert sm._newly_added_columns == {}

    cursor = _fake_cursor([(2412, 2412, 2412, 0)])
    pbix_model = _fake_pbix_model(pd.DataFrame({"should": ["never be read"]}))
    write_pandas_called = MagicMock()
    monkeypatch.setitem(
        sys.modules, "snowflake.connector.pandas_tools",
        types.SimpleNamespace(write_pandas=write_pandas_called),
    )

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "skipped"
    assert "columns" not in result
    pbix_model.get_table.assert_not_called()
    write_pandas_called.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Regression guard: an ALL-NEW (previously empty) table is unaffected --
#    still goes through the existing whole-table load path.
# ---------------------------------------------------------------------------

def test_empty_table_path_is_unaffected_even_if_columns_were_added(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()
    sm._newly_added_columns["PRODUCT"] = {"ISVANARSDEL"}

    cursor = _fake_cursor([(0,)])  # row_count == 0 -> "empty"
    raw_df = pd.DataFrame({
        "ProductID": [1, 2], "Product": ["A", "B"], "Segment": ["X", "Y"], "IsVanArsdel": ["Yes", "No"],
    })
    pbix_model = _fake_pbix_model(raw_df)
    captured: dict = {}
    _install_fake_write_pandas(monkeypatch, captured)

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "loaded"
    assert "columns" not in result
    assert set(captured["df"].columns) == {"PRODUCTID", "PRODUCT", "SEGMENT", "ISVANARSDEL"}


# ---------------------------------------------------------------------------
# 5. No reliable key -> ambiguous_needs_review, never guesses.
# ---------------------------------------------------------------------------

def test_no_key_column_is_ambiguous_needs_review_not_a_guess(monkeypatch):
    sm = _build_schema_manager()
    dataset = SMLDataset(
        unique_name="Product", label="Product", source_table="Product",
        columns=[
            SMLColumn(unique_name="Product", label="Product", data_type=DataType.STRING),
            SMLColumn(unique_name="Segment", label="Segment", data_type=DataType.STRING),
            SMLColumn(unique_name="IsVanArsdel", label="IsVanArsdel", data_type=DataType.STRING),
        ],
    )
    sm._newly_added_columns["PRODUCT"] = {"ISVANARSDEL"}

    cursor = _fake_cursor([(2412, 2412, 2412, 0)])
    pbix_model = _fake_pbix_model(pd.DataFrame({"should": ["never be read"]}))
    write_pandas_called = MagicMock()
    monkeypatch.setitem(
        sys.modules, "snowflake.connector.pandas_tools",
        types.SimpleNamespace(write_pandas=write_pandas_called),
    )

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "ambiguous_needs_review"
    assert result["columns"] == ["ISVANARSDEL"]
    assert "no key column" in result["reason"]
    pbix_model.get_table.assert_not_called()
    write_pandas_called.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Row-count mismatch between live table and source PBIX -> refuse to
#    guess, never patch a possibly-wrong subset of rows.
# ---------------------------------------------------------------------------

def test_row_count_mismatch_is_ambiguous_needs_review(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()
    sm._newly_added_columns["PRODUCT"] = {"ISVANARSDEL"}

    cursor = _fake_cursor([(2412, 2412, 2412, 0)])  # live table has 2412 rows
    # Source PBIX only has 2400 rows for this table -- mismatch.
    raw_df = pd.DataFrame({
        "ProductID": list(range(1, 2401)),
        "Product": [f"Item {i}" for i in range(1, 2401)],
        "Segment": ["Retail"] * 2400,
        "IsVanArsdel": ["Yes"] * 2400,
    })
    pbix_model = _fake_pbix_model(raw_df)
    write_pandas_called = MagicMock()
    monkeypatch.setitem(
        sys.modules, "snowflake.connector.pandas_tools",
        types.SimpleNamespace(write_pandas=write_pandas_called),
    )

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "ambiguous_needs_review"
    assert "2400" in result["reason"] and "2412" in result["reason"]
    write_pandas_called.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Duplicate key values in the source PBIX -> refuse to guess.
# ---------------------------------------------------------------------------

def test_duplicate_key_in_source_is_ambiguous_needs_review(monkeypatch):
    sm = _build_schema_manager()
    dataset = _product_dataset()
    sm._newly_added_columns["PRODUCT"] = {"ISVANARSDEL"}

    cursor = _fake_cursor([(20, 20, 20, 0)])
    raw_df = pd.DataFrame({
        "ProductID": [1, 1, 2],  # duplicate key
        "Product": ["A", "A-dup", "B"],
        "Segment": ["X", "X", "Y"],
        "IsVanArsdel": ["Yes", "Yes", "No"],
    })
    pbix_model = _fake_pbix_model(raw_df)
    write_pandas_called = MagicMock()
    monkeypatch.setitem(
        sys.modules, "snowflake.connector.pandas_tools",
        types.SimpleNamespace(write_pandas=write_pandas_called),
    )

    result = sm._backfill_one_table(cursor, pbix_model, dataset, {})

    assert result["status"] == "ambiguous_needs_review"
    assert "duplicate values" in result["reason"]
    write_pandas_called.assert_not_called()
