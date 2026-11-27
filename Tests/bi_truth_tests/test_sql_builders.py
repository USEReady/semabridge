"""Unit tests for platform-specific BI Truth SQL Builders."""
from semabridge.ai.sql_builders import (
    DatabricksTruthSQLBuilder,
    SnowflakeTruthSQLBuilder,
    get_builder,
)


def test_get_builder_resolution():
    assert isinstance(get_builder("DATABRICKS"), DatabricksTruthSQLBuilder)
    assert isinstance(get_builder("databricks"), DatabricksTruthSQLBuilder)
    assert isinstance(get_builder("SNOWFLAKE"), SnowflakeTruthSQLBuilder)
    assert get_builder("") is None
    assert get_builder("UNKNOWN") is None


def test_databricks_builder_total_and_sliced():
    b = DatabricksTruthSQLBuilder()
    view = "cat.sch.model_metric_view"
    assert b.build_total(view, "AVG_OF_REV") == (
        "SELECT MEASURE(`AVG_OF_REV`) FROM `cat`.`sch`.`model_metric_view`;"
    )
    assert b.build_sliced(view, "AVG_OF_REV", "ACCOUNTS.ACCOUNT_NAME") == (
        "SELECT `acco_account_name`, MEASURE(`AVG_OF_REV`) "
        "FROM `cat`.`sch`.`model_metric_view` GROUP BY `acco_account_name`;"
    )
    assert b.build_top_n(view, "AVG_OF_REV", "ACCOUNTS.COUNTRY", n=5) == (
        "SELECT `acco_country`, MEASURE(`AVG_OF_REV`) "
        "FROM `cat`.`sch`.`model_metric_view` "
        "GROUP BY `acco_country` ORDER BY MEASURE(`AVG_OF_REV`) DESC LIMIT 5;"
    )


def test_databricks_builder_deduplicates_catalog_schema_prefix():
    b = DatabricksTruthSQLBuilder()
    doubled = "cat.sch.cat.sch.model_metric_view"
    assert b.quote_identifier(doubled) == "`cat`.`sch`.`model_metric_view`"
    assert b.quote_identifier("cat.sch.my view") == "`cat`.`sch`.`my view`"


def test_snowflake_builder_total_sliced_and_dedup():
    b = SnowflakeTruthSQLBuilder()
    view = "SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC"
    assert b.build_total(view, "ACTIVES") == (
        "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC METRICS ACTIVES);"
    )
    assert b.build_sliced(view, "ACTIVES", "BU.BU_NAME") == (
        "SELECT * FROM SEMANTIC_VIEW(SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC METRICS ACTIVES DIMENSIONS BU.BU_NAME);"
    )
    doubled = "SEMABRIDGE.HUMAN_RESOURCES.SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC"
    assert b.quote_identifier(doubled) == "SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC"
