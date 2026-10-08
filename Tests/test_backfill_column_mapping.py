"""Each source column is loaded into its OWN final (possibly renamed) target
column, and a table fails the post-load check when a target column is 100%
NULL while its source column has values."""

import sys
import types
from unittest.mock import MagicMock

import pandas as pd
import pytest

from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.formats.sml.models import DataType, SMLColumn, SMLDataset
from semabridge.utils.identifiers import IdentifierSanitizer


def _schema_manager():
    cfg = SnowflakeConfig(account="acc", user="usr", password="pwd", warehouse="wh",
                          database="db_x", schema_name="schema_x")
    cm = MagicMock()
    cm._execute_sql.side_effect = lambda cursor, sql, params=None, context="": setattr(cursor, "_last_sql", sql)
    return SnowflakeSchemaManager(config=cfg, behavior=ConnectorBehavior(),
                                  identifier_sanitizer=IdentifierSanitizer(), connection_manager=cm,
                                  dup_name_repo=None)


def _col(name, data_type=DataType.STRING, is_key=False):
    return SMLColumn(unique_name=name, label=name, data_type=data_type, is_key=is_key)


def test_renamed_target_column_receives_its_own_source_data():
    sm = _schema_manager()
    key_a, key_b, value = _col("key_a", DataType.INTEGER, True), _col("key_b"), _col("value_x")
    # Physical names as the table was created -- two of them differ from the
    # sanitized source name (renamed/disambiguated target columns).
    physical = {"FACT_KEY_A": key_a, "KEY_B_2": key_b, "VALUE_X": value}
    df = pd.DataFrame({"key_a": [1, 2], "key_b": ["p", "q"], "value_x": ["v1", "v2"], "extra": [0, 0]})

    mapped, source_by_target = sm._map_source_columns_to_physical(df, physical)

    assert list(mapped.columns) == ["FACT_KEY_A", "KEY_B_2", "VALUE_X"]
    assert mapped["FACT_KEY_A"].tolist() == [1, 2]
    assert mapped["KEY_B_2"].tolist() == ["p", "q"]
    assert source_by_target == {"FACT_KEY_A": "key_a", "KEY_B_2": "key_b", "VALUE_X": "value_x"}


def test_source_columns_that_sanitize_alike_each_reach_their_own_column():
    sm = _schema_manager()
    dataset = SMLDataset(unique_name="table_x", source_table="table_x",
                         columns=[_col("col one"), _col("col_one")])
    physical = sm._collect_physical_source_columns(dataset)
    assert len(physical) == 2
    df = pd.DataFrame({"col one": ["a"], "col_one": ["b"]})
    mapped, _ = sm._map_source_columns_to_physical(df, physical)
    by_source = {physical[name].unique_name: mapped[name].tolist() for name in mapped.columns}
    assert by_source == {"col one": ["a"], "col_one": ["b"]}


def _cursor(post_load_counts):
    cursor = MagicMock()
    cursor.connection = MagicMock()

    def _fetchone():
        sql = getattr(cursor, "_last_sql", "")
        if isinstance(sql, str) and "COUNT(*)" in sql.upper():
            return (0, 0, 0, 0)  # classification: empty table
        return post_load_counts

    cursor.fetchone.side_effect = _fetchone
    return cursor


def _dataset():
    return SMLDataset(unique_name="fact_x", source_table="fact_x", columns=[
        _col("key_a", DataType.INTEGER, True), _col("key_b"), _col("value_x"), _col("value_y"),
    ])


def _run_backfill(monkeypatch, sm, cursor, df):
    captured = {}

    def fake_write_pandas(conn, frame, table_name, **kwargs):
        captured["df"] = frame
        return True, 1, len(frame), None

    monkeypatch.setitem(sys.modules, "snowflake.connector.pandas_tools", types.SimpleNamespace(write_pandas=fake_write_pandas))
    pbix = MagicMock()
    pbix.get_table.return_value = df
    return sm._backfill_one_table(cursor, pbix, _dataset(), {}), captured


def test_post_load_check_fails_a_table_with_an_all_null_column_whose_source_has_values(monkeypatch):
    sm = _schema_manager()
    df = pd.DataFrame({"key_a": [1, 2], "key_b": ["p", "q"], "value_x": ["v1", "v2"], "value_y": [None, None]})
    # The table reports KEY_B empty after the load.
    result, captured = _run_backfill(monkeypatch, sm, _cursor((2, 0, 2)), df)
    assert result["status"] == "failed"
    assert result["columns"] == ["KEY_B"]
    assert "100% NULL" in result["reason"]
    # The source column itself was loaded under its target name.
    assert captured["df"]["KEY_B"].tolist() == ["p", "q"]


def test_post_load_check_passes_when_every_sourced_column_is_populated(monkeypatch):
    sm = _schema_manager()
    df = pd.DataFrame({"key_a": [1, 2], "key_b": ["p", "q"], "value_x": ["v1", "v2"], "value_y": [None, None]})
    # value_y is empty in the source too -- never flagged.
    result, _ = _run_backfill(monkeypatch, sm, _cursor((2, 2, 2)), df)
    assert result["status"] == "loaded"
    assert result["row_count"] == 2
