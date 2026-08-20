"""Regression test for osi_to_sml.py's _apply_translation_advisories helper:
translation.advisory_categories (populated by DAXTranslator's Tier-3
time-intelligence call site when it opts into allow_unshifted_fallback) must
mirror onto SMLMetric.advisory_categories/advisory_notes in lockstep -- the
same same-index parallel-list convention documented on SMLMetric.

Covers the "Option 2" wiring end of the pipeline: dax_ast_parser's
allow_unshifted_fallback (tested at the renderer level in
Tests/test_dax_general_patterns.py's TestLagPeriodUnshiftedFallbackIsOptInOnly)
and DAXTranslator.translate()'s Tier-3 propagation (tested in
Tests/test_dax_general_patterns.py's
test_lag_period_of_unshiftable_measure_ships_advisory_flagged_fallback_instead_of_failing)
both feed into this mirroring step before project_mapping_engine.py's
_entity_status can key off metric.advisory_categories.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
    ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK,
)
from semabridge.converter.osi_to_sml import _apply_translation_advisories


def _metric(advisory_categories=None, advisory_notes=None):
    return SimpleNamespace(
        advisory_categories=list(advisory_categories or []),
        advisory_notes=list(advisory_notes or []),
    )


def _translation(advisory_categories=None):
    return SimpleNamespace(advisory_categories=list(advisory_categories or []))


def test_new_advisory_category_and_note_are_appended_in_lockstep():
    metric = _metric()
    translation = _translation([ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK])

    _apply_translation_advisories(metric, translation)

    assert metric.advisory_categories == [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK]
    assert metric.advisory_notes == [ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK]


def test_no_advisory_categories_on_translation_leaves_metric_untouched():
    metric = _metric()
    translation = _translation([])

    _apply_translation_advisories(metric, translation)

    assert metric.advisory_categories == []
    assert metric.advisory_notes == []


def test_reapplying_the_same_category_is_idempotent():
    """A convergence pass may call this more than once for the same metric
    (e.g. from_osi's Tier-5 batch-apply loop followed by
    _resolve_metric_dependencies) -- must never duplicate entries."""
    metric = _metric()
    translation = _translation([ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK])

    _apply_translation_advisories(metric, translation)
    _apply_translation_advisories(metric, translation)

    assert metric.advisory_categories == [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK]
    assert metric.advisory_notes == [ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK]


def test_prior_unrelated_advisory_is_preserved_alongside_the_new_one():
    """Must extend, not overwrite -- a metric may already carry an advisory
    from a different producer (e.g. the unreachable-dimension detector,
    which runs later in from_osi) by the time this mirrors a translation
    advisory onto it."""
    metric = _metric(
        advisory_categories=["unreachable_dimension"],
        advisory_notes=["Some prior unrelated advisory."],
    )
    translation = _translation([ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK])

    _apply_translation_advisories(metric, translation)

    assert metric.advisory_categories == [
        "unreachable_dimension",
        ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
    ]
    assert metric.advisory_notes == [
        "Some prior unrelated advisory.",
        ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK,
    ]
