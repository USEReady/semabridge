"""Test generic primary key discovery in TablesClauseBuilder for small unique tables.

Item 2 requirement: verify that TablesClauseBuilder generically discovers
candidate primary keys for small disconnected tables using schema_manager.get_verified_unique_columns.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer


def _dataset(unique_name, source_table, columns, key_col=None):
    return SimpleNamespace(
        unique_name=unique_name,
        source_table=source_table,
        is_fact=False,
        columns=[
            SimpleNamespace(unique_name=c, is_key=(c == key_col))
            for c in columns
        ],
    )


def test_small_unique_table_discovers_pk_from_verified_uniques():
    """Verify that a small disconnected table with no explicit is_key
    gets a PRIMARY KEY clause from schema_manager.get_verified_unique_columns."""
    indicators_ds = _dataset("Indicators", "INDICATORS", ["SORT", "STATUS", "IMAGE"], key_col=None)
    model = SimpleNamespace(datasets=[indicators_ds], relationships=[])

    mock_schema_manager = MagicMock()
    mock_schema_manager.get_verified_unique_columns.return_value = {"SORT", "STATUS"}

    builder = TablesClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=mock_schema_manager,
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
        behavior=SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode=None, auto_add_anchors=False)),
        live_schema_metadata={"INDICATORS": {"SORT", "STATUS", "IMAGE"}},
    )

    tables_sql, _, _, _, _, _ = builder.build_for_osi(
        model, registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )

    assert any('PRIMARY KEY ("SORT")' in line for line in tables_sql)
    mock_schema_manager.get_verified_unique_columns.assert_called_with("INDICATORS", cursor=None)


def test_single_row_table_falls_back_to_first_column_if_verified():
    """Verify that a 1-row table (e.g. RANGE) with no relationships gets
    PRIMARY KEY on its first column when verified unique."""
    range_ds = _dataset("Range", "RANGE", ["WWW_OBVIENCE_COM"], key_col=None)
    model = SimpleNamespace(datasets=[range_ds], relationships=[])

    mock_schema_manager = MagicMock()
    mock_schema_manager.get_verified_unique_columns.return_value = {"WWW_OBVIENCE_COM"}

    builder = TablesClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=mock_schema_manager,
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
        behavior=SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode=None, auto_add_anchors=False)),
        live_schema_metadata={"RANGE": {"WWW_OBVIENCE_COM"}},
    )

    tables_sql, _, _, _, _, _ = builder.build_for_osi(
        model, registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )

    assert any('PRIMARY KEY ("WWW_OBVIENCE_COM")' in line for line in tables_sql)
