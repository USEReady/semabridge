"""Print today's translation of every DAX test case as JSON (renderer snapshot).

Run by test_renderer_snapshot.py in a separate process, because the feature
cases use generic module names (fixture, cases, adapter) that must not leak
into the main pytest process.

Sources (all already in the repo):
  * Tests/phase_a/feature_tests_for_measures/cases.py (+ cross-table cases),
    translated through that folder's adapter.py -- DAXTranslator with the
    deterministic tiers only, the referenced measures passed as context;
  * complex_measure_tests/ai_complex_measures.dax, every MEASURE in the
    DEFINE block, with the measures it references (directly or indirectly)
    as context.

Usage: python renderer_corpus.py  -> JSON on stdout
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FEATURES = HERE.parent / "phase_a" / "feature_tests_for_measures"
COMPLEX_DAX = FEATURES / "complex_measure_tests" / "ai_complex_measures.dax"

# Deterministic tiers only: never the AI tier.
os.environ.pop("SEMABRIDGE_TIER5", None)

sys.path.insert(0, str(FEATURES))
import fixture  # noqa: E402  (feature-test synthetic model)

# adapter.py reads the table context from __main__.
TABLES = fixture.TABLES
RELATIONSHIPS = fixture.RELATIONSHIPS
INACTIVE = getattr(fixture, "INACTIVE", [])

import adapter  # noqa: E402
from cases import CASES  # noqa: E402

_MEASURE_RE = re.compile(
    r"^\s*MEASURE\s+'?(?P<table>[^'\[]+?)'?\[(?P<name>[^\]]+)\]\s*=\s*(?P<dax>.*?)(?=^\s*MEASURE\s|^\s*EVALUATE\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _complex_measures() -> dict:
    text = COMPLEX_DAX.read_text(encoding="utf-8")
    return {
        m.group("name").strip(): (m.group("table").strip(), m.group("dax").strip())
        for m in _MEASURE_RE.finditer(text)
    }


def _referenced_closure(name: str, measures: dict) -> dict:
    """Measures `name` references, directly or through other measures.

    adapter.translate re-translates every context measure recursively, so
    passing the whole file as context grows factorially; only the measures
    actually referenced matter for the result.
    """
    names_cf = {n.casefold(): n for n in measures}
    seen: dict = {}
    stack = [name]
    while stack:
        dax = measures[stack.pop()][1]
        for ref in re.findall(r"(?<!['\w\]])\[([^\]]+)\]", dax):
            target = names_cf.get(ref.strip().casefold())
            if target and target != name and target not in seen:
                seen[target] = measures[target]
                stack.append(target)
    return seen


def _memoize_adapter() -> None:
    """Cache adapter.translate on its inputs; its recursion calls the module
    global, so nested context translations hit the cache too."""
    original = adapter.translate
    cache: dict = {}

    def cached(dax, home_table, name, context=None):
        key = (dax, home_table, name, tuple(sorted((context or {}).items())))
        if key not in cache:
            cache[key] = original(dax, home_table, name, context)
        return cache[key]

    adapter.translate = cached


# Direct renderer cases: each aims at a place where the renderer spells SQL
# (quoting, ::FLOAT, IFF, TRY_TO_NUMBER/TO_VARCHAR, date functions) or reads
# its own SQL back, so moving that spelling into the dialect is checked line
# by line. Inputs are synthetic; outputs are whatever today's code returns.
_YTD_SQL = (
    "SUM(CASE WHEN DATE.\"DATE\" >= DATE_TRUNC('YEAR', CURRENT_DATE()) "
    "AND DATE.\"DATE\" <= CURRENT_DATE() THEN SALES.\"AMOUNT\"::FLOAT END)"
)
_MEASURES = {
    "Total Sales": 'SUM(SALES."AMOUNT"::FLOAT)',
    "Total Cost": 'SUM(SALES."COST"::FLOAT)',
    "Order Count": "COUNT(*)",
    "Unit Price": 'MAX(SALES."PRICE"::FLOAT)',
    "YTD Sales": _YTD_SQL,
    "Legacy YTD": 'SUM(CASE WHEN DATE."DATE" <= SALES."MAX_DATE" THEN SALES."AMOUNT"::FLOAT END)',
    "Flag YTD Sales": 'SUM(CASE WHEN SALES."FLAG_YTD" THEN SALES."AMOUNT"::FLOAT END)',
    "Row Price": 'SALES."PRICE" * 2',
    "Constant": "100",
}
_FLAGS = {
    ("YTD",): "FLAG_YTD",
    ("MTD",): "FLAG_MTD",
    ("SPLY_YEAR",): "FLAG_SPLY",
    ("PREV_MONTH",): "FLAG_PREV_MONTH",
    ("R3M",): "FLAG_R3M",
    ("CURRENT_YEAR",): "FLAG_CY",
    ("YTD", "SPLY_YEAR"): "FLAG_YTD_SPLY",
}
_DATA = {"Sales": {"Amount", "Cost", "Price", "Qty", "Code", "Status", "Order Date", "Ship Date", "OrderID"}}

DIRECT_CASES = [
    # column references and the dates column of time intelligence
    ("col_sum", "SUM(Sales[Amount])", {}),
    ("col_special_chars", "SUM('Sales'[Margin %]) + SUM(Sales[Unit $])", {}),
    ("col_unqualified_bracket", "SUM([Amount])", {"dataset_col_lookup": _DATA, "primary_table_name": "Sales"}),
    ("ti_date_identifier", "TOTALYTD(SUM(Sales[Amount]), Date[Date])", {}),
    # iterators and filtered aggregates (::FLOAT placement)
    ("sumx_filter", 'SUMX(FILTER(Sales, Sales[Status] = "Won"), Sales[Amount])', {}),
    ("averagex_row", "AVERAGEX(Sales, Sales[Qty] * Sales[Price])", {}),
    ("averagex_measure", "AVERAGEX(Sales, [Total Sales])", {"measure_sql_map": _MEASURES}),
    ("sumx_row_measure", "SUMX(Sales, [Unit Price])", {"measure_sql_map": _MEASURES}),
    ("sumx_row_expr_measure", "SUMX(Sales, [Row Price])", {"measure_sql_map": _MEASURES}),
    ("sumx_constant_measure", "SUMX(Sales, [Constant])", {"measure_sql_map": _MEASURES}),
    ("averagex_inner_sum", "AVERAGEX(Sales, SUM(Sales[Amount]))", {}),
    ("ti_date_fallback_column", "TOTALYTD(Sales[Amount], 1)", {}),
    ("ti_date_fallback_identifier", "TOTALYTD(Amount, 1)", {}),
    ("totalytd_count_measure_key", "TOTALYTD([Order Count], 'Date'[Date])",
     {"measure_sql_map": _MEASURES, "table_key_map": {"sales": "OrderID"}, "primary_table_name": "Sales"}),
    ("totalytd_count_measure_odd_key", "TOTALYTD([Order Count], 'Date'[Date])",
     {"measure_sql_map": _MEASURES, "table_key_map": {"sales": "Key$1"}, "primary_table_name": "Sales"}),
    ("calc_filter_sum", 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Won")', {}),
    ("calc_filter_avg", 'CALCULATE(AVERAGE(Sales[Amount]), Sales[Status] = "Won")', {}),
    ("calc_filter_countrows", 'CALCULATE(COUNTROWS(Sales), Sales[Status] = "Won")', {}),
    ("calc_filter_measure", 'CALCULATE([Total Sales], Sales[Status] = "Won")', {"measure_sql_map": _MEASURES}),
    ("calc_filter_distinct", 'CALCULATE(DISTINCTCOUNT(Sales[OrderID]), Sales[Status] = "Won")', {}),
    # conditionals and conversions
    ("divide_alt", "DIVIDE(SUM(Sales[Amount]), SUM(Sales[Cost]), 0)", {}),
    ("divide_blank", "DIVIDE(SUM(Sales[Amount]), SUM(Sales[Cost]))", {}),
    ("coalesce_iff_operand", "SUM(Sales[Qty]) + DIVIDE(SUM(Sales[Amount]), SUM(Sales[Cost]), 0)", {}),
    ("coalesce_plain_operand", "SUM(Sales[Qty]) - SUM(Sales[Cost])", {}),
    ("selectedvalue", 'SELECTEDVALUE(Sales[Status], "none")', {}),
    ("iferror", "IFERROR(SUM(Sales[Amount]) / SUM(Sales[Cost]), 0)", {}),
    ("value", "SUMX(Sales, VALUE(Sales[Code]))", {}),
    # current date / timestamp
    ("today", "DATEDIFF(MAX(Sales[Order Date]), TODAY(), DAY)", {}),
    ("now", "DATEDIFF(MAX(Sales[Order Date]), NOW(), HOUR)", {}),
    ("datediff", "DATEDIFF(MIN(Sales[Order Date]), MAX(Sales[Ship Date]), DAY)", {}),
    ("datesbetween_today", "CALCULATE(SUM(Sales[Amount]), DATESBETWEEN('Date'[Date], DATE(2024,1,1), TODAY()))", {}),
    ("datesbetween_literal", "CALCULATE(SUM(Sales[Amount]), DATESBETWEEN('Date'[Date], DATE(2024,1,1), DATE(2024,12,31)))", {}),
    # DATESINPERIOD
    ("datesinperiod_back", "CALCULATE(SUM(Sales[Amount]), DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))", {}),
    ("datesinperiod_flag", "CALCULATE(SUM(Sales[Amount]), DATESINPERIOD('Date'[Date], MAX('Date'[Date]), -3, MONTH))", {"anchor_flag_map": _FLAGS}),
    ("datesinperiod_forward", "CALCULATE(SUM(Sales[Amount]), DATESINPERIOD('Date'[Date], DATE(2024,1,1), 2, MONTH))", {}),
    # period-to-date
    ("totalytd", "TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])", {}),
    ("totalmtd", "TOTALMTD(SUM(Sales[Amount]), 'Date'[Date])", {}),
    ("totalqtd_count", "TOTALQTD(COUNTROWS(Sales), 'Date'[Date])", {}),
    ("totalytd_fiscal", 'TOTALYTD(SUM(Sales[Amount]), \'Date\'[Date], "6/30")', {}),
    ("totalytd_flag", "TOTALYTD(SUM(Sales[Amount]), 'Date'[Date])", {"anchor_flag_map": _FLAGS}),
    ("totalytd_measure", "TOTALYTD([Total Sales], 'Date'[Date])", {"measure_sql_map": _MEASURES}),
    ("totalytd_filter", 'TOTALYTD(SUM(Sales[Amount]), \'Date\'[Date], Sales[Status] = "Won")', {}),
    # period shifts
    ("sply", "CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))", {}),
    ("prev_year_full", "CALCULATE(SUM(Sales[Amount]), PREVIOUSYEAR('Date'[Date]))", {}),
    ("prev_month", "CALCULATE(SUM(Sales[Amount]), PREVIOUSMONTH('Date'[Date]))", {}),
    ("dateadd_quarter", "CALCULATE(SUM(Sales[Amount]), DATEADD('Date'[Date], -1, QUARTER))", {}),
    ("dateadd_day", "CALCULATE(SUM(Sales[Amount]), DATEADD('Date'[Date], -1, DAY))", {}),
    ("dateadd_year", "CALCULATE(SUM(Sales[Amount]), DATEADD('Date'[Date], -1, YEAR))", {}),
    ("dateadd_raw", "MAX(DATEADD('Date'[Date], -1, MONTH))", {}),
    ("sply_measure", "CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date]))", {"measure_sql_map": _MEASURES}),
    ("sply_ytd_measure", "CALCULATE([YTD Sales], SAMEPERIODLASTYEAR('Date'[Date]))", {"measure_sql_map": _MEASURES}),
    ("prev_year_ytd_measure", "CALCULATE([YTD Sales], PREVIOUSYEAR('Date'[Date]))", {"measure_sql_map": _MEASURES}),
    ("prev_year_legacy", "CALCULATE([Legacy YTD], PREVIOUSYEAR('Date'[Date]))", {"measure_sql_map": _MEASURES}),
    ("sply_legacy", "CALCULATE([Legacy YTD], SAMEPERIODLASTYEAR('Date'[Date]))", {"measure_sql_map": _MEASURES}),
    ("sply_flag", "CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('Date'[Date]))", {"anchor_flag_map": _FLAGS}),
    ("prev_month_flag", "CALCULATE(SUM(Sales[Amount]), PREVIOUSMONTH('Date'[Date]))", {"anchor_flag_map": _FLAGS}),
    ("sply_flag_measure", "CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date]))",
     {"measure_sql_map": _MEASURES, "anchor_flag_map": _FLAGS}),
    ("sply_nested_flag", "CALCULATE([Flag YTD Sales], SAMEPERIODLASTYEAR('Date'[Date]))",
     {"measure_sql_map": _MEASURES, "anchor_flag_map": _FLAGS}),
    ("yoy_comparison_flags", "[Total Sales] - CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date]))",
     {"measure_sql_map": _MEASURES, "anchor_flag_map": _FLAGS}),
    ("yoy_growth_flags", "DIVIDE([Total Sales] - CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date])), CALCULATE([Total Sales], SAMEPERIODLASTYEAR('Date'[Date])))",
     {"measure_sql_map": _MEASURES, "anchor_flag_map": _FLAGS}),
]


def _direct_cases() -> dict:
    from semabridge.converter.dax_ast_parser import try_ast_translate

    out = {}
    for case_id, dax, extra in DIRECT_CASES:
        kwargs = {"table_alias": "SALES", "date_alias": "DATE"}
        kwargs.update(extra)
        errors: list = []
        advisories: list = []
        try:
            sql = try_ast_translate(dax, render_errors=errors, advisory_categories=advisories, **kwargs)
            out[case_id] = {"sql": sql, "errors": errors, "advisories": advisories}
        except Exception as exc:  # a crash is part of today's behaviour too
            out[case_id] = {"sql": None, "errors": [f"{type(exc).__name__}: {exc}"], "advisories": advisories}
    return out


def _record(res: dict) -> dict:
    return {"status": res.get("status"), "sql": res.get("sql"), "reason": res.get("reason") or ""}


def main() -> None:
    _memoize_adapter()
    out = {"feature_cases": {}, "complex_measures": {}}
    for case in CASES:
        res = adapter.translate(case["dax"], case["home"], case["id"], case.get("context"))
        out["feature_cases"][case["id"]] = _record(res)

    measures = _complex_measures()
    for name, (home, dax) in measures.items():
        context = _referenced_closure(name, measures)
        res = adapter.translate(dax, home, name, context)
        out["complex_measures"][name] = _record(res)

    out["renderer_direct"] = _direct_cases()

    sys.stdout.write(json.dumps(out, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
