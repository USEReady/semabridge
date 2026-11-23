"""Time-intelligence flag SQL (build_shape_boolean_sql) executed for real.

The generated Snowflake boolean is run in DuckDB over a 10-year calendar and the
selected dates are compared with an independent Python reference, for many
anchors (month ends, leap days, quarter/year boundaries) and shapes.
"""
import datetime as dt
import re

import duckdb
import pytest
from dateutil.relativedelta import relativedelta

from semabridge.converter.time_intelligence_shapes import build_shape_boolean_sql

ANCHORS = [dt.date(*a) for a in [
    (2014, 12, 31), (2015, 1, 1), (2015, 1, 31), (2015, 2, 28), (2016, 2, 29), (2016, 3, 1),
    (2016, 3, 31), (2016, 4, 1), (2016, 6, 30), (2016, 7, 1), (2016, 9, 30), (2016, 10, 1),
    (2017, 5, 15), (2017, 8, 31), (2017, 11, 30), (2017, 12, 1), (2018, 12, 31), (2019, 6, 15),
    (2020, 2, 29), (2020, 12, 31), (2021, 1, 15), (2021, 3, 31), (2023, 7, 31), (2024, 2, 29),
]]
CAL_START, CAL_END = dt.date(2010, 1, 1), dt.date(2026, 12, 31)


def _qstart(d):
    return dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _qend(d):
    return _qstart(d) + relativedelta(months=3, days=-1)


def _mend(d):
    return d.replace(day=1) + relativedelta(months=1, days=-1)


def _fy_start(d, k):
    s = dt.date(d.year, k + 1, 1)
    return s if d >= s else dt.date(d.year - 1, k + 1, 1)


def _rng(a, b):
    return (a, b)


REFERENCE = {
    ("YTD",): lambda a: _rng(dt.date(a.year, 1, 1), a),
    ("MTD",): lambda a: _rng(a.replace(day=1), a),
    ("QTD",): lambda a: _rng(_qstart(a), a),
    ("CURRENT_YEAR",): lambda a: _rng(dt.date(a.year, 1, 1), dt.date(a.year, 12, 31)),
    ("CURRENT_QUARTER",): lambda a: _rng(_qstart(a), _qend(a)),
    ("CURRENT_MONTH",): lambda a: _rng(a.replace(day=1), _mend(a)),
    ("CURRENT_DAY",): lambda a: _rng(a, a),
    ("R3M",): lambda a: _rng(a - relativedelta(months=3) + dt.timedelta(days=1), a),
    ("R12M",): lambda a: _rng(a - relativedelta(months=12) + dt.timedelta(days=1), a),
    ("YTD", "SPLY_YEAR"): lambda a: _rng(dt.date((a - relativedelta(years=1)).year, 1, 1), a - relativedelta(years=1)),
    ("MTD", "SPLY_YEAR"): lambda a: _rng((a - relativedelta(years=1)).replace(day=1), a - relativedelta(years=1)),
    ("QTD", "SPLY_YEAR"): lambda a: _rng(_qstart(a - relativedelta(years=1)), a - relativedelta(years=1)),
    ("SPLY_YEAR",): lambda a: _rng(dt.date((a - relativedelta(years=1)).year, 1, 1), a - relativedelta(years=1)),
    ("PREV_YEAR",): lambda a: _rng(dt.date(a.year - 1, 1, 1), dt.date(a.year - 1, 12, 31)),
    ("PREV_MONTH",): lambda a: _rng((a - relativedelta(months=1)).replace(day=1), _mend(a - relativedelta(months=1))),
    ("PREV_QUARTER",): lambda a: _rng(_qstart(a - relativedelta(months=3)), _qend(a - relativedelta(months=3))),
    ("SPLY_2_YEAR",): lambda a: _rng(dt.date((a - relativedelta(years=2)).year, 1, 1), a - relativedelta(years=2)),
    ("PREV_2_MONTH",): lambda a: _rng((a - relativedelta(months=2)).replace(day=1), _mend(a - relativedelta(months=2))),
    ("FYTD06",): lambda a: _rng(_fy_start(a, 6), a),
    ("FYTD03",): lambda a: _rng(_fy_start(a, 3), a),
    ("FYTD06", "SPLY_YEAR"): lambda a: _rng(_fy_start(a - relativedelta(years=1), 6), a - relativedelta(years=1)),
}


def _dateadd(unit, n, d):
    unit = unit.upper()
    if unit == "YEAR":
        return d + relativedelta(years=n)
    if unit == "QUARTER":
        return d + relativedelta(months=3 * n)
    if unit == "MONTH":
        return d + relativedelta(months=n)
    if unit == "DAY":
        return d + dt.timedelta(days=n)
    raise ValueError(unit)


def _date_trunc(unit, d):
    unit = unit.upper()
    if unit == "YEAR":
        return dt.date(d.year, 1, 1)
    if unit == "QUARTER":
        return _qstart(d)
    if unit == "MONTH":
        return d.replace(day=1)
    return d


def _last_day(d, unit="MONTH"):
    unit = unit.upper()
    if unit == "YEAR":
        return dt.date(d.year, 12, 31)
    if unit == "QUARTER":
        return _qend(d)
    return _mend(d)


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.create_function("SB_DATEADD", _dateadd, ["VARCHAR", "INTEGER", "DATE"], "DATE")
    c.create_function("SB_LAST_DAY", _last_day, ["DATE", "VARCHAR"], "DATE")
    c.create_function("SB_DATE_TRUNC", _date_trunc, ["VARCHAR", "DATE"], "DATE")
    c.execute(f"CREATE TABLE cal AS SELECT CAST(d AS DATE) AS d FROM range(DATE '{CAL_START}', DATE '{CAL_END}' + 1, INTERVAL 1 DAY) t(d)")
    return c


def _to_duckdb(sql):
    sql = re.sub(r"\bDATEADD\(\s*(\w+)\s*,", r"SB_DATEADD('\1',", sql)
    sql = re.sub(r"\bLAST_DAY\(", "SB_LAST_DAY(", sql)
    sql = re.sub(r"\bDATE_TRUNC\(", "SB_DATE_TRUNC(", sql)
    return sql


CASES = [(s, a) for s in REFERENCE for a in ANCHORS]


@pytest.mark.parametrize("shape,anchor", CASES, ids=[f"{'+'.join(s)}@{a}" for s, a in CASES])
def test_shape_selects_exactly_the_reference_dates(con, shape, anchor):
    sql = build_shape_boolean_sql(shape, "d", f"DATE '{anchor}'")
    assert "CURRENT_DATE" not in sql.upper(), f"shape {shape} uses today's date: {sql}"
    rows = con.execute(f"SELECT MIN(d), MAX(d), COUNT(*) FROM cal WHERE {_to_duckdb(sql)}").fetchone()
    lo, hi = REFERENCE[shape](anchor)
    expected = (lo, hi, (hi - lo).days + 1)
    assert (rows[0], rows[1], rows[2]) == expected, f"{shape} @ {anchor}: got {rows}, expected {expected}\nSQL: {sql}"


@pytest.mark.parametrize("bad", [(), ("NOPE",), ("YTD_X",), ("SPLY_X_YEAR",)])
def test_unsupported_shapes_are_rejected(bad):
    with pytest.raises(ValueError):
        build_shape_boolean_sql(bad, "d", "DATE '2020-01-01'")
