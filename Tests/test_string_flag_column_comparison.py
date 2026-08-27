"""
Regression test for string-typed flag column comparisons.

Ensures that columns whose declared schema data type is STRING / VARCHAR (even if their
names match boolean pattern heuristics like IS_ACTIVE, IS_VALID, IS_VANARSDEL, HAS_DISCOUNT)
are NEVER falsely transformed into boolean literal comparisons (= TRUE / = FALSE) or IFF(... = TRUE, 1, 0),
and are always compared using proper string literals (e.g. 'Yes', 'No', 'True', 'False').
"""
import pytest
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.dax_translation.tier5.validation import MetricSqlValidator
from semabridge.sml.models import DataType


def test_build_safe_sum_sql_respects_string_data_type():
    translator = MetricExpressionTranslator()
    
    # 1. With string column type mapping provided
    column_types = {
        "IS_ACTIVE": DataType.STRING,
        "IS_VALID": "VARCHAR",
        "PRODUCT_ISVANARSDEL": "string",
        "IS_FLAG_BOOL": DataType.BOOLEAN,
    }
    
    # String column starting with IS_ should NOT become IFF(... = TRUE, 1, 0)
    sql_str1 = translator._build_safe_sum_sql("T.IS_ACTIVE", identifier_hint="T.IS_ACTIVE", column_data_types=column_types)
    assert "= TRUE" not in sql_str1
    assert "SUM(T.IS_ACTIVE::FLOAT)" in sql_str1

    sql_str2 = translator._build_safe_sum_sql("T.PRODUCT_ISVANARSDEL", identifier_hint="PRODUCT_ISVANARSDEL", column_data_types=column_types)
    assert "= TRUE" not in sql_str2
    assert "SUM(T.PRODUCT_ISVANARSDEL::FLOAT)" in sql_str2

    # Boolean column starting with IS_ SHOULD become IFF(... = TRUE, 1, 0)
    sql_bool = translator._build_safe_sum_sql("T.IS_FLAG_BOOL", identifier_hint="T.IS_FLAG_BOOL", column_data_types=column_types)
    assert "= TRUE" in sql_bool
    assert "SUM(IFF(T.IS_FLAG_BOOL = 1 OR T.IS_FLAG_BOOL = TRUE, 1, 0))" in sql_bool


def test_validator_build_safe_sum_sql_respects_string_data_type():
    validator = MetricSqlValidator()
    column_types = {
        "HAS_PROMOTION": "TEXT",
    }
    
    sql = validator._build_safe_sum_sql("T.HAS_PROMOTION", identifier_hint="HAS_PROMOTION", dialect="snowflake", column_data_types=column_types)
    assert "= TRUE" not in sql
    assert "SUM(T.HAS_PROMOTION::FLOAT)" in sql
