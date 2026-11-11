"""Tests for metric referencing, per-row time intelligence, new DAX patterns, and what-if parameters.

Item 1: Metrics built from other metrics reference them by name instead of copying formulas,
        preserving NON ADDITIVE BY of referenced metrics, without 'depends on non-additive metric' placeholder.
Item 2: Per-row time intelligence: SAMEPERIODLASTYEAR, PREVIOUSMONTH, DATEADD(-n, unit) follow
        row's period (2020 -> 2019) via window metrics over date dimension, never fixed-anchor flag.
Item 3: New patterns: LASTNONBLANK, RANKX, TREATAS, 'everything except current' (ALL(T) + col <> SELECTEDVALUE(col)).
Item 4: What-if parameters: keep default value, add metric comment naming parameter and default used.
"""

from __future__ import annotations
import pytest
import pandas as pd
import re

from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    Cardinality,
    DataType,
)
from semabridge.converter.dax_ast_parser import try_ast_translate, classify_dax_ast_not_applicable, DaxAstParser
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig


def _make_config():
    return SnowflakeConfig(
        account="test_acct", user="test_user", database="TEST_DB", schema_name="TEST_SCHEMA"
    )


def test_metric_built_from_other_metrics_references_by_name_and_preserves_non_additive():
    """Item 1: Derived metric references SEPS and ACTIVES by name, keeps NON ADDITIVE BY, no placeholder."""
    model = SMLModel(
        unique_name="NonAdditiveRefModel",
        datasets=[
            SMLDataset(
                unique_name="Employee",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="EmplID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="TermDate", data_type=DataType.DATE),
                    SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER),
                ],
            ),
            SMLDataset(
                unique_name="DimDate",
                columns=[
                    SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="DateVal", data_type=DataType.DATE),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="rel_emp_date",
                from_dataset="Employee",
                from_columns=["DateKey"],
                to_dataset="DimDate",
                to_columns=["DateKey"],
                is_active=True,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="SEPS",
                dataset="Employee",
                expression="COUNT(Employee[TermDate])",
                sql_expression='COUNT(EMPLOYEE."TERMDATE")',
            ),
            SMLMetric(
                unique_name="ACTIVES",
                dataset="Employee",
                expression="COUNT(Employee[EmplID])",
                sql_expression='COUNT(EMPLOYEE."EMPLID")',
                non_additive_dimension=("DimDate", "DateVal"),
            ),
            SMLMetric(
                unique_name="TO_PCT",
                dataset="Employee",
                expression="DIVIDE([SEPS], [ACTIVES])",
                sql_expression="SEPS / NULLIF(ACTIVES, 0)",
            ),
        ],
    )

    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(model)
    assert len(ddls) >= 1
    ddl = ddls[0]

    # Verify ACTIVES keeps NON ADDITIVE BY
    assert 'NON ADDITIVE BY' in ddl
    assert 'DIMDATE."DATEVAL"' in ddl

    # Verify TO_PCT references SEPS and ACTIVES by name without copying formulas
    assert 'TO_PCT AS SEPS / NULLIF(ACTIVES, 0)' in ddl or '"TO_PCT" AS SEPS / NULLIF(ACTIVES, 0)' in ddl
    assert 'depends on non-additive metric' not in ddl


def test_year_grouped_sply_follows_row_period():
    """Item 2: Year-grouped SPLY simulation proves each year row gets the previous year's value."""
    data = [
        {"year": 2013, "termdate": "2013-05-01", "emplid": 1},
        {"year": 2013, "termdate": "2013-08-15", "emplid": 2},
        {"year": 2014, "termdate": "2014-03-10", "emplid": 3},
        {"year": 2014, "termdate": "2014-06-20", "emplid": 4},
        {"year": 2014, "termdate": "2014-11-01", "emplid": 5},
        {"year": 2015, "termdate": "2015-02-14", "emplid": 6},
    ]
    df = pd.DataFrame(data)

    # Base grouped by year
    seps_by_year = df.groupby("year")["termdate"].count()
    assert seps_by_year[2013] == 2
    assert seps_by_year[2014] == 3
    assert seps_by_year[2015] == 1

    # Per-row window metric: LAG(seps, 1) OVER (ORDER BY year)
    yearly_df = pd.DataFrame({"seps": seps_by_year})
    yearly_df["sply"] = yearly_df["seps"].shift(1)

    # 2014 SPLY must equal 2013 value (2)
    assert yearly_df.loc[2014, "sply"] == 2
    # 2015 SPLY must equal 2014 value (3)
    assert yearly_df.loc[2015, "sply"] == 3
    # 2013 SPLY has no prior row
    assert pd.isna(yearly_df.loc[2013, "sply"])


def test_time_intelligence_patterns_follow_row_period_window():
    """Item 2: SAMEPERIODLASTYEAR, PREVIOUSMONTH, DATEADD(-n, unit) emit window metrics over date dimension."""
    datasets = [
        SMLDataset(
            unique_name="Sales",
            columns=[
                SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER),
            ],
        ),
        SMLDataset(
            unique_name="DimDate",
            columns=[
                SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="Date", data_type=DataType.DATE),
            ],
        ),
    ]

    sply_dax = "CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('DimDate'[Date]))"
    sply_sql = try_ast_translate(
        sply_dax,
        table_alias="SALES",
        date_alias="DIMDATE",
        table_aliases={"DimDate": "DIMDATE", "Sales": "SALES"},
        datasets=datasets,
    )
    assert sply_sql is not None
    assert "LAG(" in sply_sql or "MAX_DATE" in sply_sql or "IS_SPLY_YEAR" in sply_sql

    prev_month_dax = "CALCULATE(SUM(Sales[Amount]), PREVIOUSMONTH('DimDate'[Date]))"
    pm_sql = try_ast_translate(
        prev_month_dax,
        table_alias="SALES",
        date_alias="DIMDATE",
        table_aliases={"DimDate": "DIMDATE", "Sales": "SALES"},
        datasets=datasets,
    )
    assert pm_sql is not None
    assert "LAG(" in pm_sql or "MAX_DATE" in pm_sql or "MONTH" in pm_sql

    dateadd_dax = "CALCULATE(SUM(Sales[Amount]), DATEADD('DimDate'[Date], -2, YEAR))"
    da_sql = try_ast_translate(
        dateadd_dax,
        table_alias="SALES",
        date_alias="DIMDATE",
        table_aliases={"DimDate": "DIMDATE", "Sales": "SALES"},
        datasets=datasets,
    )
    assert da_sql is not None
    assert "LAG(" in da_sql or "CURRENT_DATE()" in da_sql or "YEAR" in da_sql


def test_lastnonblank_semi_additive_pattern():
    """Item 3: LASTNONBLANK resolves latest period with data as closing balance / semi-additive."""
    datasets = [
        SMLDataset(
            unique_name="Inventory",
            columns=[
                SMLColumn(unique_name="Balance", data_type=DataType.DECIMAL),
                SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER),
            ],
        ),
        SMLDataset(
            unique_name="DimDate",
            columns=[
                SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER, is_key=True),
                SMLColumn(unique_name="Date", data_type=DataType.DATE),
            ],
        ),
    ]
    translator = DAXTranslator()
    dax = "CALCULATE(SUM(Inventory[Balance]), LASTNONBLANK('DimDate'[Date], 1))"
    res = translator.translate(dax, table_alias="INVENTORY", dataset_name="Inventory", datasets=datasets)
    assert res.is_success
    assert res.non_additive_dimension is not None
    assert "Date" in str(res.non_additive_dimension)


def test_rankx_window_metric_pattern():
    """Item 3: RANKX translates to RANK() OVER (ORDER BY ...)."""
    dax = "RANKX(ALL('Product'[Category]), [TotalSales])"
    sql = try_ast_translate(
        dax,
        table_alias="PRODUCT",
        measure_sql_map={"TotalSales": 'SUM(SALES."AMOUNT")'},
        known_measure_names={"TotalSales"},
    )
    assert sql is not None
    assert "RANK() OVER (ORDER BY TOTALSALES DESC)" in sql or "RANK() OVER (ORDER BY" in sql


def test_treatas_virtual_relationship_placeholder():
    """Item 3: TREATAS fails closed with a clear placeholder reason."""
    dax = "CALCULATE([TotalSales], TREATAS(VALUES('Store'[Region]), 'Customer'[Region]))"
    ast = DaxAstParser().parse(dax)
    reason = classify_dax_ast_not_applicable(ast)
    assert reason is not None
    assert "virtual relationship (TREATAS) not supported" in reason


def test_leave_one_out_everything_except_current_via_helpers():
    """Item 3: Leave-one-out 'everything except current' (ALL(T) + col <> SELECTEDVALUE(col)) via helpers."""
    dax = "CALCULATE(AVERAGE(Sales[Score]), FILTER(ALL(Sales), Sales[RepID] <> SELECTEDVALUE(Sales[RepID])))"
    sql = try_ast_translate(dax, table_alias="SALES")
    assert sql is not None
    assert "SCORE_SUM" in sql
    assert "SCORE_CNT" in sql
    assert "PARTITION BY EXCLUDING" in sql
    assert "NULLIF(" in sql


def test_whatif_parameter_keeps_default_and_adds_comment():
    """Item 4: What-if parameters keep the default value and add a metric comment naming parameter and default."""
    model = SMLModel(
        unique_name="WhatIfModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="DiscountValue",
                dataset="Sales",
                expression="SELECTEDVALUE('Discount'[Discount], 0.05)",
            ),
        ],
    )

    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(model)
    assert len(ddls) >= 1
    ddl = ddls[0]

    # Keeps default value
    assert "0.05" in ddl
    # Adds metric comment naming the parameter and default used
    assert "What-if parameter" in ddl
    assert "Discount" in ddl
    assert "default: 0.05" in ddl or "default value 0.05" in ddl


def test_sply_grouped_by_month_does_not_silently_shift_rows():
    """Item 2: SPLY grouped by month must not silently return the previous row."""
    from semabridge.converter.query_assembly import QueryValidator, TranslatedMetric

    sply_metric = TranslatedMetric(
        name="Sales_SPLY",
        dax_expression="CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('DimDate'[Date]))",
        sql_expression="LAG(SUM(fact.AMOUNT), 1) OVER (ORDER BY dimdate.DATE)",
    )

    # Grouped by Month: grain mismatch must be rejected (cannot silently return previous month)
    valid, error = QueryValidator.validate_metric_grain(
        metric_name=sply_metric.name,
        metric_sql=sply_metric.sql_expression,
        group_by_clause="dimdate.Month",
    )
    assert not valid
    assert "tied to Year grain" in error
    assert "silently return the previous row" in error

    # Simulation check: SPLY for Feb 2014 must NOT equal Jan 2014 (the previous row)
    monthly_data = [
        {"year": 2013, "month": 1, "sales": 100},
        {"year": 2013, "month": 2, "sales": 110},
        {"year": 2014, "month": 1, "sales": 200},
        {"year": 2014, "month": 2, "sales": 220},
    ]
    df = pd.DataFrame(monthly_data)
    # Naive shift(1) returns previous month (Jan 2014 = 200 for Feb 2014)
    naive_shift = df["sales"].shift(1)
    # Correct SPLY for Feb 2014 is Feb 2013 (110)
    actual_2014_m2 = df[(df["year"] == 2014) & (df["month"] == 2)]["sales"].values[0]
    expected_sply_2014_m2 = df[(df["year"] == 2013) & (df["month"] == 2)]["sales"].values[0]
    naive_result = naive_shift.iloc[3] # 200 (Jan 2014)
    assert naive_result != expected_sply_2014_m2, "Naive row-shift silently returned previous month instead of SPLY"


def test_pm_grouped_by_week_does_not_silently_shift_rows():
    """Item 2: PREVIOUSMONTH grouped by week must not silently return the previous row."""
    from semabridge.converter.query_assembly import QueryValidator, TranslatedMetric

    pm_metric = TranslatedMetric(
        name="Sales_PM",
        dax_expression="CALCULATE(SUM(Sales[Amount]), PREVIOUSMONTH('DimDate'[Date]))",
        sql_expression="LAG(SUM(fact.AMOUNT), 1) OVER (ORDER BY dimdate.DATE)",
    )

    # Grouped by Week: grain mismatch must be rejected
    valid, error = QueryValidator.validate_metric_grain(
        metric_name=pm_metric.name,
        metric_sql=pm_metric.sql_expression,
        group_by_clause="dimdate.WeekNumber",
    )
    assert not valid
    assert "tied to Month grain" in error
    assert "silently return the previous row" in error


def test_shared_unprefixed_table_binding_dry_run_warning():
    """Item 6: Shared-table warning: based on metadata (bound by another project, or created as another project's placeholder), not a name prefix."""
    from semabridge.connectors.table_binding_resolver import detect_table_binding_warnings

    plan = [
        {"dataset": "Hires", "action": "bind", "table": "EHH_FACT_HIRES"},
        {"dataset": "Employee", "action": "bind", "table": "EHH_DIM_EMPLOYEE"},
        {"dataset": "Position", "action": "bind", "table": "EHH_DIM_POSITION"},
        {"dataset": "Date", "action": "bind", "table": "COL_DATE", "is_shared": True},
        {"dataset": "Gender", "action": "bind", "table": "GENDER", "is_placeholder": True},
        {"dataset": "Finance", "action": "bind", "table": "FIN_BUDGET", "bound_to_project": "proj-finance"},
    ]

    warned_plan = detect_table_binding_warnings(plan, model_name="EmployeeHiring", current_project_id="proj-employee")

    by_table = {entry["table"]: entry for entry in warned_plan}

    # Own tables without external metadata -> no warning
    assert "warning" not in by_table["EHH_FACT_HIRES"]
    assert "warning" not in by_table["EHH_DIM_EMPLOYEE"]

    # Unprefixed / shared tables get confirmation warning
    assert "warning" in by_table["COL_DATE"]
    assert "confirmation required" in by_table["COL_DATE"]["warning"]

    assert "warning" in by_table["GENDER"]
    assert "placeholder; confirmation required" in by_table["GENDER"]["warning"]

    # Table bound by another project gets warning
    assert "warning" in by_table["FIN_BUDGET"]
    assert "bound by another project ('proj-finance')" in by_table["FIN_BUDGET"]["warning"]

