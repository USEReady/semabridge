"""
Tests for SPLY (Same Period Last Year) and Prior-Period Time Intelligence Grouping Semantics.

Background:
In Power BI / DAX:
  SAMEPERIODLASTYEAR('Calendar'[Date]) shifts the date filter context back by 1 year.
  When grouped by Year:
    - On Year 2013 row: Seps = 100, Seps SPLY = [2012 Seps]
    - On Year 2014 row: Seps = 150, Seps SPLY = 100 (MUST equal 2013's Seps!)
    - YoY Var = 150 - 100 = +50
    - YoY % = 50 / 100 = +50%

The Single-Table Row Flag Flaw:
  A single-table boolean column (e.g. `IS_SPLY_YEAR`) or in-row condition cannot work:
  When a query groups by Year (or filters to Year = 2014), the query's filter context
  only includes rows from 2014. Rows from 2013 are excluded by the query's WHERE/GROUP BY.
  Thus, checking `IS_SPLY_YEAR` evaluates to 0 for almost every year, producing:
    Seps SPLY = 0
    YoY Var = Seps - 0 = Seps (WRONG)
    YoY % = division by zero -> NULL (WRONG)

Generic Solution (Prior-Year View):
  The fact table is joined with a date-shifted view (DATEADD(YEAR, 1, date)) as e.g. EMPLOYEE_PY.
  Filtering on Year 2014 joins against 2013 rows (which have their date shifted forward to 2014).

Until the prior-year view is implemented, SemaBridge MUST fail closed (return not_applicable
with 'time intelligence not supported yet') rather than emitting wrong single-table SQL.
"""

import pandas as pd
import pytest

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.converter.dax_ast_parser import dax_time_intelligence_failure_reason


def test_sply_grouping_semantics_simulation():
    """
    Simulate why single-table row flags fail when grouped by year,
    and why grouped SPLY must equal previous year's value.
    """
    # Sample multi-year fact data
    data = [
        # 2013 facts
        {"emplid": 1, "year": 2013, "termdate": "2013-05-01", "is_new_hire": 0},
        {"emplid": 2, "year": 2013, "termdate": "2013-08-15", "is_new_hire": 1},
        # 2014 facts
        {"emplid": 3, "year": 2014, "termdate": "2014-03-10", "is_new_hire": 1},
        {"emplid": 4, "year": 2014, "termdate": "2014-06-20", "is_new_hire": 1},
        {"emplid": 5, "year": 2014, "termdate": "2014-11-01", "is_new_hire": 0},
    ]
    df = pd.DataFrame(data)

    # 1. Expected Power BI / Ground Truth grouped by year:
    # 2013 Seps: 2 (emplid 1, 2)
    # 2014 Seps: 3 (emplid 3, 4, 5)
    seps_by_year = df.groupby("year")["termdate"].apply(lambda s: s.notna().sum()).to_dict()
    assert seps_by_year[2013] == 2
    assert seps_by_year[2014] == 3

    # On Year 2014: SPLY MUST equal 2013's seps (2)
    expected_2014_sply = seps_by_year[2013]
    assert expected_2014_sply == 2

    # Expected YoY Var for 2014 = 3 - 2 = 1
    expected_yoy_var = seps_by_year[2014] - expected_2014_sply
    assert expected_yoy_var == 1

    # 2. What a single-table row flag (IS_SPLY_YEAR) produces:
    # IS_SPLY_YEAR is fixed to TRUE for one year (e.g. 2013)
    df["is_sply_year"] = df["year"] == 2013

    # In a query grouped by Year (e.g. SELECT year, ... FROM ... GROUP BY year):
    broken_sply_by_year = {}
    for yr, group in df.groupby("year"):
        # Single table expression: COUNT(CASE WHEN is_sply_year AND termdate IS NOT NULL THEN emplid END)
        broken_sply_by_year[yr] = (group["is_sply_year"] & group["termdate"].notna()).sum()

    # On 2014 row, 2013 rows are not in the group, so is_sply_year is FALSE for all 2014 rows!
    assert broken_sply_by_year[2014] == 0  # WRONG: gives 0 instead of 2!

    # This causes Seps YoY Var = Seps - 0 = Seps on almost every year!
    broken_yoy_var = seps_by_year[2014] - broken_sply_by_year[2014]
    assert broken_yoy_var == seps_by_year[2014]  # Seps YoY Var equals Seps!


def test_semabridge_declines_wrong_sply_sql():
    """
    Ensure SemaBridge declines to emit single-table SQL for SPLY/lag functions,
    failing closed to 'time intelligence not supported yet' so wrong numbers are not emitted.
    """
    dax_cases = [
        "CALCULATE(SUM(Employee[isNewHire]), SAMEPERIODLASTYEAR('Date'[Date]))",
        "CALCULATE(COUNT(Employee[EmplID]), PREVIOUSYEAR('Date'[Date]))",
        "CALCULATE(SUM(Employee[BadHires]), PARALLELPERIOD('Date'[Date], -1, YEAR))",
        "CALCULATE(COUNT(Employee[EmplID]), DATEADD('Date'[Date], -1, YEAR))",
    ]

    for dax in dax_cases:
        reason = dax_time_intelligence_failure_reason(dax)
        assert reason == "time intelligence not supported yet", f"Expected failure reason for: {dax}"

    translator = DAXTranslator()
    for dax in dax_cases:
        res = translator.translate(dax, "employee", "Employee", metric_name="TestMetric")
        assert res.sql is None, f"Expected no SQL for: {dax}, but got: {res.sql}"
        assert res.sync_enabled is False, f"Expected sync_enabled=False for: {dax}"
        assert res.failure_reason == "time intelligence not supported yet"
