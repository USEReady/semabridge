"""Regression tests for MeasureSynchronizer._build_safe_sum_sql's flag-vs-
numeric detection.

The name-pattern heuristic (^IS_/^HAS_/^WAS_/etc.) was the only signal used
to decide whether a measure column needed boolean-safe SUM handling, even
though the actual synced data (real Python bool vs. int/float, faithfully
preserved from Power BI's JSON DAX-query results) already proves the answer.
A numeric measure whose business name happens to start with one of those
prefixes was silently collapsed into a 0/1 tally instead of a real SUM.

Uses only synthetic placeholder names/data — no real customer schema.
"""
from types import SimpleNamespace

from semabridge.connectors.measure_sync import MeasureSynchronizer
from semabridge.core.behavior import ConnectorBehavior
from semabridge.utils.identifiers import IdentifierSanitizer


def _synchronizer() -> MeasureSynchronizer:
    return MeasureSynchronizer(
        config=SimpleNamespace(database="TESTDB", schema_name="TESTSCHEMA"),
        behavior=ConnectorBehavior(),
        identifier_sanitizer=IdentifierSanitizer(),
    )


def test_numeric_column_with_flag_like_name_is_not_collapsed_to_a_tally():
    """Real numeric data must win over a coincidental ^HAS_ name match."""
    sync = _synchronizer()
    synthetic_data = [{"HAS_DISCOUNT_AMOUNT": 12.5, "IS_ACTIVE_FLAG_COL": True}]
    column_types = sync._detect_boolean_columns(synthetic_data)

    sql = sync._build_safe_sum_sql(
        'base."HAS_DISCOUNT_AMOUNT"',
        "HAS_DISCOUNT_AMOUNT",
        is_boolean_column=column_types.get("HAS_DISCOUNT_AMOUNT"),
    )

    assert "IFF" not in sql
    assert "SUM(" in sql


def test_genuine_boolean_column_still_gets_flag_safe_sum():
    sync = _synchronizer()
    synthetic_data = [{"HAS_DISCOUNT_AMOUNT": 12.5, "IS_ACTIVE_FLAG_COL": True}]
    column_types = sync._detect_boolean_columns(synthetic_data)

    sql = sync._build_safe_sum_sql(
        'base."IS_ACTIVE_FLAG_COL"',
        "IS_ACTIVE_FLAG_COL",
        is_boolean_column=column_types.get("IS_ACTIVE_FLAG_COL"),
    )

    assert "IFF" in sql


def test_column_absent_from_synced_data_falls_back_to_name_heuristic_unchanged():
    """No real data available for this identifier -> existing behavior,
    not a regression for callers that never pass column_types at all."""
    sync = _synchronizer()

    sql = sync._build_safe_sum_sql('base."WAS_DELETED"', "WAS_DELETED")

    assert "IFF" in sql


def test_detect_boolean_columns_returns_definitive_entry_for_every_synced_column():
    sync = _synchronizer()
    synthetic_data = [
        {"PLAIN_NUMERIC_METRIC": 3, "HAS_DISCOUNT_AMOUNT": 12.5, "IS_ACTIVE_FLAG_COL": True}
    ]

    column_types = sync._detect_boolean_columns(synthetic_data)

    assert column_types["PLAIN_NUMERIC_METRIC"] is False
    assert column_types["HAS_DISCOUNT_AMOUNT"] is False
    assert column_types["IS_ACTIVE_FLAG_COL"] is True


def test_detect_boolean_columns_empty_data_returns_empty_dict():
    sync = _synchronizer()
    assert sync._detect_boolean_columns([]) == {}


def test_generate_semantic_view_tiered_wires_real_column_types_through(monkeypatch):
    """End-to-end: generate_semantic_view_tiered must actually pass the
    real-data-derived column_types into _build_safe_sum_sql, not just accept
    the parameter without using it."""
    from semabridge.converter.measure_triage import MaterializationStrategy, TriageResult

    sync = _synchronizer()
    # Already underscored/uppercase so sanitize_column is a safe no-op and
    # the sanitized key deterministically matches the synthetic data below —
    # avoids coupling this test to IdentifierSanitizer's exact camelCase
    # handling, which isn't this fix's concern.
    triage_results = {
        "HAS_DISCOUNT_AMOUNT": TriageResult(
            measure_name="HAS_DISCOUNT_AMOUNT",
            expression="SUM([HAS_DISCOUNT_AMOUNT])",
            tier=1,
            strategy=MaterializationStrategy.PASSTHROUGH,
            reason="passthrough",
            recommendations=[],
        )
    }
    synthetic_data = [{"HAS_DISCOUNT_AMOUNT": 12.5}]
    column_types = sync._detect_boolean_columns(synthetic_data)

    ddl = sync.generate_semantic_view_tiered(
        model_name="TestModel",
        shadow_table="DB.SCHEMA.SHADOW",
        triage_results=triage_results,
        grain_dimensions=[],
        column_types=column_types,
    )

    assert "IFF" not in ddl
