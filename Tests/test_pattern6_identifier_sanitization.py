import pytest
from semabridge.utils.identifiers import IdentifierSanitizer

def test_pattern6_identifier_sanitization_synthetic():
    sanitizer = IdentifierSanitizer()
    
    # Special characters: %, @, #, space, leading digits
    assert sanitizer.sanitize_column("% Units Market Share") == "PCT_UNITS_MARKET_SHARE"
    assert sanitizer.sanitize_column("@Indicator01") == "ATINDICATOR01"
    assert sanitizer.sanitize_column("@Indicator02") == "ATINDICATOR02"
    assert sanitizer.sanitize_column("#Space01") == "NUMSPACE01"
    assert sanitizer.sanitize_column("#Space02") == "NUMSPACE02"
    assert sanitizer.sanitize_column("123Metric") == "_123METRIC"
    assert sanitizer.sanitize_column("Total Units YTD Var %") == "TOTAL_UNITS_YTD_VAR_PCT"

    # Verify uniform sanitization output for references
    metric_name = "%_CATEGORY_COMPETE_SHARE"
    ref_sanitized = sanitizer.sanitize_column(metric_name)
    assert ref_sanitized == "PCT_CATEGORY_COMPETE_SHARE"

    # Verify decimal numbers, leading decimals, and negative decimals in expressions
    assert sanitizer.sanitize_column(".5") == "_5"
    assert sanitizer.sanitize_column("-.5") == "_5"
    assert sanitizer.sanitize_column(".5_METRIC") == "_5_METRIC"


