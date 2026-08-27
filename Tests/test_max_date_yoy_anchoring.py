import pytest
import datetime
from semabridge.converter.time_intelligence_shapes import (
    discover_time_intelligence_shapes,
    metrics_with_time_intelligence_shapes,
    build_shape_boolean_sql,
    flag_column_name,
)
from semabridge.connectors.anchor_flag_rerender import rerender_anchor_dependent_metrics
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.sml.models import SMLMetric, SMLDataset, SMLColumn, SMLModel


def test_yoy_variance_and_r12m_shape_discovery():
    """Verify that composite metrics (YoY variance, branch metrics, R12M) are discovered as shaped metrics."""
    m_ytd = SMLMetric(unique_name="Total Units YTD", dataset="SalesFact", expression="TOTALYTD(SUM(SalesFact[Units]), Date[Date])")
    m_sply = SMLMetric(unique_name="TOTAL UNITS SPLY", dataset="SalesFact", expression="CALCULATE(SUM(SalesFact[Units]), SAMEPERIODLASTYEAR(Date[Date]))")
    m_ytd_sply = SMLMetric(unique_name="Total Units YTD SPLY", dataset="SalesFact", expression="CALCULATE([Total Units YTD], SAMEPERIODLASTYEAR(Date[Date]))")
    m_var = SMLMetric(unique_name="Total Units YTD Var", dataset="SalesFact", expression="[Total Units YTD] - [Total Units YTD SPLY]")
    m_branch1 = SMLMetric(unique_name="KPI01_BRANCH_1", dataset="SalesFact", expression="CONCATENATE(LEFT([Total Units YTD Var], 3), '% ROI')")
    m_r12m = SMLMetric(unique_name="Total Units R12Ms", dataset="SalesFact", expression="CALCULATE(SUM(SalesFact[Units]), FILTER(ALL('Date'), 'Date'[MonthIndex] <= MAX('Date'[MonthIndex]) && 'Date'[MonthIndex] > MAX('Date'[MonthIndex]) - 12))")

    metrics = [m_ytd, m_sply, m_ytd_sply, m_var, m_branch1, m_r12m]
    shaped = metrics_with_time_intelligence_shapes(metrics)

    assert "Total Units YTD" in shaped
    assert "TOTAL UNITS SPLY" in shaped
    assert "Total Units YTD SPLY" in shaped
    assert "Total Units YTD Var" in shaped
    assert "KPI01_BRANCH_1" in shaped
    assert "Total Units R12Ms" in shaped

    shapes = discover_time_intelligence_shapes(metrics)
    assert ("YTD",) in shapes
    assert ("SPLY_YEAR",) in shapes
    assert ("YTD", "SPLY_YEAR") in shapes
    assert ("R12M",) in shapes


def test_lagging_historical_dataset_anchoring():
    """Verify that precomputed flag columns for a historical dataset use MAX(date) literal (e.g. 2014-12-30) instead of CURRENT_DATE()."""
    date_col = 'f."COL_DATE"'
    historical_max_date = "'2014-12-30'"

    ytd_sql = build_shape_boolean_sql(("YTD",), date_col, historical_max_date)
    sply_sql = build_shape_boolean_sql(("SPLY_YEAR",), date_col, historical_max_date)
    r12m_sql = build_shape_boolean_sql(("R12M",), date_col, historical_max_date)

    assert "'2014-12-30'" in ytd_sql
    assert "CURRENT_DATE" not in ytd_sql
    assert "DATE_TRUNC('YEAR', '2014-12-30')" in ytd_sql

    assert "'2014-12-30'" in sply_sql
    assert "DATEADD(YEAR, -1, '2014-12-30')" in sply_sql

    assert "DATEADD(MONTH, -12, '2014-12-30')" in r12m_sql


def test_live_current_dataset_anchoring():
    """Verify that for a live current dataset, MAX(date) anchor generates identical date ranges as CURRENT_DATE()."""
    date_col = 'f."COL_DATE"'
    today_literal = "CURRENT_DATE()"

    ytd_sql = build_shape_boolean_sql(("YTD",), date_col, today_literal)
    sply_sql = build_shape_boolean_sql(("SPLY_YEAR",), date_col, today_literal)

    assert "CURRENT_DATE()" in ytd_sql
    assert "DATE_TRUNC('YEAR', CURRENT_DATE())" in ytd_sql
    assert "DATEADD(YEAR, -1, CURRENT_DATE())" in sply_sql


def test_rerender_anchors_composite_metrics_to_flag_columns():
    """Verify that re-rendering composite and branch metrics replaces CURRENT_DATE() with flag column references."""
    m_ytd = SMLMetric(unique_name="Total Units YTD", dataset="SalesFact", expression="TOTALYTD(SUM(SalesFact[Units]), Date[Date])", sql_expression="SUM(CASE WHEN COL_DATE >= DATE_TRUNC('YEAR', CURRENT_DATE()) AND COL_DATE <= CURRENT_DATE() THEN Units ELSE NULL END)")
    m_sply = SMLMetric(unique_name="Total Units YTD SPLY", dataset="SalesFact", expression="CALCULATE([Total Units YTD], SAMEPERIODLASTYEAR(Date[Date]))", sql_expression="SUM(CASE WHEN COL_DATE >= DATE_TRUNC('YEAR', DATEADD(YEAR, -1, CURRENT_DATE())) AND COL_DATE <= DATEADD(YEAR, -1, CURRENT_DATE()) THEN Units ELSE NULL END)")
    m_var = SMLMetric(unique_name="Total Units YTD Var", dataset="SalesFact", expression="[Total Units YTD] - [Total Units YTD SPLY]", sql_expression="(SUM(CASE WHEN COL_DATE >= DATE_TRUNC('YEAR', CURRENT_DATE()) THEN Units ELSE NULL END)) - (SUM(CASE WHEN COL_DATE >= DATE_TRUNC('YEAR', DATEADD(YEAR, -1, CURRENT_DATE())) THEN Units ELSE NULL END))")
    m_branch1 = SMLMetric(unique_name="KPI01_BRANCH_1", dataset="SalesFact", expression="[Total Units YTD Var]", sql_expression="[Total Units YTD Var]")

    ds = SMLDataset(unique_name="SalesFact", name="SalesFact", columns=[SMLColumn(unique_name="Units", label="Units"), SMLColumn(unique_name="COL_DATE", label="COL_DATE")])
    model = SMLModel(unique_name="TestModel", metrics=[m_ytd, m_sply, m_var, m_branch1], datasets=[ds])

    anchor_flag_map = {
        "salesfact": {
            ("YTD",): "IS_YTD",
            ("YTD", "SPLY_YEAR"): "IS_YTD_SPLY_YEAR",
            ("SPLY_YEAR",): "IS_SPLY_YEAR",
            ("R12M",): "IS_R12M",
        }
    }

    lookup, aliases = DAXTranslator.build_schema_lookup(model.datasets)
    n = rerender_anchor_dependent_metrics(model, anchor_flag_map, lookup, aliases, label="test")

    assert n >= 3
    assert "IS_YTD" in m_ytd.sql_expression
    assert "IS_YTD_SPLY_YEAR" in m_sply.sql_expression
    assert "IS_YTD" in m_var.sql_expression
    assert "IS_YTD_SPLY_YEAR" in m_var.sql_expression
    assert "CURRENT_DATE" not in m_var.sql_expression
    assert "CURRENT_DATE" not in m_branch1.sql_expression


def test_relationship_based_date_column_resolution_for_enrichment():
    """Verify that a fact table with NO raw physical date column, but connected to Date dimension via relationship, is correctly identified as date-eligible for enrichment."""
    from semabridge.sml.models import SMLRelationship

    m_ytd = SMLMetric(unique_name="Total Sales YTD", dataset="SalesFact", expression="TOTALYTD(SUM(SalesFact[Amount]), Date[Date])")

    ds_sales = SMLDataset(unique_name="SalesFact", name="SalesFact", columns=[SMLColumn(unique_name="Amount", label="Amount"), SMLColumn(unique_name="DateID", label="DateID")])
    ds_date = SMLDataset(unique_name="Date", name="Date", columns=[SMLColumn(unique_name="COL_DATE", label="COL_DATE"), SMLColumn(unique_name="DateID", label="DateID")])

    rel = SMLRelationship(unique_name="SalesFact_Date", from_dataset="SalesFact", from_columns=["DateID"], to_dataset="Date", to_columns=["DateID"])

    model = SMLModel(unique_name="TestModel", metrics=[m_ytd], datasets=[ds_sales, ds_date], relationships=[rel])

    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import get_settings
    emitter = SnowflakeEmitter(get_settings().snowflake)

    fact_cols = {"AMOUNT", "DATEID"}

    # Fact table connected via relationship is eligible
    eligible = emitter._is_fact_table_date_eligible(model, "SalesFact", "Date", "COL_DATE", fact_cols)
    assert eligible is True

    # Unrelated selector table is NOT eligible
    kpi_eligible = emitter._is_fact_table_date_eligible(model, "KPI", "Date", "COL_DATE", {"KEY", "VALUE"})
    assert kpi_eligible is False
