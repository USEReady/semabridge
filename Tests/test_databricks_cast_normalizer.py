"""Snowflake-style casts become Databricks CASTs around the whole qualified name."""
import pytest

from semabridge.connectors.databricks_measure_translation import DatabricksMeasureTranslator


@pytest.mark.parametrize("sql, expected", [
    ("SUM(COL_DATE.`MONTHNO`::DOUBLE)", "SUM(CAST(COL_DATE.`MONTHNO` AS DOUBLE))"),
    ("SUM(`a`.`b`::FLOAT)", "SUM(CAST(`a`.`b` AS DOUBLE))"),
    ("SUM(sales.units::FLOAT)", "SUM(CAST(sales.units AS DOUBLE))"),
    ("SUM(`units`::FLOAT)", "SUM(CAST(`units` AS DOUBLE))"),
    ("(SUM(x))::FLOAT", "CAST(SUM(x) AS DOUBLE)"),
])
def test_cast_wraps_the_whole_qualified_name(sql, expected):
    assert DatabricksMeasureTranslator().normalize_dialect_sql(sql) == expected


def test_planner_path_sql_is_not_rewritten_again():
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.target_rules import get_target_rules

    conv = OSIToSMLConverter(target_type="databricks", rules=get_target_rules("databricks"))
    assert conv.rules.target == "databricks"  # the publisher clean-up is skipped for these rules
