"""Unit tests for platform-specific BI Truth Window Builders."""
from semabridge.ai.bi_truth_windows import build_windowed_sql
from semabridge.ai.window_builders import (
    DatabricksWindowBuilder,
    SnowflakeWindowBuilder,
    get_window_builder,
)


def test_get_window_builder():
    assert isinstance(get_window_builder("DATABRICKS"), DatabricksWindowBuilder)
    assert isinstance(get_window_builder("SNOWFLAKE"), SnowflakeWindowBuilder)
    assert get_window_builder(None) is None
    assert get_window_builder("UNKNOWN") is None


def test_snowflake_window_injection():
    sql = "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.HR.VIEW METRICS ACTIVES);"
    res = build_windowed_sql(sql, "COL_DATE.YEAR = 2014", platform="SNOWFLAKE")
    assert res == "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.HR.VIEW METRICS ACTIVES WHERE COL_DATE.YEAR = 2014)"


def test_databricks_window_injection_total_and_sliced():
    total_sql = "SELECT MEASURE(`AVG_OF_REV`) FROM `cat`.`sch`.`model_metric_view`;"
    res_total = build_windowed_sql(total_sql, "ACCOUNTS.COUNTRY = 'USA'", platform="DATABRICKS")
    assert res_total == (
        "SELECT MEASURE(`AVG_OF_REV`) FROM `cat`.`sch`.`model_metric_view` WHERE `acco_country` = 'USA'"
    )

    sliced_sql = "SELECT `acco_account_name`, MEASURE(`AVG_OF_REV`) FROM `cat`.`sch`.`model_metric_view` GROUP BY `acco_account_name`;"
    res_sliced = build_windowed_sql(sliced_sql, "acco_country = 'USA'", platform="DATABRICKS")
    assert res_sliced == (
        "SELECT `acco_account_name`, MEASURE(`AVG_OF_REV`) FROM `cat`.`sch`.`model_metric_view` "
        "WHERE `acco_country` = 'USA' GROUP BY `acco_account_name`"
    )


def test_window_builder_rejects_sql_injection():
    sql = "SELECT MEASURE(`M`) FROM `cat`.`sch`.`view`;"
    assert build_windowed_sql(sql, "1=1; DROP TABLE users", platform="DATABRICKS") is None
    assert build_windowed_sql("DROP TABLE foo", "a = 1", platform="DATABRICKS") is None


def test_unknown_platform_returns_none():
    assert build_windowed_sql("SELECT 1", "T.PERIOD = 1", platform="UNKNOWN") is None
    assert build_windowed_sql("SELECT 1", "T.PERIOD = 1", platform=None) is None
