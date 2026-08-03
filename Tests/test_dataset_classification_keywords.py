"""Regression tests for the shared calendar/dimension whole-word keyword
detection used by converter/osi_to_sml.py and converter/tmsl_to_sml.py.

Both files used to check `"DATE" in name.upper() or "CALENDAR" in
name.upper()` (calendar-dimension existence) and tmsl_to_sml.py separately
checked `any(x in name.upper() for x in ["BU","BUSINESSUNIT","DIM","USER",
"CALENDAR"])` (force-dimension classification override) — both unbounded
substring checks. A dataset named "Validated_Orders", "Consolidated_Fact",
or "Distribution_Fact" would trip these by accident, either wrongly
skipping calendar-dimension injection (leaving no real date dimension) or
wrongly forcing a genuine fact table to be classified as a dimension.

Synthetic placeholder names only.
"""
from semabridge.connectors.dataset_classification_keywords import (
    CALENDAR_KEYWORDS,
    DIMENSION_OVERRIDE_KEYWORDS,
    is_calendar_like_name,
    is_dimension_like_name,
)


def test_whole_word_calendar_keyword_is_detected():
    assert is_calendar_like_name("Date") is True
    assert is_calendar_like_name("Calendar") is True
    assert is_calendar_like_name("Dim_Dates") is True


def test_calendar_mid_word_substring_is_not_a_false_positive():
    """The exact bug: these all contain 'date'/'calendar' as a substring
    but are not calendar/date dimensions."""
    for name in ("Validated_Orders", "Update_Log", "Consolidated_Fact", "Mandate_Terms"):
        assert is_calendar_like_name(name) is False, name


def test_whole_word_dimension_override_keyword_is_detected():
    assert is_dimension_like_name("BU_MAPPING") is True
    assert is_dimension_like_name("UserDimension") is True
    assert is_dimension_like_name("Calendar") is True
    assert is_dimension_like_name("Dim_Product") is True


def test_dimension_override_mid_word_substring_is_not_a_false_positive():
    """The exact bug: these all contain 'bu' as a substring but are real
    fact-shaped tables, not business-unit dimensions."""
    for name in ("Distribution_Fact", "Contribution_Margin", "Attribute_Sales", "Suburb_Region"):
        assert is_dimension_like_name(name) is False, name


def test_business_unit_compound_phrase_still_matches_despite_tokenization():
    """'BusinessUnit' is a genuine two-word compound; the whole-word
    tokenizer alone would split it into ['BUSINESS', 'UNIT'] and miss it
    (neither token alone is a safe standalone keyword — 'BUSINESS' would
    false-positive on 'Business_Metrics_Fact'). Matched as its own
    two-word phrase instead."""
    assert is_dimension_like_name("BusinessUnit") is True
    assert is_dimension_like_name("Business_Unit") is True
    assert is_dimension_like_name("Business Unit") is True


def test_business_standalone_does_not_false_positive_on_a_real_fact_table():
    assert is_dimension_like_name("Business_Metrics_Fact") is False
    assert is_dimension_like_name("BusinessMetricsFact") is False


def test_keyword_sets_are_generic_not_tied_to_any_specific_project():
    assert CALENDAR_KEYWORDS == frozenset({"DATE", "DATES", "CALENDAR"})
    assert DIMENSION_OVERRIDE_KEYWORDS == frozenset({"BU", "DIM", "USER", "CALENDAR"})
