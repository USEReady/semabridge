"""Semantic-fidelity regressions for DAX -> semantic-view metric translation.

Product rule: a measure either translates to SQL with exactly the DAX
semantics, or is declined with a specific, user-readable failure_reason.
Every test uses random table/column/measure names (nothing model-specific)
and, where feasible, evaluates the emitted SQL in DuckDB against the value
DAX would return.
"""
import datetime as dt
import logging
import re
import uuid

import duckdb
import pytest

from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS,
    ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE,
    DaxAstParser,
    DaxSqlRenderer,
)
from semabridge.converter.dax_rule_translator import translate_iterator
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.time_intelligence_shapes import (
    build_shape_boolean_sql,
    discover_time_intelligence_shapes,
)
from semabridge.sml.models import DataType, SMLColumn, SMLDataset, SMLMetric, SMLRelationship

logging.disable(logging.CRITICAL)


def rid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


# ---------------------------------------------------------------------------
# A random single-fact model (plus one dimension) shared by every test.
# ---------------------------------------------------------------------------
class Model:
    def __init__(self):
        self.fact = rid("Fct")
        self.dim = rid("Dim")
        self.other = rid("Oth")
        self.alias = self.fact.upper()
        self.c_key = rid("Key")
        self.c_chan = rid("Chan")
        self.c_amt = rid("Amt")
        self.c_cost = rid("Cost")
        self.c_qty = rid("Qty")
        self.c_disc = rid("Disc")
        self.c_date = rid("Dt")
        self.c_ship = rid("Ship")
        self.d_key = rid("DKey")
        self.d_cat = rid("Cat")
        self.m_sales = rid("Sales ")
        self.m_cost = rid("Cost ")
        self.m_qty = rid("Qty ")
        self.m_margin = rid("Margin ")
        self.m_rows = rid("Rows ")
        self.m_ytd = rid("Ytd ")
        A = self.alias
        q = lambda c: f'{A}."{c.upper()}"'
        self.metrics = [
            SMLMetric(unique_name=self.m_sales, label=self.m_sales, dataset=self.fact,
                      expression=f"SUM({self.fact}[{self.c_amt}])", sql_expression=f"SUM({q(self.c_amt)})"),
            SMLMetric(unique_name=self.m_cost, label=self.m_cost, dataset=self.fact,
                      expression=f"SUM({self.fact}[{self.c_cost}])", sql_expression=f"SUM({q(self.c_cost)})"),
            SMLMetric(unique_name=self.m_qty, label=self.m_qty, dataset=self.fact,
                      expression=f"SUM({self.fact}[{self.c_qty}])", sql_expression=f"SUM({q(self.c_qty)})"),
            SMLMetric(unique_name=self.m_margin, label=self.m_margin, dataset=self.fact,
                      expression=f"[{self.m_sales}] - [{self.m_cost}]",
                      sql_expression=f"SUM({q(self.c_amt)}) - SUM({q(self.c_cost)})"),
            SMLMetric(unique_name=self.m_rows, label=self.m_rows, dataset=self.fact,
                      expression=f"COUNTROWS({self.fact})", sql_expression="COUNT(*)"),
            SMLMetric(unique_name=self.m_ytd, label=self.m_ytd, dataset=self.fact,
                      expression=f"TOTALYTD([{self.m_sales}], {self.fact}[{self.c_date}])",
                      sql_expression=(f"SUM(CASE WHEN {q(self.c_date)} >= DATE_TRUNC('YEAR', CURRENT_DATE()) "
                                      f"AND {q(self.c_date)} <= CURRENT_DATE() THEN {q(self.c_amt)} ELSE NULL END)")),
        ]
        self.rels = [
            SMLRelationship(unique_name=rid("R"), from_dataset=self.fact, from_columns=[self.c_key],
                            to_dataset=self.dim, to_columns=[self.d_key], is_active=True),
            SMLRelationship(unique_name=rid("R"), from_dataset=self.fact, from_columns=[self.c_ship],
                            to_dataset=self.dim, to_columns=[self.d_key], is_active=False),
        ]
        col = lambda n, t: SMLColumn(unique_name=n, data_type=t)
        self.datasets = [
            SMLDataset(unique_name=self.fact, columns=[
                col(self.c_key, DataType.INTEGER), col(self.c_chan, DataType.STRING),
                col(self.c_amt, DataType.DECIMAL), col(self.c_cost, DataType.DECIMAL),
                col(self.c_qty, DataType.INTEGER), col(self.c_disc, DataType.DECIMAL),
                col(self.c_date, DataType.DATE), col(self.c_ship, DataType.DATE),
            ]),
            SMLDataset(unique_name=self.dim, columns=[col(self.d_key, DataType.INTEGER), col(self.d_cat, DataType.STRING)]),
        ]

    def tr(self, dax, typed=True, **kw):
        kw.setdefault("skip_tier5", True)
        if typed:
            kw.setdefault("datasets", self.datasets)
        return DAXTranslator().translate(
            dax, self.alias, self.fact, metric_name=rid("M"), metrics_context=self.metrics,
            relationships=self.rels, **kw,
        )

    # ---------------------------------------------------------------- duckdb
    def db(self, rows):
        """rows: (chan, amt, cost, qty, disc, date)"""
        con = duckdb.connect()
        c = self
        con.execute(
            f'CREATE TABLE {c.alias} ("{c.c_chan.upper()}" VARCHAR, "{c.c_amt.upper()}" DOUBLE, '
            f'"{c.c_cost.upper()}" DOUBLE, "{c.c_qty.upper()}" DOUBLE, "{c.c_disc.upper()}" DOUBLE, '
            f'"{c.c_date.upper()}" DATE)'
        )
        for r in rows:
            con.execute(f"INSERT INTO {c.alias} VALUES (?, ?, ?, ?, ?, ?)", list(r))
        return con

    def eval(self, con, sql, anchor="2024-08-15", group=None):
        duck = to_duck(sql, anchor)
        if group:
            g = f'{self.alias}."{group.upper()}"'
            return dict(con.execute(f"SELECT {g}, {duck} FROM {self.alias} GROUP BY 1").fetchall())
        return con.execute(f"SELECT {duck} FROM {self.alias}").fetchone()[0]


def _split_top(s):
    out, depth, cur, ins = [], 0, "", False
    for ch in s:
        if ch == "'":
            ins = not ins
        if not ins and ch == "(":
            depth += 1
        elif not ins and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not ins:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    out.append(cur.strip())
    return out


def _rewrite_call(sql, name, fn):
    while True:
        i = sql.upper().rfind(name + "(")
        if i < 0:
            return sql
        j, depth = i + len(name), 0
        for k in range(j, len(sql)):
            if sql[k] == "(":
                depth += 1
            elif sql[k] == ")":
                depth -= 1
                if depth == 0:
                    break
        args = _split_top(sql[j + 1:k])
        sql = sql[:i] + fn(args) + sql[k + 1:]


def _shift(unit, n, expr):
    unit = unit.strip("'").upper()
    f = {"YEAR": f"to_years(CAST({n} AS INTEGER))", "MONTH": f"to_months(CAST({n} AS INTEGER))",
         "QUARTER": f"to_months(CAST(3 * ({n}) AS INTEGER))", "DAY": f"to_days(CAST({n} AS INTEGER))"}[unit]
    return f"CAST(({expr}) + {f} AS DATE)"


def to_duck(sql, anchor):
    """Snowflake -> DuckDB for the handful of date functions the renderer emits."""
    s = sql.replace("CURRENT_DATE()", f"DATE '{anchor}'").replace("::FLOAT", "::DOUBLE")
    s = _rewrite_call(s, "DATEADD", lambda a: _shift(a[0], a[1], a[2]))
    s = _rewrite_call(
        s, "LAST_DAY",
        lambda a: f"CAST(date_trunc('{a[1].strip(chr(39)).lower()}', {a[0]}) + "
                  f"{_shift(a[1], 1, '0').split(' + ')[1].split(' AS DATE')[0]} - to_days(1) AS DATE)"
        if len(a) > 1 else f"last_day({a[0]})",
    )
    s = re.sub(r"DATE_TRUNC\('(\w+)'", lambda m: f"date_trunc('{m.group(1).lower()}'", s)
    return s


@pytest.fixture
def m():
    return Model()


def F(m, col):
    return f"{m.fact}[{col}]"


# ---------------------------------------------------------------------------
# 1. Non-time filters are kept alongside a time-intelligence filter
# ---------------------------------------------------------------------------
def test_time_intelligence_keeps_other_calculate_filters(m):
    dax = (f'CALCULATE([{m.m_sales}], SAMEPERIODLASTYEAR({F(m, m.c_date)}), '
           f'{F(m, m.c_chan)} = "Online")')
    r = m.tr(dax)
    assert r.is_success, r.failure_reason
    assert f'"{m.c_chan.upper()}" = \'Online\'' in r.sql
    con = m.db([
        ("Online", 10, 0, 1, None, dt.date(2023, 3, 1)),   # prior YTD, Online -> counted
        ("Store", 20, 0, 1, None, dt.date(2023, 3, 1)),    # prior YTD, Store
        ("Online", 40, 0, 1, None, dt.date(2023, 12, 1)),  # prior year after cutoff
        ("Online", 80, 0, 1, None, dt.date(2024, 3, 1)),   # current year
    ])
    assert m.eval(con, r.sql) == 10


# ---------------------------------------------------------------------------
# 2. TOTALYTD filter argument
# ---------------------------------------------------------------------------
def test_totalytd_filter_argument_is_applied(m):
    r = m.tr(f'TOTALYTD([{m.m_sales}], {F(m, m.c_date)}, {F(m, m.c_chan)} = "Online")')
    assert r.is_success, r.failure_reason
    con = m.db([
        ("Online", 10, 0, 1, None, dt.date(2024, 2, 1)),
        ("Store", 20, 0, 1, None, dt.date(2024, 2, 1)),
        ("Online", 40, 0, 1, None, dt.date(2023, 2, 1)),
    ])
    assert m.eval(con, r.sql) == 10


# ---------------------------------------------------------------------------
# 3. Fiscal year end
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("form", ["TOTALYTD({m}, {d}, \"6/30\")", "CALCULATE({m}, DATESYTD({d}, \"6/30\"))"])
def test_fiscal_year_end_shifts_year_start(m, form):
    r = m.tr(form.format(m=f"[{m.m_sales}]", d=F(m, m.c_date)))
    assert r.is_success, r.failure_reason
    con = m.db([
        ("x", 1, 0, 1, None, dt.date(2024, 6, 30)),   # previous fiscal year
        ("x", 10, 0, 1, None, dt.date(2024, 7, 1)),   # fiscal YTD
        ("x", 100, 0, 1, None, dt.date(2024, 8, 15)),  # fiscal YTD (anchor)
        ("x", 1000, 0, 1, None, dt.date(2024, 8, 16)),  # after anchor
    ])
    assert m.eval(con, r.sql, anchor="2024-08-15") == 110
    # anchor before the fiscal year boundary -> previous July 1 start
    assert m.eval(con, r.sql, anchor="2024-06-30") == 1


def test_fiscal_year_end_not_month_end_is_declined(m):
    r = m.tr(f'TOTALYTD([{m.m_sales}], {F(m, m.c_date)}, "6/15")')
    assert not r.is_success
    assert "fiscal year-end not supported" in r.failure_reason


def test_fiscal_ytd_shape_flag_sql():
    sql = build_shape_boolean_sql(("FYTD03",), "d", "a")
    assert "DATEADD(MONTH, 3, DATE_TRUNC('YEAR', DATEADD(MONTH, -3, a)))" in sql


# ---------------------------------------------------------------------------
# 4. PARALLELPERIOD / PREVIOUSx return the FULL previous period
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mod", ["PARALLELPERIOD({d}, -1, YEAR)", "PREVIOUSYEAR({d})"])
def test_full_previous_year(m, mod):
    r = m.tr(f"CALCULATE([{m.m_sales}], {mod.format(d=F(m, m.c_date))})")
    assert r.is_success, r.failure_reason
    con = m.db([
        ("x", 1, 0, 1, None, dt.date(2023, 2, 1)),
        ("x", 10, 0, 1, None, dt.date(2023, 11, 30)),  # after the same-day cutoff: still counted
        ("x", 100, 0, 1, None, dt.date(2024, 2, 1)),
    ])
    assert m.eval(con, r.sql, anchor="2024-08-15") == 11


def test_previous_year_of_ytd_measure_is_full_previous_year(m):
    r = m.tr(f"CALCULATE([{m.m_ytd}], PREVIOUSYEAR({F(m, m.c_date)}))")
    assert r.is_success, r.failure_reason
    con = m.db([
        ("x", 1, 0, 1, None, dt.date(2023, 2, 1)),
        ("x", 10, 0, 1, None, dt.date(2023, 12, 31)),
        ("x", 100, 0, 1, None, dt.date(2024, 2, 1)),
    ])
    assert m.eval(con, r.sql, anchor="2024-08-15") == 11


def test_prev_year_shapes_are_full_period():
    assert build_shape_boolean_sql(("PREV_YEAR",), "d", "a") == "YEAR(d) = YEAR(LAST_DAY(DATEADD(YEAR, -1, a), 'YEAR'))"
    nested = build_shape_boolean_sql(("YTD", "PREV_YEAR"), "d", "a")
    assert "LAST_DAY(DATEADD(YEAR, -1, a), 'YEAR')" in nested
    assert "PREV_2_MONTH" and build_shape_boolean_sql(("PREV_2_MONTH",), "d", "a").startswith("YEAR(d)")


# ---------------------------------------------------------------------------
# 5. USERELATIONSHIP / 6. HASONEVALUE guard
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("extra", ["", ", {dim}[{cat}] = \"z\""])
def test_userelationship_declined_with_reason(m, extra):
    dax = (f"CALCULATE([{m.m_sales}], USERELATIONSHIP({F(m, m.c_ship)}, {m.dim}[{m.d_key}])"
           + extra.format(dim=m.dim, cat=m.d_cat) + ")")
    r = m.tr(dax)
    assert not r.is_success
    assert "USERELATIONSHIP requires an inactive-relationship path" in r.failure_reason


def test_hasonevalue_guard_declined_not_non_additive(m):
    r = m.tr(f"IF(HASONEVALUE({m.dim}[{m.d_cat}]), [{m.m_sales}], BLANK())")
    assert not r.is_success
    assert r.non_additive_dimension is None
    assert "HASONEVALUE" in r.failure_reason


def test_error_guard_unwrap_still_translates(m):
    r = m.tr(f'IF(NOT HASONEVALUE({m.dim}[{m.d_cat}]), ERROR("pick one"), [{m.m_sales}])')
    assert r.is_success, r.failure_reason


# ---------------------------------------------------------------------------
# 7. BLANK comparison semantics
# ---------------------------------------------------------------------------
ROWS = [
    ("Online", 10, 4, 2, None, dt.date(2024, 1, 1)),
    ("Store", 20, 8, None, 0.0, dt.date(2024, 1, 1)),
    (None, 30, 9, 3, 0.1, dt.date(2024, 1, 1)),
    ("Online", 40, 20, None, None, dt.date(2024, 1, 1)),
]


def test_numeric_column_equals_blank_matches_null_and_zero(m):
    r = m.tr(f"CALCULATE([{m.m_sales}], {F(m, m.c_disc)} = BLANK())")
    assert r.is_success, r.failure_reason
    assert "COALESCE(NULL" not in r.sql
    assert m.eval(m.db(ROWS), r.sql) == 70  # NULL, 0 and NULL rows


def test_numeric_column_not_equal_blank(m):
    r = m.tr(f"CALCULATE([{m.m_sales}], {F(m, m.c_disc)} <> BLANK())")
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == 30


def test_text_not_equal_literal_includes_blank_rows(m):
    r = m.tr(f'CALCULATE([{m.m_sales}], {F(m, m.c_chan)} <> "Online")')
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == 50  # Store + blank channel


def test_not_of_comparison_includes_blank_rows(m):
    r = m.tr(f'CALCULATE([{m.m_sales}], NOT({F(m, m.c_chan)} = "Online"))')
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == 50


def test_measure_equals_zero_treats_blank_as_zero(m):
    r = m.tr(f"IF([{m.m_qty}] = 0, 0, [{m.m_sales}] / [{m.m_qty}])")
    assert r.is_success, r.failure_reason
    by_chan = m.eval(m.db(ROWS), r.sql, group=m.c_chan)
    assert by_chan["Store"] == 0          # qty BLANK -> DAX BLANK = 0 is TRUE
    assert by_chan["Online"] == 25


def test_blank_comparison_on_untyped_column_declined_with_reason(m):
    r = m.tr(f"CALCULATE([{m.m_sales}], {F(m, m.c_disc)} = BLANK())", typed=False)
    assert not r.is_success
    assert "BLANK" in r.failure_reason


# ---------------------------------------------------------------------------
# 8. Regex strict tier: no ELSE 0, no multi-aggregate mangling
# ---------------------------------------------------------------------------
def test_strict_tier_empty_group_is_blank_not_zero(m):
    t = DAXTranslator()
    sql = t._try_strict_translation(f'CALCULATE([{m.m_sales}], {F(m, m.c_chan)} = "Store")', m.alias, m.metrics)
    assert sql is not None and "ELSE 0" not in sql


def test_strict_tier_declines_multi_aggregate_measure(m):
    t = DAXTranslator()
    assert t._parse_sql_aggregation('SUM(a."X") - SUM(a."Y")') is None
    assert t._try_strict_translation(f'CALCULATE([{m.m_margin}], {F(m, m.c_chan)} = "Online")', m.alias, m.metrics) is None


def test_filtered_multi_aggregate_measure_numeric(m):
    r = m.tr(f'CALCULATE([{m.m_margin}], {F(m, m.c_chan)} = "Online")')
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == (10 - 4) + (40 - 20)


# ---------------------------------------------------------------------------
# 9. Override semantics advisory; KEEPFILTERS = intersection
# ---------------------------------------------------------------------------
def test_boolean_filter_carries_override_advisory(m):
    r = m.tr(f'CALCULATE([{m.m_sales}], {m.dim}[{m.d_cat}] = "z")')
    assert r.is_success
    assert ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS in r.advisory_categories


def test_keepfilters_translated_without_override_advisory(m):
    r = m.tr(f'CALCULATE([{m.m_sales}], KEEPFILTERS({F(m, m.c_chan)} = "Online"))')
    assert r.is_success, r.failure_reason
    assert ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS not in r.advisory_categories
    assert m.eval(m.db(ROWS), r.sql, group=m.c_chan)["Online"] == 50


# ---------------------------------------------------------------------------
# 10. Malformed SQL shapes
# ---------------------------------------------------------------------------
def test_calculate_of_ratio_keeps_balanced_parens(m):
    r = m.tr(f'CALCULATE([{m.m_sales}] / [{m.m_qty}], {F(m, m.c_chan)} = "Online")')
    assert r.is_success, r.failure_reason
    assert r.sql.count("(") == r.sql.count(")")
    assert m.eval(m.db(ROWS), r.sql) == 50 / 2


@pytest.mark.parametrize("it", ["SUMX", "MAXX", "AVERAGEX"])
def test_iterator_over_values_declined(m, it):
    dax = f"{it}(VALUES({m.dim}[{m.d_cat}]), [{m.m_sales}])"
    r = m.tr(dax)
    assert not r.is_success
    assert "not applicable" in r.failure_reason and "VALUES" in r.failure_reason
    assert translate_iterator(dax, m.alias) is None


def test_sumx_over_own_table_of_measure_is_row_level(m):
    r = m.tr(f"SUMX({m.fact}, [{m.m_sales}])")
    assert r.is_success, r.failure_reason
    assert not re.search(r"SUM\s*\(\s*\(?\s*SUM", r.sql)
    assert m.eval(m.db(ROWS), r.sql) == 100


def test_iterator_over_other_table_of_measure_declined(m):
    r = m.tr(f"AVERAGEX({m.dim}, [{m.m_sales}])")
    assert not r.is_success
    assert "context transition" in r.failure_reason


def test_switch_true_is_searched_case(m):
    r = m.tr(f"SWITCH(TRUE(), [{m.m_sales}] > 1000, 3, [{m.m_sales}] > 50, 2, 1)")
    assert r.is_success, r.failure_reason
    assert "TRUE =" not in r.sql
    assert m.eval(m.db(ROWS), r.sql) == 2


# ---------------------------------------------------------------------------
# 11. CURRENT_DATE anchor advisory
# ---------------------------------------------------------------------------
def test_current_date_anchor_advisory(m):
    r = m.tr(f"TOTALYTD([{m.m_sales}], {F(m, m.c_date)})")
    assert r.is_success
    assert ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE in r.advisory_categories


def test_flag_anchor_has_no_current_date_advisory(m):
    r = m.tr(f"TOTALYTD(SUM({F(m, m.c_amt)}), {F(m, m.c_date)})", anchor_flag_map={("YTD",): "IS_YTD"})
    assert r.is_success
    assert "IS_YTD" in r.sql
    assert ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE not in r.advisory_categories


# ---------------------------------------------------------------------------
# 12. Previously reason-less declines
# ---------------------------------------------------------------------------
def test_countblank_numeric(m):
    r = m.tr(f"COUNTBLANK({F(m, m.c_disc)})")
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == 2


def test_in_list(m):
    r = m.tr(f'CALCULATE([{m.m_sales}], {F(m, m.c_chan)} IN {{"Online", "Store"}})')
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == 70


def test_isblank_filter(m):
    r = m.tr(f"CALCULATE([{m.m_rows}], ISBLANK({F(m, m.c_disc)}))")
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql) == 2


def test_datesbetween_literal_dates(m):
    r = m.tr(f"CALCULATE([{m.m_sales}], DATESBETWEEN({F(m, m.c_date)}, DATE(2024,1,1), DATE(2024,1,31)))")
    assert r.is_success, r.failure_reason
    con = m.db([("x", 1, 0, 1, None, dt.date(2023, 12, 31)), ("x", 10, 0, 1, None, dt.date(2024, 1, 31))])
    assert m.eval(con, r.sql) == 10


def test_datesinperiod_rolling_window(m):
    r = m.tr(f"CALCULATE([{m.m_sales}], DATESINPERIOD({F(m, m.c_date)}, MAX({F(m, m.c_date)}), -3, MONTH))")
    assert r.is_success, r.failure_reason
    con = m.db([
        ("x", 1, 0, 1, None, dt.date(2024, 5, 15)),   # exactly 3 months back: excluded
        ("x", 10, 0, 1, None, dt.date(2024, 5, 16)),
        ("x", 100, 0, 1, None, dt.date(2024, 8, 15)),
    ])
    assert m.eval(con, r.sql, anchor="2024-08-15") == 110
    assert ("R3M",) in discover_time_intelligence_shapes([m.metrics[0].model_copy(update={"expression": (
        f"CALCULATE([{m.m_sales}], DATESINPERIOD({F(m, m.c_date)}, MAX({F(m, m.c_date)}), -3, MONTH))")})])


def test_coalesce_measure(m):
    r = m.tr(f"COALESCE([{m.m_qty}], 0)")
    assert r.is_success, r.failure_reason
    assert m.eval(m.db(ROWS), r.sql, group=m.c_chan)["Store"] == 0


def test_related_inside_row_iterator(m):
    r = m.tr(f"SUMX({m.fact}, {F(m, m.c_qty)} * RELATED({m.dim}[{m.d_cat}]))")
    assert r.is_success, r.failure_reason
    assert f'{m.dim.upper()}."{m.d_cat.upper()}"' in r.sql


@pytest.mark.parametrize("dax_tpl", [
    "CLOSINGBALANCEMONTH(SUM({fact}[{amt}]), {fact}[{date}])",
    "CALCULATE([{sales}], TOPN(5, ALL({dim}[{cat}]), [{sales}]))",
    '"Total: " & FORMAT([{sales}], "0")',
    "COUNTBLANK({fact}[{chan}])",
    "SUMX(VALUES({fact}[{chan}]), [{sales}])",
])
def test_declines_always_carry_a_reason(m, dax_tpl):
    dax = dax_tpl.format(fact=m.fact, amt=m.c_amt, date=m.c_date, sales=m.m_sales, dim=m.dim,
                         cat=m.d_cat, chan=m.c_chan)
    r = m.tr(dax)
    assert not r.is_success
    assert r.failure_reason and len(r.failure_reason) > 10


def test_renderer_text_concat_parses_and_is_text_result():
    ast = DaxAstParser().parse('"a" & FORMAT([x], "0")')
    assert ast is not None
    assert DaxSqlRenderer(table_alias="T").render(ast) is None
