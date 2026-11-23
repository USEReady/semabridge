"""Final gate: no deployed metric may be anchored to today's date unless its DAX uses TODAY()/NOW()."""
import types

import pytest

from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

gate = SnowflakeEmitter._enforce_no_unanchored_current_date


def _run(sql, dax):
    m = types.SimpleNamespace(unique_name="M", sql_expression=sql, expression=dax,
                              sync_failure_reason=None, sync_enabled=True)
    gate(types.SimpleNamespace(ti_failed_closed_metrics=[]), types.SimpleNamespace(metrics=[m]))
    return m


TODAY_SQL = [
    "SUM(CASE WHEN T.D <= CURRENT_DATE() THEN T.X END)",
    "SUM(CASE WHEN T.D <= current_date() THEN T.X END)",
    "SUM(CASE WHEN T.D <= CURRENT_DATE THEN T.X END)",
    "SUM(CASE WHEN T.D <= Current_Date ( ) THEN T.X END)",
    "SUM(IFF(T.D BETWEEN DATE_TRUNC('YEAR', CURRENT_DATE()) AND CURRENT_DATE(), T.X, 0))",
    "SUM(CASE WHEN YEAR(T.D) = YEAR(DATEADD(YEAR,-1,CURRENT_DATE())) THEN T.X END)",
    "SUM(CASE WHEN T.D > DATEADD(MONTH, -12, CURRENT_DATE) THEN T.X END)",
    "COUNT(CASE WHEN MONTH(T.D)=MONTH(CURRENT_DATE()) THEN 1 END)",
]
ORDINARY_DAX = [
    "TOTALYTD(SUM(T[X]), 'Date'[Date])",
    "CALCULATE(SUM(T[X]), SAMEPERIODLASTYEAR('Date'[Date]))",
    "CALCULATE([M], PREVIOUSMONTH('Date'[Date]))",
    "CALCULATE(SUM(T[X]), DATEADD('Date'[Date], -1, YEAR))",
    "CALCULATE(SUM(T[X]), DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -12, MONTH))",
    "SUM(T[KnownValue])",                      # contains 'NOW' inside a word
    "CALCULATE([Todays Sales], ALL('Date'))",  # contains 'Today' inside a name, no call
    "",
]
TODAY_DAX = [
    "CALCULATE(SUM(T[X]), 'Date'[Date] <= TODAY())",
    "CALCULATE(SUM(T[X]), 'Date'[Date] <= today())",
    "CALCULATE(SUM(T[X]), 'Date'[Date] <= TODAY ( ))",
    "DATEDIFF(T[Start], NOW(), DAY)",
    "IF(YEAR(TODAY()) = 2020, 1, 0)",
]


@pytest.mark.parametrize("sql", TODAY_SQL)
@pytest.mark.parametrize("dax", ORDINARY_DAX)
def test_today_anchored_sql_without_today_in_dax_fails_closed(sql, dax):
    m = _run(sql, dax)
    assert m.sql_expression is None and m.sync_enabled is False
    assert "today" in (m.sync_failure_reason or "").lower()


@pytest.mark.parametrize("sql", TODAY_SQL[:4])
@pytest.mark.parametrize("dax", TODAY_DAX)
def test_dax_using_today_or_now_keeps_current_date(sql, dax):
    m = _run(sql, dax)
    assert m.sql_expression == sql and m.sync_enabled is True


@pytest.mark.parametrize("sql", [
    "SUM(T.X)", "SUM(CASE WHEN T.IS_SPLY_YEAR THEN T.X END)", "SUM(T.CURRENT_DATE_FLAG)",
    "COUNT(T.LAST_CURRENT_DATEKEY)", "MAX(T.D)", None, "",
])
def test_metrics_without_current_date_are_untouched(sql):
    m = _run(sql, "SUM(T[X])")
    assert m.sql_expression == sql and m.sync_enabled is True
