"""Generic unit tests for semantic description diff classification and caveat sensitivity.

Tests the four distinct shapes:
1. Clause removed + unrelated sentence added -> TEXT_REMOVED (high severity)
2. Clause replaced by equivalent clause -> TEXT_REWORDED
3. Sentence appended with nothing removed -> TEXT_ADDED
4. Single word corrected (typo) -> TEXT_REWORDED (low severity)
"""

import pytest
from semabridge.governance.drift.description_diff import (
    classify_and_diff_description,
    DiffSeverity,
)


def test_clause_removed_and_unrelated_sentence_added_classified_as_removed():
    """When a clause with caveats/constraints is removed and an unrelated sentence is added,

    the change must be classified as TEXT_REMOVED with high severity, not rewording.
    """
    baseline = (
        "Base calculation rule for item total. Note that specific filter conditions apply "
        "when grouping by target attribute; results differ without the mandatory filter."
    )
    live = (
        "Base calculation rule for item total. Updated live directly in staging environment "
        "for verification testing."
    )

    diff = classify_and_diff_description(baseline, live)

    assert diff["classification"] == "TEXT_REMOVED"
    assert diff["severity"] == DiffSeverity.HIGH.value
    assert len(diff["removed_caveats"]) > 0


def test_clause_replaced_by_equivalent_clause_classified_as_reworded():
    """When a clause is replaced by an equivalent phrasing with similar length/meaning,

    it must be classified as TEXT_REWORDED with uncertainty flag recorded.
    """
    baseline = "Must be filtered by calendar date for aggregate summation."
    live = "Requires filtering on calendar date for aggregate summation."

    diff = classify_and_diff_description(baseline, live)

    assert diff["classification"] == "TEXT_REWORDED"
    assert diff["is_uncertain"] is True
    assert diff["confidence"] > 0.0


def test_sentence_appended_with_nothing_removed_classified_as_added():
    """When text is appended to an existing description with nothing removed,

    it must be classified as TEXT_ADDED.
    """
    baseline = "Total count of active records across all partitions."
    live = "Total count of active records across all partitions. Excludes archived items."

    diff = classify_and_diff_description(baseline, live)

    assert diff["classification"] == "TEXT_ADDED"
    assert diff["severity"] in (DiffSeverity.MEDIUM.value, DiffSeverity.LOW.value)


def test_single_word_corrected_classified_as_reworded_typo():
    """When a minor typo or single word is corrected,

    it must be classified as TEXT_REWORDED with low severity and no uncertainty.
    """
    baseline = "Standard aggregate metric across entity records."
    live = "Standard aggregated metric across entity records."

    diff = classify_and_diff_description(baseline, live)

    assert diff["classification"] == "TEXT_REWORDED"
    assert diff["severity"] == DiffSeverity.LOW.value
    assert diff["is_uncertain"] is False

