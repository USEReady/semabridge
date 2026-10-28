"""The semantic view depends only on the PBIX model and the Snowflake schema,
never on table data; data is only read for warnings and sample values."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.core.exceptions import ConnectorError
from semabridge.utils.identifiers import IdentifierSanitizer
from test_tables_clause_builder_generic_pk import _dataset


class _Cursor:
    """Answers every data query with `counts`; metadata queries are empty."""

    def __init__(self, counts):
        self.counts, self.description, self.executed = counts, [], []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(sql)
        return self

    def fetchone(self):
        return self.counts

    def fetchall(self):
        return []


def _tables_clause(counts):
    manager = SnowflakeSchemaManager(
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
        behavior=MagicMock(),
        identifier_sanitizer=IdentifierSanitizer(),
        connection_manager=MagicMock(_execute_sql=lambda cur, sql, *a, **k: cur.execute(sql)),
    )
    cursor = _Cursor(counts)
    datasets = [
        _dataset("Sales", "SALES", ["PRODUCT_ID", "UNITS"]),
        _dataset("Product", "PRODUCT", ["PRODUCT_ID", "NAME"]),
        _dataset("Notes", "NOTES", ["NOTE", "TEXT"]),
    ]
    rel = SimpleNamespace(
        is_active=True, from_dataset="Sales", from_columns=["PRODUCT_ID"],
        to_dataset="Product", to_columns=["PRODUCT_ID"],
    )
    builder = TablesClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=manager,
        config=SimpleNamespace(database="DB", schema_name="SCHEMA"),
        behavior=SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode=None, auto_add_anchors=False)),
        live_schema_metadata={
            "SALES": {"PRODUCT_ID", "UNITS"},
            "PRODUCT": {"PRODUCT_ID", "NAME"},
            "NOTES": {"NOTE", "TEXT"},
        },
        cursor=cursor,
    )
    tables_sql, *_ = builder.build_for_osi(
        SimpleNamespace(datasets=datasets, relationships=[rel]),
        registry=AliasRegistry(), metric_counts_by_dataset={}, related_datasets=set(),
    )
    return tables_sql


def test_tables_clause_is_identical_for_loaded_and_empty_tables():
    loaded = _tables_clause((1_000, 1_000))
    empty = _tables_clause((0, 0))
    duplicated = _tables_clause((1_000, 3))  # keys not unique in the data
    assert loaded == empty == duplicated
    assert any('PRODUCT AS' in l and 'PRIMARY KEY ("PRODUCT_ID")' in l for l in loaded)
    assert any(l.strip().startswith("SALES AS") and "PRIMARY KEY" not in l for l in loaded)


def test_missing_target_schema_stops_with_a_clear_error():
    manager = SnowflakeSchemaManager(
        config=SimpleNamespace(database="DB", schema_name="NEW_SCHEMA"),
        behavior=MagicMock(),
        identifier_sanitizer=IdentifierSanitizer(),
        connection_manager=MagicMock(_execute_sql=lambda cur, sql, *a, **k: cur.execute(sql)),
    )
    cursor = _Cursor((0, 0))
    with pytest.raises(ConnectorError, match="does not exist"):
        manager._ensure_schema_exists(cursor)
    assert cursor.executed and cursor.executed[0].startswith("SHOW SCHEMAS LIKE 'NEW_SCHEMA'")
    assert not any("CREATE SCHEMA" in s.upper() for s in cursor.executed)


def test_precompute_skips_measure_references():
    from semabridge.connectors.ddl_builder import SemanticViewBuilder

    builder = SemanticViewBuilder.__new__(SemanticViewBuilder)
    builder.identifier_sanitizer = IdentifierSanitizer()
    model = SimpleNamespace(
        datasets=[
            SimpleNamespace(unique_name="Fact", columns=[SimpleNamespace(unique_name="Units")]),
            SimpleNamespace(unique_name="Dim", columns=[SimpleNamespace(unique_name="Flag")]),
        ],
        metrics=[
            SimpleNamespace(unique_name="Ratio", dataset="Fact", expression="DIVIDE(Fact[Units] + Dim[Score], 'Dim'[Flag])"),
            SimpleNamespace(unique_name="Score", dataset="Dim", expression="SUM(Dim[Flag])"),
        ],
        relationships=[
            SimpleNamespace(unique_name="rel_fact_dim", from_dataset="Fact", to_dataset="Dim", from_columns=["DimID"], to_columns=["DimID"]),
        ],
    )
    suggestions = builder._precompute_suggestions(model)
    assert suggestions == {"Fact": ["DIM_FLAG"]}


def test_rejected_openai_key_is_noted_once_and_disables_the_tier(caplog, monkeypatch):
    import logging
    from semabridge.connectors import translator

    monkeypatch.setattr(translator, "_OPENAI_KEY_REJECTED", False)

    class AuthenticationError(Exception):
        status_code = 401

    with caplog.at_level(logging.WARNING):
        assert translator._note_openai_auth_failure(AuthenticationError("Incorrect API key"))
        assert translator._note_openai_auth_failure(AuthenticationError("Incorrect API key"))
        assert not translator._note_openai_auth_failure(TimeoutError("slow"))
    assert translator._openai_key_rejected()
    assert caplog.text.count("rejected the configured API key") == 1
