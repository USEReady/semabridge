import pytest
from semabridge.connectors.translator import MetricExpressionTranslator

@pytest.fixture
def translator():
    """Returns a basic instance of MetricExpressionTranslator for testing AST functions."""
    return MetricExpressionTranslator(
        identifier_sanitizer=None,
        dialect="snowflake",
        behavior=None,
        config=None
    )

def test_cast_stripping(translator):
    """Test cases for CAST and TO_DOUBLE stripping (Scenarios 31-35)."""
    # Note: These call the specific method we implemented for AST transformation
    test_cases = [
        ("CAST(column AS FLOAT)", "column"),
        ("TO_DOUBLE(column)", "column"),
        ("CAST(SUM(revenue) AS DOUBLE)", "SUM(revenue)"),
        ("CAST( (a+b) AS FLOAT )", "(a + b)"),
        ("SUM(CAST(val AS FLOAT))", "SUM(val)"),
    ]
    
    for input_sql, expected_output in test_cases:
        output = translator.fix_common_llm_issues(input_sql)
        # Using string replacement to ignore whitespace differences if any
        assert output.replace(" ", "").upper() == expected_output.replace(" ", "").upper()

def test_date_function_normalization(translator):
    """Test cases for DATE_PART, DATE_TRUNC, DATEADD normalization (Scenarios 23-30)."""
    from semabridge.connectors.snowflake_emitter import _sanitize_snowflake_date_functions
    
    test_cases = [
        ("DATE_PART(YEAR, col)", "DATE_PART('year', col)"),
        ("DATE_PART('YEAR', col)", "DATE_PART('year', col)"),
        ("DATE_PART(\"YEAR\", col)", "DATE_PART('year', col)"),
        ("DATE_PART(  YEAR  , col)", "DATE_PART('year', col)"),
        ("DATE_TRUNC(MONTH, col)", "DATE_TRUNC('month', col)"),
        ("DATEADD(YEAR, 1, col)", "DATEADD('year', 1, col)"),
        ("DATEADD(month, -3, col)", "DATEADD('month', -3, col)"),
        ("DATEADD('year', DATE_PART(YEAR, col), col)", "DATEADD('year', DATE_PART('year', col), col)"),
    ]
    
    for input_sql, expected_output in test_cases:
        output = _sanitize_snowflake_date_functions(input_sql)
        assert output.replace(" ", "").upper() == expected_output.replace(" ", "").upper()

