"""The DAX renderer spells SQL through the dialect it is given (Phase 2b).

Uses a test-only dialect with deliberately different spelling, so each
family moved out of the renderer (quoting, float cast, IFF / TRY_TO_NUMBER /
TO_VARCHAR, date functions) is shown to come from the dialect, and the
translation cache is shown to keep dialects apart. Snowflake output itself
is covered byte for byte by Tests/golden.
"""

from __future__ import annotations

from semabridge.converter.dax_ast_parser import try_ast_translate
from semabridge.target_rules import SNOWFLAKE_RULES
from semabridge.target_rules.snowflake import SnowflakeDialect


class _MarkerDialect(SnowflakeDialect):
    """Snowflake rules with every moved spelling replaced by a marker."""

    name = "marker-test"
    identifier_quote = "`"
    float_cast_suffix = "::DOUBLE"
    iff_function = "IF"

    def quote_identifier(self, name: str) -> str:
        return f"`{name}`"

    def try_to_number(self, expr: str) -> str:
        return f"TRY_CAST({expr} AS DOUBLE)"

    def to_varchar(self, expr: str) -> str:
        return f"CAST({expr} AS STRING)"

    def current_date(self) -> str:
        return "TODAY_MARK()"

    def current_timestamp(self) -> str:
        return "NOW_MARK()"

    def date_add(self, unit: str, amount: str, expr: str) -> str:
        return f"ADD_MARK({unit}, {amount}, {expr})"

    def date_trunc(self, unit: str, expr: str) -> str:
        return f"TRUNC_MARK('{unit}', {expr})"

    def last_day(self, expr: str, unit: str) -> str:
        return f"LASTDAY_MARK({expr}, '{unit}')"

    def date_diff(self, unit: str, start: str, end: str) -> str:
        return f"DIFF_MARK({unit}, {start}, {end})"


MARK = _MarkerDialect()


def _both(dax: str, **kwargs):
    kwargs.setdefault("table_alias", "SALES")
    kwargs.setdefault("date_alias", "DATE")
    snowflake = try_ast_translate(dax, **kwargs)
    marked = try_ast_translate(dax, dialect=MARK, **kwargs)
    return snowflake, marked


def test_default_is_snowflake() -> None:
    dax = "SUM(Sales[Amount])"
    assert try_ast_translate(dax, table_alias="SALES") == try_ast_translate(
        dax, table_alias="SALES", dialect=SNOWFLAKE_RULES.dialect
    ) == 'SUM(SALES."AMOUNT"::FLOAT)'


def test_quoting_and_float_cast_follow_dialect() -> None:
    snowflake, marked = _both("SUM(Sales[Amount])")
    assert snowflake == 'SUM(SALES."AMOUNT"::FLOAT)'
    assert marked == "SUM(SALES.`AMOUNT`::DOUBLE)"


def test_cache_keeps_dialects_apart() -> None:
    # Same DAX and context, rendered Snowflake first, then the marker
    # dialect, then Snowflake again: each must get its own spelling.
    dax = "SUM(Sales[Qty]) * 3"
    first = try_ast_translate(dax, table_alias="SALES")
    marked = try_ast_translate(dax, table_alias="SALES", dialect=MARK)
    again = try_ast_translate(dax, table_alias="SALES")
    assert first == again
    assert '"' in first and "`" not in first
    assert "`" in marked and '"' not in marked


def test_conditionals_follow_dialect() -> None:
    _, divide = _both("DIVIDE(SUM(Sales[Amount]), SUM(Sales[Cost]), 0)")
    assert divide.startswith("IF(") and "IFF(" not in divide

    _, iferror = _both("IFERROR(SUM(Sales[Amount]) / SUM(Sales[Cost]), 0)")
    assert "TRY_CAST(CAST(" in iferror and "TRY_TO_NUMBER" not in iferror and "TO_VARCHAR" not in iferror

    _, value = _both("SUMX(Sales, VALUE(Sales[Code]))")
    assert "TRY_CAST(SALES.`CODE` AS DOUBLE)" in value

    _, selected = _both('SELECTEDVALUE(Sales[Status], "none")')
    assert "IFF(" not in selected and selected.startswith("IF(")


def test_dates_follow_dialect() -> None:
    _, today = _both("DATEDIFF(MAX(Sales[Order Date]), TODAY(), DAY)")
    assert today == "DIFF_MARK(DAY, MAX(SALES.`ORDER_DATE`), TODAY_MARK())"

    _, now = _both("DATEDIFF(MAX(Sales[Order Date]), NOW(), HOUR)")
    assert "NOW_MARK()" in now

    _, ytd = _both("TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])")
    assert "TRUNC_MARK('YEAR', TODAY_MARK())" in ytd
    assert "CURRENT_DATE" not in ytd and "DATE_TRUNC" not in ytd

    _, sply = _both("CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))")
    assert "ADD_MARK(YEAR, -1, TODAY_MARK())" in sply and "DATEADD" not in sply

    _, period = _both("CALCULATE(SUM(Sales[Amount]), DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))")
    assert "ADD_MARK(MONTH, -3, TODAY_MARK())" in period

    _, full_period = _both(
        "CALCULATE([YTD Sales], PREVIOUSYEAR('Date'[Date]))",
        measure_sql_map={
            "YTD Sales": "SUM(CASE WHEN DATE.`DATE` >= TRUNC_MARK('YEAR', TODAY_MARK()) "
            "AND DATE.`DATE` <= TODAY_MARK() THEN SALES.`AMOUNT`::DOUBLE END)"
        },
    )
    assert "LASTDAY_MARK(ADD_MARK(YEAR, -1, TODAY_MARK()), 'YEAR')" in full_period


def test_anchor_flags_follow_dialect() -> None:
    _, ytd_flag = _both(
        "TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])", anchor_flag_map={("YTD",): "FLAG_YTD"}
    )
    assert "SALES.`FLAG_YTD`" in ytd_flag
