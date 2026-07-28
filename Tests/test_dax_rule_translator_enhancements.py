from __future__ import annotations

from semabridge.converter.dax_rule_translator import (
    rule_based_translation,
    set_dialect,
    _quote_identifier,
)


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

    sql = rule_based_translation(dax, "SPEND_FACT", "DIVERSE_SUPPLIER_SPEND", "snowflake")

    assert sql == (
        'SUM(CASE WHEN DIVERSITY_BRIDGE."DIVERSITY_FLAG" = \'Y\' '
        'THEN SPEND_FACT."TRANSACTION_USD_AMOUNT" ELSE 0 END)'
    )


def test_time_intelligence_uses_max_date_anchor():
    sql = rule_based_translation(
        "TOTALYTD(SUM(spend_fact[Transaction_USD_Amount]), 'date'[Cal_DT])",
        "SPEND_FACT",
        "Spend YTD",
        "snowflake",
    )

    # MAX_DATE must be qualified with the fact table's alias — a bare MAX_DATE
    # is not a valid identifier inside a semantic view's METRICS clause; it only
    # resolves because it's a real column on the fact table's enriched view.
    assert "DATE_TRUNC('YEAR', SPEND_FACT.\"MAX_DATE\")" in sql
    assert 'CAL_DT" <= SPEND_FACT."MAX_DATE"' in sql
    assert 'SPEND_FACT."TRANSACTION_USD_AMOUNT"' in sql


def test_sumx_arithmetic_translates_without_llm():
    sql = rule_based_translation(
        "SUMX('Sales', [Quantity] * [Unit_Price])",
        "SALES",
        "Revenue",
        "snowflake",
    )

    assert sql == 'SUM(SALES."QUANTITY" * SALES."UNIT_PRICE")'
