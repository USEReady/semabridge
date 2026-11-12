"""E8: keys come only from the model (isKey), relationship one-sides or declared
Snowflake PKs -- never from a column-name suffix."""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.core.drop_ledger import DropLedger
from semabridge.utils.identifiers import IdentifierSanitizer


def _osi():
    tmsl = {"model": {
        "tables": [
            {"name": "Sales", "columns": [
                {"name": "OrderID", "dataType": "int64", "sourceColumn": "OrderID"},
                {"name": "CustKey", "dataType": "int64", "sourceColumn": "CustKey"},
                {"name": "Amount", "dataType": "double", "sourceColumn": "Amount", "summarizeBy": "sum"},
            ]},
            {"name": "Customer", "columns": [
                {"name": "CustKey", "dataType": "int64", "sourceColumn": "CustKey"},
                {"name": "Name", "dataType": "string", "sourceColumn": "Name"},
            ]},
            {"name": "Calendar", "columns": [
                {"name": "Date", "dataType": "dateTime", "sourceColumn": "Date", "isKey": True},
            ]},
        ],
        "relationships": [{"fromTable": "Sales", "fromColumn": "CustKey", "toTable": "Customer", "toColumn": "CustKey"}],
    }}
    return TMSLToOSIConverter().to_osi({"tmsl": tmsl, "dataset_id": "M"})


def test_is_key_only_from_model_flag():
    osi = _osi()
    keys = {(d.unique_name, c.unique_name) for d in osi.datasets for c in d.columns if c.is_key}
    assert keys == {("Calendar", "Date")}


def test_identifier_named_columns_still_not_implicit_metrics():
    names = {m.unique_name for m in _osi().metrics}
    assert not any("ORDERID" in n or "CUSTKEY" in n for n in names)


class _DDL:
    def to_snowflake_relationship_name(self, name):
        return IdentifierSanitizer().sanitize_column(name)


class _Schema:
    def _resolve_physical_column_name(self, dataset, column):
        return IdentifierSanitizer().sanitize_column(column)


def _build(rel, aliases, by_name, ledger):
    return RelationshipsClauseBuilder(IdentifierSanitizer(), _Schema(), _DDL(), drop_ledger=ledger).build_for_osi(
        SimpleNamespace(relationships=[rel], metrics=[]),
        dataset_aliases=aliases, dataset_by_name=by_name,
        dataset_col_lookup={}, declared_pk_by_alias={}, relationship_target_alias={},
    )


def test_many_to_many_relationship_dropped_with_expected_reason():
    ledger = DropLedger()
    rel = SimpleNamespace(unique_name="f_b", is_active=True, from_dataset="fact", to_dataset="bridge",
                          from_columns=["K"], to_columns=["K"], cardinality="many-to-many")
    lines = _build(rel, {"fact": "FACT", "bridge": "BRIDGE"}, {"fact": SimpleNamespace(), "bridge": SimpleNamespace()}, ledger)
    assert lines == []
    rec = ledger.to_json()[0]
    assert rec["by_design"] and "Many-to-many" in rec["reason"]


def test_relationship_to_excluded_table_is_expected_drop():
    ledger = DropLedger()
    rel = SimpleNamespace(unique_name="f_c", is_active=True, from_dataset="fact", to_dataset="Calc Calendar",
                          from_columns=["D"], to_columns=["Date"])
    assert _build(rel, {"fact": "FACT"}, {"fact": SimpleNamespace()}, ledger) == []
    rec = ledger.to_json()[0]
    assert rec["by_design"] and "not in the semantic view" in rec["reason"]
