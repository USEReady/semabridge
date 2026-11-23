"""Phase 3.1: Databricks rules, the TargetBackend outline and the planner flag.

Snowflake stays the default everywhere (Tests/golden proves its output byte
for byte); these tests cover what Databricks adds.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import date, timedelta

import pytest

from semabridge.converter.dax_ast_parser import try_ast_translate
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.target_backends import (
    TargetBackend,
    TargetBackendNotReady,
    UnsupportedBackendError,
    get_backend,
    registered_backends,
)
from semabridge.target_backends.databricks import DatabricksBackend
from semabridge.target_rules import (
    DATABRICKS_RULES,
    SNOWFLAKE_RULES,
    DatabricksDialect,
    ReasonCode,
    get_target_rules,
    registered_targets,
)

DBX = DatabricksDialect()

# Snowflake-only spellings that must never appear in Databricks SQL.
_SNOWFLAKE_ONLY = ('"', "::FLOAT", "IFF(", "TRY_TO_NUMBER(", "TO_VARCHAR(", "DATEADD(", "PARTITION BY EXCLUDING")


# --- registration ------------------------------------------------------------


def test_databricks_rules_are_registered_and_snowflake_stays_default() -> None:
    assert "databricks" in registered_targets() and "snowflake" in registered_targets()
    assert get_target_rules("Databricks") is DATABRICKS_RULES
    assert DATABRICKS_RULES.target == "databricks"
    assert DATABRICKS_RULES.dialect.name == "databricks"
    assert DAXTranslator()._rules is SNOWFLAKE_RULES


# --- policies ----------------------------------------------------------------


def test_every_reason_code_has_a_databricks_rule() -> None:
    policies = DATABRICKS_RULES.policies
    for code in ReasonCode:
        decision = policies.decide(code, "x")
        assert decision.keep is False, code
        reason = decision.reason.lower()
        # The translator treats these prefixes as a decline.
        assert reason.startswith(("not applicable", policies.by_design_prefix.lower())), code
        assert "snowflake" not in reason, code


def test_same_decisions_as_snowflake() -> None:
    for code in ReasonCode:
        assert DATABRICKS_RULES.policies.rule_for(code).keep == SNOWFLAKE_RULES.policies.rule_for(code).keep
        assert (
            DATABRICKS_RULES.policies.rule_for(code).by_design
            == SNOWFLAKE_RULES.policies.rule_for(code).by_design
        )


def test_placeholder_is_an_aggregate_null() -> None:
    policies = DATABRICKS_RULES.policies
    assert policies.placeholder_sql == "MAX(CAST(NULL AS DOUBLE))"
    assert policies.is_placeholder("MAX(CAST(NULL AS DOUBLE))")
    assert policies.is_placeholder("max(cast(null as double)) + 1")
    assert not policies.is_placeholder("MAX(SALES.`AMOUNT`)")
    assert policies.relationship_advisories is False
    assert policies.decline_two_table_measures is True


def test_capabilities() -> None:
    caps = DATABRICKS_RULES.capabilities
    assert (caps.text_measures, caps.window_metrics, caps.enriched_helper_columns) == (False, False, False)
    assert (caps.nested_joins, caps.bridge_views) == (True, True)


# --- dialect: spelling ----------------------------------------------------------


def test_identifiers() -> None:
    assert DBX.quote_identifier("Order Amount") == "`Order Amount`"
    assert DBX.quote_identifier("a`b") == "`a``b`"
    assert DBX.qualify("SALES", "AMOUNT") == "SALES.`AMOUNT`"
    assert DBX.column_ref("SALES", "Unit Price") == "SALES.`UNIT_PRICE`"
    assert DBX.table_alias("Sales") == SNOWFLAKE_RULES.dialect.table_alias("Sales")
    assert DBX.measure_ref("Total Sales") == "MEASURE(`Total Sales`)"


def test_column_ref_uses_real_names_when_known() -> None:
    dialect = DatabricksDialect(column_names={("sales", "unit price"): "unit_price_usd"})
    assert dialect.column_ref("SALES", "Unit Price") == "SALES.`unit_price_usd`"
    assert dialect.column_ref("SALES", "Qty") == "SALES.`QTY`"


def test_casts_and_conditionals() -> None:
    assert DBX.cast_float("x") == "x::DOUBLE"
    assert DBX.iff("c", "a", "b") == "IF(c, a, b)"
    assert DBX.try_to_number("x") == "TRY_CAST(x AS DOUBLE)"
    assert DBX.to_varchar("x") == "CAST(x AS STRING)"
    assert DBX.current_date() == "CURRENT_DATE()"
    assert DBX.current_timestamp() == "CURRENT_TIMESTAMP()"


@pytest.mark.parametrize(
    "unit, amount, expected",
    [
        ("MONTH", -1, "ADD_MONTHS(d, -1)"),
        ("month", "-3", "ADD_MONTHS(d, -3)"),
        ("QUARTER", -1, "ADD_MONTHS(d, -3)"),
        ("YEAR", -1, "ADD_MONTHS(d, -12)"),
        ("YEAR", "n", "ADD_MONTHS(d, (n) * 12)"),
        ("DAY", 7, "DATE_ADD(d, 7)"),
        ("day", "-1", "DATE_ADD(d, -1)"),
        ("WEEK", 2, "DATE_ADD(d, 14)"),
        ("HOUR", 2, "TIMESTAMPADD(HOUR, 2, d)"),
    ],
)
def test_date_add_keeps_dates(unit, amount, expected) -> None:
    assert DBX.date_add(unit, amount, "d") == expected


def test_date_trunc_and_period_ends() -> None:
    assert DBX.date_trunc("YEAR", "d") == "TRUNC(d, 'YEAR')"
    assert DBX.date_trunc("quarter", "d") == "TRUNC(d, 'QUARTER')"
    assert DBX.date_trunc("DAY", "d") == "CAST(d AS DATE)"
    assert DBX.date_trunc("HOUR", "d") == "DATE_TRUNC('HOUR', d)"
    assert DBX.last_day("d", "MONTH") == "LAST_DAY(d)"
    assert DBX.last_day("d", "QUARTER") == "DATE_ADD(ADD_MONTHS(TRUNC(d, 'QUARTER'), 3), -1)"
    assert DBX.last_day("d", "YEAR") == "DATE_ADD(ADD_MONTHS(TRUNC(d, 'YEAR'), 12), -1)"


def test_date_diff_spelling() -> None:
    assert DBX.date_diff("YEAR", "s", "e") == "(YEAR(e) - YEAR(s))"
    assert DBX.date_diff("QUARTER", "s", "e") == "((YEAR(e) - YEAR(s)) * 4 + QUARTER(e) - QUARTER(s))"
    assert DBX.date_diff("MONTH", "s", "e") == "((YEAR(e) - YEAR(s)) * 12 + MONTH(e) - MONTH(s))"
    assert DBX.date_diff("DAY", "s", "e") == "DATEDIFF(CAST(e AS DATE), CAST(s AS DATE))"
    assert DBX.date_diff("WEEK", "s", "e") == (
        "(DATEDIFF(DATE_SUB(CAST(e AS DATE), DAYOFWEEK(e) - 1), DATE_SUB(CAST(s AS DATE), DAYOFWEEK(s) - 1)) DIV 7)"
    )
    assert DBX.date_diff("HOUR", "s", "e") == "TIMESTAMPDIFF(HOUR, DATE_TRUNC('HOUR', s), DATE_TRUNC('HOUR', e))"


def test_dialect_never_raises_on_unexpected_units() -> None:
    # The renderer only catches its own decline errors.
    assert DBX.date_diff("FORTNIGHT", "s", "e") == "TIMESTAMPDIFF(FORTNIGHT, s, e)"
    assert DBX.last_day("d", "WEEK") == "LAST_DAY(d)"


# --- dialect: date-difference logic matches DAX ------------------------------------
# The same calendar arithmetic as the SQL above, in Python, checked against
# the DAX documentation's example (DATEDIFF from 2019-07-01 to 2021-12-31:
# Year 2, Quarter 9, Month 29, Week 130, Day 914) and boundary cases where
# Databricks' own DATEDIFF (whole elapsed units) would differ.


def _dayofweek(d: date) -> int:  # Databricks DAYOFWEEK: 1 = Sunday ... 7 = Saturday
    return d.isoweekday() % 7 + 1


def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def _boundaries(unit: str, s: date, e: date) -> int:
    if unit == "YEAR":
        return e.year - s.year
    if unit == "QUARTER":
        return (e.year - s.year) * 4 + _quarter(e) - _quarter(s)
    if unit == "MONTH":
        return (e.year - s.year) * 12 + e.month - s.month
    if unit == "DAY":
        return (e - s).days
    if unit == "WEEK":
        week_start = lambda d: d - timedelta(days=_dayofweek(d) - 1)  # noqa: E731
        return (week_start(e) - week_start(s)).days // 7
    raise AssertionError(unit)


@pytest.mark.parametrize(
    "unit, expected", [("YEAR", 2), ("QUARTER", 9), ("MONTH", 29), ("WEEK", 130), ("DAY", 914)]
)
def test_date_diff_logic_matches_dax_doc_example(unit, expected) -> None:
    assert _boundaries(unit, date(2019, 7, 1), date(2021, 12, 31)) == expected


@pytest.mark.parametrize(
    "unit, s, e, expected",
    [
        ("MONTH", date(2026, 1, 31), date(2026, 2, 1), 1),  # Databricks DATEDIFF(MONTH) gives 0
        ("YEAR", date(2025, 12, 31), date(2026, 1, 1), 1),  # Databricks DATEDIFF(YEAR) gives 0
        ("QUARTER", date(2026, 3, 31), date(2026, 4, 1), 1),
        ("DAY", date(2024, 2, 28), date(2024, 3, 1), 2),  # leap year
        ("WEEK", date(2026, 11, 21), date(2026, 11, 22), 1),  # Saturday -> Sunday
        ("WEEK", date(2026, 11, 22), date(2026, 11, 28), 0),  # Sunday -> Saturday
        ("MONTH", date(2026, 3, 1), date(2026, 1, 31), -2),  # reversed: negative
    ],
)
def test_date_diff_counts_boundaries(unit, s, e, expected) -> None:
    assert _boundaries(unit, s, e) == expected


# --- the renderer with Databricks rules --------------------------------------------

_KW = dict(table_alias="SALES", primary_table_name="Sales", date_alias="DATE")

_RENDERED = [
    ("SUM(Sales[Amount])", "SUM(SALES.`AMOUNT`::DOUBLE)"),
    ("DIVIDE(SUM(Sales[Amount]), SUM(Sales[Qty]))", "SUM(SALES.`AMOUNT`::DOUBLE) / NULLIF(SUM(SALES.`QTY`::DOUBLE), 0)"),
    ("DISTINCTCOUNT(Sales[Customer ID])", "COUNT(DISTINCT SALES.`CUSTOMER_ID`)"),
    ("VALUE(Sales[Code])", "TRY_CAST(SALES.`CODE` AS DOUBLE)"),
    (
        "DATEDIFF(MIN(Sales[OrderDate]), MAX(Sales[ShipDate]), MONTH)",
        "((YEAR(MAX(SALES.`SHIPDATE`)) - YEAR(MIN(SALES.`ORDERDATE`))) * 12 "
        "+ MONTH(MAX(SALES.`SHIPDATE`)) - MONTH(MIN(SALES.`ORDERDATE`)))",
    ),
    (
        "TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])",
        "SUM(CASE WHEN COL_DATE.`COL_DATE` >= TRUNC(CURRENT_DATE(), 'YEAR') "
        "AND COL_DATE.`COL_DATE` <= CURRENT_DATE() THEN SALES.`AMOUNT`::DOUBLE END)",
    ),
]


@pytest.mark.parametrize("dax, expected", _RENDERED)
def test_renderer_spells_databricks(dax, expected) -> None:
    assert try_ast_translate(dax, rules=DATABRICKS_RULES, **_KW) == expected


_SAMPLE_DAX = [
    "SUM(Sales[Amount])",
    "AVERAGE(Sales[Unit Price])",
    "MIN(Sales[Amount]) + MAX(Sales[Amount])",
    "COUNTROWS(Sales)",
    "DISTINCTCOUNT(Sales[Customer ID])",
    "DIVIDE(SUM(Sales[Amount]), SUM(Sales[Qty]), 0)",
    'CALCULATE(SUM(Sales[Amount]), Sales[Region] = "West")',
    'CALCULATE(SUM(Sales[Amount]), Sales[Region] IN {"West", "East"})',
    'SUMX(FILTER(Sales, Sales[Status] = "Won"), Sales[Amount])',
    "SUMX(Sales, Sales[Qty] * Sales[Price])",
    "AVERAGEX(Sales, Sales[Qty] * Sales[Price])",
    "IF(SUM(Sales[Qty]) > 10, 1, 0)",
    "VALUE(Sales[Code])",
    "DATEDIFF(MIN(Sales[OrderDate]), MAX(Sales[ShipDate]), DAY)",
    "DATEDIFF(MIN(Sales[OrderDate]), MAX(Sales[ShipDate]), WEEK)",
    "DATEDIFF(MIN(Sales[OrderDate]), MAX(Sales[ShipDate]), YEAR)",
    "TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])",
    "TOTALQTD(SUM(Sales[Amount]), 'Date'[Date])",
    "TOTALMTD(SUM(Sales[Amount]), 'Date'[Date])",
    "CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))",
    "CALCULATE(SUM(Sales[Amount]), DATEADD('Date'[Date], -1, MONTH))",
    "CALCULATE(SUM(Sales[Amount]), PREVIOUSMONTH('Date'[Date]))",
    "CALCULATE(SUM(Sales[Amount]), DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))",
    "SUM(Sales[Amount]) * 100",
]


@pytest.mark.parametrize("dax", _SAMPLE_DAX)
def test_no_snowflake_spelling_reaches_databricks_sql(dax) -> None:
    errors: list = []
    sql = try_ast_translate(dax, rules=DATABRICKS_RULES, render_errors=errors, **_KW)
    snowflake = try_ast_translate(dax, **_KW)
    # Databricks renders whatever Snowflake renders (same shared renderer).
    assert (sql is None) == (snowflake is None), (dax, errors)
    if sql is not None:
        for token in _SNOWFLAKE_ONLY:
            # whole words only: "DATEDIFF(" must not count as "IFF("
            pattern = re.escape(token) if not token[0].isalpha() else r"(?<![A-Za-z_])" + re.escape(token)
            assert not re.search(pattern, sql), (dax, token, sql)


def test_window_measures_are_declined_not_spelled_for_snowflake() -> None:
    dax = 'CALCULATE(COUNTA(Store[StoreType]), FILTER(ALL(Store), Store[StoreType] = "New Store"))'
    errors: list = []
    assert try_ast_translate(
        dax, table_alias="STORE", primary_table_name="Store", rules=DATABRICKS_RULES, render_errors=errors
    ) is None
    assert errors == ["not applicable: needs window metric"]


def test_translator_with_databricks_rules() -> None:
    result = DAXTranslator().translate(
        "SUM(Sales[Amount])", "SALES", "Sales", metric_name="M", skip_tier5=True, rules=DATABRICKS_RULES
    )
    assert result.sql == "SUM(SALES.`AMOUNT`::DOUBLE)"
    text = '"Sales: " & FORMAT(SUM(Sales[Amount]), "0")'
    declined = DAXTranslator().translate(text, "SALES", "Sales", metric_name="M", skip_tier5=True, rules=DATABRICKS_RULES)
    assert declined.failure_reason == "not applicable: text result"


# --- TargetBackend -----------------------------------------------------------------


def test_backend_registry() -> None:
    assert "databricks" in registered_backends()
    backend = get_backend("DataBricks")
    assert isinstance(backend, DatabricksBackend) and isinstance(backend, TargetBackend)
    assert backend.rules is DATABRICKS_RULES
    with pytest.raises(UnsupportedBackendError):
        get_backend("snowflake")  # Snowflake keeps its existing emitter


@pytest.mark.parametrize("step", ["match_tables", "plan", "write", "deploy", "verify"])
def test_unbuilt_steps_refuse_clearly(step) -> None:
    with pytest.raises(TargetBackendNotReady, match="not built yet"):
        getattr(get_backend("databricks"), step)(None)


def test_backend_refuses_other_platforms_rules() -> None:
    with pytest.raises(ValueError):
        DatabricksBackend(SNOWFLAKE_RULES)
    other = dataclasses.replace(DATABRICKS_RULES, target="other")
    with pytest.raises(ValueError):
        DatabricksBackend(other)


# --- the planner flag ----------------------------------------------------------------


def test_planner_flag_defaults_off() -> None:
    assert DatabricksBehavior().use_planner_path is False
    assert ConnectorBehavior().databricks.use_planner_path is False


def test_planner_flag_is_read_from_behavior_config() -> None:
    behavior = ConnectorBehavior(**{"databricks": {"use_planner_path": True}})
    assert behavior.databricks.use_planner_path is True
