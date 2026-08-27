from __future__ import annotations
import pytest
import sys
import os
import json
import re

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

from semabridge.utils.logger import setup_logging
setup_logging(level="DEBUG")

from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer

# Test cases based on user feedback
test_cases = [
    {
        "name": "Simple SUM",
        "dax": "SUM('SalesFact'[Units])",
        "expected_pattern": "SUM",
        "should_pass": True,
    },
    {
        "name": "YTD",
        "dax": "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        # CURRENT_DATE() is the correct anchor, not the synthetic MAX_DATE
        # enriched-view column — see connectors/translator.py's e4c8322 fix
        # and dax_rule_translator.py's translate_time_intelligence_with_anchors.
        "expected_pattern": "CURRENT_DATE",
        "should_pass": True,
    },
    {
        "name": "VanArsdel Units",
        "dax": "CALCULATE(SUM('SalesFact'[Units]), 'Product'[isVanArsdel] = \"Yes\")",
        "expected_pattern": "CASE WHEN",
        "should_pass": True,
    },
    {
        "name": "Market Share",
        "dax": "DIVIDE([VanArsdel Units], [Total Units], 0)",
        "expected_pattern": "COALESCE",
        "should_pass": True,
    },
    {
        "name": "Sentiment Gap",
        "dax": 'CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]="No") - CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]="Yes")',
        "expected_pattern": "SUM",
        "should_pass": True,
    },
    {
        "name": "Invalid YTD with CURRENT_DATE",
        "dax": "TOTALYTD(SUM('SalesFact'[Units]), 'Date'[Date])",
        "sql_override": "SUM(CASE WHEN DATE.YEAR = YEAR(CURRENT_DATE) AND DATE.DATE <= CURRENT_DATE THEN SALESFACT.UNITS ELSE 0 END)",
        "should_pass": False,
    },
    {
        "name": "Invalid Nested Aggregate",
        "dax": "SUMX(Sales, SUM(Orders[Amount]))",
        "sql_override": "SUM(SUM(ORDERS.AMOUNT))",
        "should_pass": False,
    }
]

class MockMetric:
    """A simple mock object to simulate a metric."""
    def __init__(self, expression, unique_name, dataset):
        self.expression = expression
        self.unique_name = unique_name
        self.dataset = dataset

@pytest.fixture(scope="module")
def translator():
    """Provides a MetricExpressionTranslator instance."""
    sanitizer = IdentifierSanitizer(force_uppercase=True)
    return MetricExpressionTranslator(identifier_sanitizer=sanitizer)

def get_translation_from_cache(dax: str, metric_name: str) -> str | None:
    """Helper to pull a translation from the cache file."""
    cache_file = '.llm_dax_cache.json'
    if not os.path.exists(cache_file):
        return None
    
    with open(cache_file, 'r') as f:
        try:
            cache = json.load(f)
        except json.JSONDecodeError:
            return None

    # Search for a key that matches the metric name and dax
    for key, value in cache.items():
        if key.startswith(f"{metric_name}|") and key.endswith(f"|{dax}"):
             return value
    
    # Fallback for older cache format
    cache_key = f"{metric_name}|{dax}"
    return cache.get(cache_key)


@pytest.mark.parametrize("test_case", test_cases)
def test_dax_translation_validation(translator: MetricExpressionTranslator, test_case: dict):
    """
    Tests the validate_and_test_translation method against various DAX patterns.
    """
    dax = test_case["dax"]
    metric_name = test_case["name"]
    should_pass = test_case["should_pass"]

    # If a SQL override is provided, inject it into the result to test validation logic directly
    if "sql_override" in test_case:
        # Monkey-patch the _get_cached_or_translate to return the override
        original_method = translator._get_cached_or_translate
        translator._get_cached_or_translate = lambda d, m: test_case["sql_override"]
        
        validation_result = translator.validate_and_test_translation(dax, metric_name)
        
        # Restore original method
        translator._get_cached_or_translate = original_method
    else:
        # Use the real translation method and then validate the result
        print(f"!!!!!! Calling _try_llm_metric_fallback_expression for: {dax}")
        
        metric_obj = MockMetric(dax, metric_name, "Test")

        translated_sql = translator._try_llm_metric_fallback_expression(
            metric=metric_obj,
            metric_name=metric_name,
            table_alias="FACT_TABLE",
            alias_by_raw={},
            dataset_col_lookup={"Test": {"Units", "Date", "isVanArsdel", "Sentiment", "MfgisVanArsdel", "Amount", "Quantity", "Unit_Price"}},
            dataset_aliases={"Test": "FACT_TABLE"},
            metric_name_set={"VanArsdel Units", "Total Units", "Sentiment"},
            all_physical_col_names=set(),
            emittable_metric_name_set=set(),
            skipped_metric_names=set()
        )
        
        # Since we have the SQL now, we can't use the validation method that relies on cache.
        # We will simulate the validation logic here.
        # A better approach would be to refactor `validate_and_test_translation` to accept SQL.
        
        issues = []
        if not translated_sql:
            issues.append("Translation failed - no SQL generated")
        else:
            sql_upper = translated_sql.upper()
            # Rule 1: No SELECT, FROM, JOIN
            forbidden = ["SELECT", "FROM", "JOIN", "WITH", "SUBQUERY"]
            for f in forbidden:
                if f in sql_upper:
                    issues.append(f"Contains forbidden keyword: {f}")
            
            # Rule 2: No nested aggregates
            # A simple regex check that is not perfect but good enough for these tests
            if len(re.findall(r'\b(SUM|AVG|COUNT)\s*\(', translated_sql, re.IGNORECASE)) > 1:
                # Check if they are separate statements (e.g., SUM(...) / SUM(...))
                if '/' not in translated_sql and '+' not in translated_sql and '-' not in translated_sql:
                    issues.append("Contains potentially nested aggregates")

            # Rule 5: No window functions
            if "OVER" in sql_upper or "PARTITION BY" in sql_upper:
                issues.append("Contains window function")

        validation_result = {
            "translated_sql": translated_sql,
            "issues": issues,
            "is_valid": len(issues) == 0
        }

    print(f"Testing '{metric_name}':")
    print(f"  DAX: {dax}")
    print(f"  SQL: {validation_result['translated_sql']}")
    print(f"  Issues: {validation_result['issues']}")
    print(f"  Is Valid: {validation_result['is_valid']}")

    if should_pass:
        # For tests that are expected to pass, we assert validity and pattern matching
        assert (
            validation_result["is_valid"] is True
        ), f"Validation issues: {validation_result['issues']}"
        if validation_result["translated_sql"] and "expected_pattern" in test_case:
            assert test_case["expected_pattern"].upper() in validation_result["translated_sql"].upper()
    else:
        # For tests that are not expected to pass, we can check for specific issues or just assert is_valid is False
        assert validation_result["is_valid"] is False


def test_malformed_json_response_rejection():
    from semabridge.dax_translation.tier5.prompt import parse_structured_response
    from semabridge.dax_translation.tier5.validation import _is_scalar_metric_sql

    # 1. Malformed JSON with error text
    raw_json_error = '{\n  "explanation": "DAX expression implies the relationship cannot be resolved",\n  "error": "syntax error"\n}'
    sql, conf = parse_structured_response(raw_json_error)
    assert sql is None, f"Expected None SQL for malformed JSON error payload, got: {sql!r}"

    # 2. Direct _is_scalar_metric_sql test with curly braces
    assert _is_scalar_metric_sql(raw_json_error) is False
    assert _is_scalar_metric_sql('FACT."METRIC" AS {"error": "cannot translate"}') is False


