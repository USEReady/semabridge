"""E9: a table that only hosts measures plus hidden placeholder column(s) is omitted from TABLES."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer


def _col(name, hidden):
    return SimpleNamespace(unique_name=name, is_key=False, is_hidden=hidden)


def _build(container_cols, metrics):
    fact = SimpleNamespace(unique_name="Sales", source_table="SALES", is_fact=True,
                           columns=[_col("Amount", False), _col("Value", False)])
    container = SimpleNamespace(unique_name="Calculations", source_table="CALCULATIONS", is_fact=False,
                                columns=container_cols)
    model = SimpleNamespace(datasets=[fact, container], relationships=[], metrics=metrics)
    sm = MagicMock()
    sm.declared_primary_keys.return_value = {}
    sm.check_key_uniqueness.return_value = None
    builder = TablesClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(), schema_manager=sm,
        config=SimpleNamespace(database="DB", schema_name="S"),
        behavior=SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode=None, auto_add_anchors=False)),
        live_schema_metadata={},
    )
    lines, *_ = builder.build_for_osi(model, registry=AliasRegistry(),
                                      metric_counts_by_dataset={"Calculations": 1}, related_datasets=set())
    return "\n".join(lines)


def test_hidden_placeholder_only_container_is_omitted():
    m = SimpleNamespace(dataset="Calculations", expression="SUMX(Sales, Sales[Value])", sql_expression=None, source_column=None)
    tables = _build([_col("Value", True)], [m])
    assert "CALCULATIONS" not in tables
    assert "SALES" in tables


def test_container_whose_hidden_column_is_used_is_kept():
    m = SimpleNamespace(dataset="Calculations", expression="SUM(Calculations[Value])", sql_expression=None, source_column=None)
    assert "CALCULATIONS" in _build([_col("Value", True)], [m])


def test_container_with_visible_column_is_kept():
    m = SimpleNamespace(dataset="Calculations", expression="1", sql_expression=None, source_column=None)
    assert "CALCULATIONS" in _build([_col("Label", False)], [m])
