from __future__ import annotations

from semabridge.converter.dax_rule_translator import (
    rule_based_translation,
    set_dialect,
    _quote_identifier,
)
from semabridge.converter.dax_translator import DAXTranslator


def test_quote_identifier_supports_snowflake_and_databricks():
    set_dialect("snowflake")
    assert _quote_identifier("Transaction USD Amount") == '"TRANSACTION_USD_AMOUNT"'

    set_dialect("databricks")
    assert _quote_identifier("Transaction USD Amount") == "`TRANSACTION_USD_AMOUNT`"


def test_calculate_filter_translates_to_case_when_snowflake():
    dax = """
    CALCULATE(
        SUM(spend_fact[Transaction_USD_Amount]),
        FILTER(
            diversity_bridge,
            diversity_bridge[Diversity_Flag] = "Y"
        )
    )
    """

    # Since 765f27c (F10) the regex rule tier no longer handles CALCULATE
    # filters (translate_calculate_with_filters is a stub); the AST renderer
    # does, with DAX filter semantics (no ELSE 0: filtered-out rows are BLANK).
    assert rule_based_translation(dax, "SPEND_FACT", "DIVERSE_SUPPLIER_SPEND", "snowflake") is None

    result = DAXTranslator().translate(dax, table_alias="SPEND_FACT", dataset_name="spend_fact")
    assert result.is_success
    assert result.sql == (
        "SUM(CASE WHEN DIVERSITY_BRIDGE.DIVERSITY_FLAG = 'Y' "
        'THEN SPEND_FACT."TRANSACTION_USD_AMOUNT" END)'
    )


def test_time_intelligence_uses_current_date_not_synthetic_max_date_anchor():
    """Regression: this used to table-qualify a synthetic MAX_DATE
    enriched-view anchor column. MAX_DATE is not actually in scope inside
    a semantic view's METRICS clause (see connectors/translator.py's
    parallel implementation, fixed the same way in commit e4c8322) — now
    uses the native CURRENT_DATE() function instead, with no dependency
    on an enrichment stage having run."""
    sql = rule_based_translation(
        "TOTALYTD(SUM(spend_fact[Transaction_USD_Amount]), 'date'[Cal_DT])",
        "SPEND_FACT",
        "Spend YTD",
        "snowflake",
    )

    assert "MAX_DATE" not in sql.upper()
    assert "DATE_TRUNC('YEAR', CURRENT_DATE())" in sql
    assert '"CAL_DT" <= CURRENT_DATE()' in sql
    assert 'SPEND_FACT."TRANSACTION_USD_AMOUNT"' in sql


def test_sumx_arithmetic_translates_without_llm():
    sql = rule_based_translation(
        "SUMX('Sales', [Quantity] * [Unit_Price])",
        "SALES",
        "Revenue",
        "snowflake",
    )

    # ::FLOAT on SUM/AVG iterators is intended (137869f8, F3).
    assert sql == 'SUM(SALES."QUANTITY" * SALES."UNIT_PRICE"::FLOAT)'
