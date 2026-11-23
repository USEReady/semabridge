"""BI-Truth helpers: classification by metadata, never by guesses; honest values; strict project scope."""
import datetime as dt
import decimal
import json
import types
import uuid

import pytest

from semabridge.ai import bi_truth_service as bt

RATIO = [("CLOSE_PCT", None, None), ("Close %", None, None), ("Win Rate", None, None), ("Market Share", None, None),
         ("Conversion Ratio", None, None), ("Margin Pct", None, None), ("X", None, "DIVIDE(SUM(a),SUM(b))"),
         ("X", None, "SUM(a)/NULLIF(SUM(b),0)"), ("Percentage Complete", None, None), ("SLA_COMPLIANCE", "SLA %", None)]
NOT_RATIO = [("Corporate Spend", None, "SUM(t.spend)"), ("CORPORATE_SPEND", None, "SUM(t.spend)"),
             ("Shareholders", None, "COUNT(t.id)"), ("Separations", None, "COUNT(t.id)"),
             ("Accelerated Units", None, "SUM(t.u)"), ("Rated Products", None, "COUNT(t.p)"),
             ("Total Units", None, "SUM(t.units)"), ("Revenue", None, "SUM(t.rev)"),
             ("Generated Leads", None, "COUNT(t.l)"), ("Pirate Count", None, "COUNT(t.p)"),
             ("Gross Margin Amount", None, "SUM(t.rev) - SUM(t.cogs)"), ("Order Count", None, "COUNT(t.o)")]


@pytest.mark.parametrize("name,label,formula", RATIO)
def test_ratio_metrics_are_recognised(name, label, formula):
    assert bt._is_ratio_or_pct_metric(name, label, formula, None)


@pytest.mark.parametrize("name,label,formula", NOT_RATIO)
def test_additive_metrics_are_not_called_ratios(name, label, formula):
    assert not bt._is_ratio_or_pct_metric(name, label, formula, "SUM")


WINDOW = [
    ("SUM(C.N) / NULLIF(SUM(C.N) OVER (PARTITION BY EXCLUDING PRODUCTS.PRODUCT), 0)", ["PRODUCTS.PRODUCT"]),
    ("SUM(X) OVER (PARTITION BY EXCLUDING (E.ETHNICITY, E.GENDER))", ["E.ETHNICITY", "E.GENDER"]),
    ('SUM(X) OVER (PARTITION BY EXCLUDING "P"."A")', ["P.A"]),
    ("SUM(X) OVER (PARTITION BY EXCLUDING A.B, C.D)", ["A.B", "C.D"]),
]


@pytest.mark.parametrize("sql,dims", WINDOW)
def test_window_metrics_return_all_required_dimensions(sql, dims):
    is_w, got = bt._is_window_metric(sql, None, "M")
    assert is_w and got == dims


@pytest.mark.parametrize("sql,name", [("SUM(T.X)", "CASES_PCT_BY_PRODUCT"), ("COUNT(*)", "REV_BY_REGION"),
                                      ("SUM(T.X)", "X_PCT_BY_Y")])
def test_window_status_never_guessed_from_the_name(sql, name):
    assert bt._is_window_metric(sql, None, name) == (False, [])


def _m(**kw):
    base = dict(name="M", is_hidden=False, is_private=False, deploy_reason="")
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.mark.parametrize("name", ["ORDER_CNT", "TOTAL_SUM", "CSAT_NUM", "RATIO_DEN", "AVG_OF_DAYS", "COUNT_OF_ITEMS"])
def test_business_metrics_are_not_skipped_because_of_their_name(name):
    assert bt._is_unanswerable_metric(_m(name=name), "SUM(T.X)")[0] is False


@pytest.mark.parametrize("kw,sql", [({"is_private": True}, "SUM(T.X)"), ({"is_hidden": True}, "SUM(T.X)"),
                                    ({"deploy_reason": "PRIVATE helper"}, "SUM(T.X)"), ({}, "NULL"),
                                    ({}, "CAST(NULL AS DOUBLE)"), ({}, ""), ({}, "  ")])
def test_private_and_null_metrics_are_skipped(kw, sql):
    assert bt._is_unanswerable_metric(_m(**kw), sql)[0] is True


@pytest.mark.parametrize("view", ["", "   ", None])
def test_expected_sql_requires_a_real_view(view):
    with pytest.raises(ValueError):
        bt._format_expected_sql(view, "T.M")


@pytest.mark.parametrize("dims", [None, "T.D", ["T.D"], ["A.B", "C.D"]])
def test_expected_sql_is_a_semantic_view_query(dims):
    sql = bt._format_expected_sql("DB.S.V", "T.M", dims)
    assert sql.startswith("SELECT * FROM SEMANTIC_VIEW(") and "METRICS" in sql and "PERIOD" not in sql
    assert ("DIMENSIONS" in sql) == bool(dims)


@pytest.mark.parametrize("case_pid,target,expected", [
    ("proj-ai", "proj-ai", True), ("proj-ai", "ai", True), ("AI", "proj-ai", True), ("proj-ai", "proj-ai-sample", False),
    (None, "proj-ai", False), ("", "proj-ai", False), ("proj-hr", "proj-ai", False), ("proj-ai ", "proj-ai", True),
])
def test_cases_belong_to_exactly_one_project(case_pid, target, expected):
    assert bt._matches_pid(case_pid, target) is expected


@pytest.mark.parametrize("val", [dt.date(2024, 2, 29), dt.datetime(2024, 1, 1, 12, 0), decimal.Decimal("10"),
                                 decimal.Decimal("10.25"), decimal.Decimal("-0.0001"), uuid.uuid4(),
                                 [decimal.Decimal("1.5"), dt.date(2020, 1, 1)], {"a": decimal.Decimal("2")},
                                 (1, decimal.Decimal("3.5")), None, "text", 7, 7.5, True])
def test_snowflake_values_become_json_serialisable(val):
    json.dumps(bt._sanitize_cell_value(val))


@pytest.mark.parametrize("val,expected", [(decimal.Decimal("10"), 10), (decimal.Decimal("10.25"), 10.25),
                                          (decimal.Decimal("123456789012345678.5"), "exact")])
def test_decimals_keep_their_value(val, expected):
    out = bt._sanitize_cell_value(val)
    if expected == "exact":
        assert decimal.Decimal(str(out)) == val, f"precision lost: {out}"
    else:
        assert out == expected


@pytest.mark.parametrize("val", [decimal.Decimal("NaN"), decimal.Decimal("Infinity")])
def test_special_decimals_do_not_crash(val):
    json.dumps(bt._sanitize_cell_value(val), default=str)


@pytest.mark.parametrize("case,ok", [
    ({"power_bi_reference_value": 10, "expected_value": 10}, True),
    ({"power_bi_reference_value": 10, "expected_value": 10.00001}, True),
    ({"power_bi_reference_value": 10, "expected_value": 11}, False),
    ({"expected_value": 10}, False), ({"power_bi_reference_value": None, "expected_value": 10}, False),
    ({"power_bi_reference_value": "abc", "expected_value": "abc"}, True),
    ({"power_bi_reference_value": 1e9, "expected_value": 1e9 + 0.5}, True),
])
def test_certification_requires_a_matching_power_bi_value(case, ok):
    assert bt._is_eligible_for_certification(case) is ok
