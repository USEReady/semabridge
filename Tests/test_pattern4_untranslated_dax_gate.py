import pytest
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer

def test_pattern4_untranslated_dax_validation_gate():
    translator = MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer())
    dataset_col_lookup = {"SalesFact": {"UNITS", "REVENUE"}}
    dataset_aliases = {"SalesFact": "SalesFact"}

    # 1. Raw DAX containing CALCULATE should be rejected
    is_valid, reason = translator._validate_metric_column_references(
        "CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]='No')",
        "Sentiment_Gap",
        dataset_col_lookup,
        dataset_aliases,
    )
    assert not is_valid
    assert "DAX" in reason

    # 2. Raw DAX containing measure bracket syntax should be rejected
    is_valid, reason = translator._validate_metric_column_references(
        "[Sentiment] - [Sentiment_SPLY]",
        "Sentiment_Diff",
        dataset_col_lookup,
        dataset_aliases,
    )
    assert not is_valid
    assert "bracket" in reason

    # 3. Clean SQL expression should pass
    is_valid, reason = translator._validate_metric_column_references(
        'SUM(SalesFact."UNITS")',
        "Total_Units",
        dataset_col_lookup,
        dataset_aliases,
    )
    assert is_valid
    assert reason is None
