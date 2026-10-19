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


def test_disconnected_table_takes_the_pk_declared_in_snowflake():
    """A disconnected table with no PBIX key takes the primary key declared in
    Snowflake (metadata), never one discovered by counting rows."""
    indicators_ds = _dataset("Indicators", "INDICATORS", ["SORT", "STATUS", "IMAGE"], key_col=None)
    model = SimpleNamespace(datasets=[indicators_ds], relationships=[])

    mock_schema_manager = MagicMock()
    mock_schema_manager.declared_primary_keys.return_value = {"INDICATORS": ["STATUS"]}

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

    assert any('PRIMARY KEY ("STATUS")' in line for line in tables_sql)
    # Uniqueness in the data is only checked for a warning.
    mock_schema_manager.warn_if_key_not_unique.assert_called_with("INDICATORS", ["STATUS"], cursor=None)


def test_table_without_any_key_falls_back_to_first_column_without_data():
    """A table with no relationship, PBIX key or declared key gets PRIMARY
    KEY on its first column, whatever its data holds."""
    range_ds = _dataset("Range", "RANGE", ["WWW_OBVIENCE_COM"], key_col=None)
    model = SimpleNamespace(datasets=[range_ds], relationships=[])

    mock_schema_manager = MagicMock()
    mock_schema_manager.declared_primary_keys.return_value = {}

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
