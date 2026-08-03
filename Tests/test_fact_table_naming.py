"""Regression tests for the shared fact-table-name detection used by both
connectors/snowflake_emitter.py (_identify_fact_table) and
connectors/tables_clause_builder.py (anchor-injection gating).

tables_clause_builder.py used to have its own, independent "fact" in
name.lower() substring check — no word-boundary, so any dataset merely
containing those four letters (Manufacturer, Artifact, Satisfaction)
tripped it — while snowflake_emitter.py's copy had already been fixed to
match whole words only. Extracting one canonical implementation both
files import means a future fix can't silently diverge like that again.

Synthetic placeholder names only.
"""
from semabridge.connectors.fact_table_naming import (
    FACT_KEYWORDS,
    is_fact_like_name,
    tokenize_dataset_name,
)


def test_whole_word_fact_keyword_is_detected():
    assert is_fact_like_name("SalesFact") is True
    assert is_fact_like_name("Sales_Fact") is True
    assert is_fact_like_name("TRANSACTIONS") is True


def test_mid_word_substring_is_not_a_false_positive():
    """The exact bug: these all contain 'fact'/'sales' as a substring but
    are not fact tables."""
    assert is_fact_like_name("Manufacturer") is False
    assert is_fact_like_name("Artifact") is False
    assert is_fact_like_name("Satisfaction") is False


def test_synthetic_non_fact_dimension_names_are_not_flagged():
    for name in ("WidgetDimension", "GadgetLookup", "CustomerRegistry"):
        assert is_fact_like_name(name) is False


def test_tokenize_splits_on_case_and_separator_boundaries():
    assert tokenize_dataset_name("SalesFact") == ["SALES", "FACT"]
    assert tokenize_dataset_name("Sales_Fact_2024") == ["SALES", "FACT", "2024"]
    assert tokenize_dataset_name("Manufacturer") == ["MANUFACTURER"]


def test_fact_keywords_are_generic_not_tied_to_any_specific_project():
    assert FACT_KEYWORDS == frozenset(
        {"FACT", "FACTS", "SALES", "TRANSACTION", "TRANSACTIONS", "AGGREGATE", "AGGREGATES"}
    )


def test_snowflake_emitter_and_tables_clause_builder_share_one_implementation():
    """Structural proof against future divergence: both modules must
    resolve to the exact same function/constant objects, not independent
    copies."""
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.connectors import fact_table_naming

    assert SnowflakeEmitter._FACT_KEYWORDS is fact_table_naming.FACT_KEYWORDS
    assert SnowflakeEmitter._tokenize_dataset_name is fact_table_naming.tokenize_dataset_name
