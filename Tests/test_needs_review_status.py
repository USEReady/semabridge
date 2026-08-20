"""Regression tests for the dry-run "needs_review" status ("Option 2" of
the SAMEPERIODLASTYEAR/time-intelligence fallback work): a metric whose DAX
translated to real, deployable SQL via allow_unshifted_fallback (an
unshifted approximation standing in for a lag-period shift the renderer
couldn't safely apply -- see dax_ast_parser.ADVISORY_CATEGORY_LAG_PERIOD_
UNSHIFTED_FALLBACK) must be tagged "needs_review", not folded into "auto"
where a user could not tell it apart from a genuinely clean translation.

Deliberately distinct from STATUS_PREDICTED_FAILURE (Tests/
test_predicted_failure_status_reconciliation.py): that status means "this
will fail at real deploy time"; needs_review means "this deployed fine, but
a human should confirm the fallback is acceptable." Both are keyed off
advisory_categories, via disjoint sets (REAL_DEPLOY_ONLY_ADVISORY_CATEGORIES
vs REVIEW_REQUIRED_ADVISORY_CATEGORIES), so this suite also checks the two
never collide for the same category.
"""
from __future__ import annotations

from semabridge.api.services.project_mapping_engine import (
    REAL_DEPLOY_ONLY_ADVISORY_CATEGORIES,
    REVIEW_REQUIRED_ADVISORY_CATEGORIES,
    STATUS_NEEDS_REVIEW,
    STATUS_PREDICTED_FAILURE,
    _compute_static_risk_tier,
    _entity_status,
    build_entity_mappings,
)
from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
    ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT,
    ADVISORY_CATEGORY_UNREACHABLE_DIMENSION,
)


# ---------------------------------------------------------------------------
# _entity_status unit tests
# ---------------------------------------------------------------------------

def test_lag_period_unshifted_fallback_category_yields_needs_review():
    entity = {
        "entity_kind": "metric",
        "advisory_categories": [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK],
    }
    assert _entity_status(entity, is_manual=False) == STATUS_NEEDS_REVIEW


def test_needs_review_overrides_manual_too():
    """Same rationale as predicted_failure: renaming a target identifier
    doesn't change whether the underlying SQL is a fallback approximation
    a human should double-check."""
    entity = {
        "entity_kind": "metric",
        "advisory_categories": [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK],
    }
    assert _entity_status(entity, is_manual=True) == STATUS_NEEDS_REVIEW


def test_no_advisory_categories_stays_auto():
    entity = {"entity_kind": "metric", "advisory_categories": []}
    assert _entity_status(entity, is_manual=False) == "auto"


def test_real_deploy_only_category_wins_over_needs_review_if_both_present():
    """REAL_DEPLOY_ONLY_ADVISORY_CATEGORIES and REVIEW_REQUIRED_ADVISORY_
    CATEGORIES are disjoint sets today, but _entity_status checks
    predicted_failure first -- a metric that is BOTH known to fail at
    real-deploy time AND carries an unrelated needs-review advisory must
    still surface as predicted_failure, the more severe signal."""
    entity = {
        "entity_kind": "metric",
        "advisory_categories": [
            ADVISORY_CATEGORY_UNREACHABLE_DIMENSION,
            ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK,
        ],
    }
    assert _entity_status(entity, is_manual=False) == STATUS_PREDICTED_FAILURE


def test_the_two_advisory_category_sets_are_disjoint():
    assert not (REAL_DEPLOY_ONLY_ADVISORY_CATEGORIES & REVIEW_REQUIRED_ADVISORY_CATEGORIES)


def test_pending_live_schema_enrichment_category_yields_needs_review():
    entity = {
        "entity_kind": "metric",
        "advisory_categories": [ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT],
    }
    assert _entity_status(entity, is_manual=False) == STATUS_NEEDS_REVIEW


# ---------------------------------------------------------------------------
# _compute_static_risk_tier must not contradict needs_review with a
# "predicted failure" badge driven by the same underlying sync_enabled=False
# signal that produced needs_review in the first place.
# ---------------------------------------------------------------------------

def test_needs_review_status_gets_no_static_risk_badge_even_though_sync_failed():
    """The exact bug this fix closes: a metric whose CALCULATE(...) filters
    a relationship-reachable table fails translation at dry-run
    (sync_enabled=False, sync_failure_reason set) -- the SAME raw signal
    _compute_static_risk_tier used to unconditionally read as
    STATIC_RISK_PREDICTED_FAILURE. Once _entity_status has already
    resolved this to needs_review, the risk tier must defer to that,
    not re-derive predicted_failure from the raw sync fields."""
    entity = {
        "entity_kind": "metric",
        "sync_enabled": False,
        "sync_failure_reason": "Tier-5 (LLM) batch translation was attempted for this metric and did not produce a usable result.",
        "advisory_categories": [ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT],
    }
    assert _compute_static_risk_tier(entity, status=STATUS_NEEDS_REVIEW) is None


# ---------------------------------------------------------------------------
# End-to-end sanity check through the real build_entity_mappings entry point
# ---------------------------------------------------------------------------

def test_build_entity_mappings_wires_needs_review_status():
    model = {
        "unique_name": "SyntheticModel",
        "datasets": [
            {"unique_name": "Fact", "columns": [{"unique_name": "Amount", "data_type": "double"}]},
        ],
        "metrics": [
            {
                "unique_name": "CleanMetric",
                "dataset": "Fact",
                "expression": "SUM(Fact[Amount])",
                "sync_enabled": True,
                "sync_failure_reason": None,
                "advisory_notes": [],
                "advisory_categories": [],
            },
            {
                "unique_name": "UnshiftedFallbackMetric",
                "dataset": "Fact",
                "expression": "CALCULATE([SomeOtherMeasure], SAMEPERIODLASTYEAR('Date'[Date]))",
                "sync_enabled": True,
                "sync_failure_reason": None,
                "advisory_notes": ["Shipped as an unshifted approximation; needs review."],
                "advisory_categories": [ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK],
            },
        ],
    }

    built = build_entity_mappings(
        project_id="synthetic-proj",
        model=model,
        existing_mappings={},
        session_key="synthetic-session",
        target_connector="snowflake",
    )

    rows_by_name = {row["source_name"]: row for row in built["mappings"]}
    assert rows_by_name["CleanMetric"]["status"] == "auto"
    assert rows_by_name["UnshiftedFallbackMetric"]["status"] == STATUS_NEEDS_REVIEW
    assert rows_by_name["UnshiftedFallbackMetric"]["status"] != "auto"
