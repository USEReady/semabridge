"""Tests for the dry-run "static risk tier" — a deliberately coarse,
3-value label (STATIC_RISK_NO_KNOWN_RISK / STATIC_RISK_KNOWN_RISKY_PATTERN
/ STATIC_RISK_PREDICTED_FAILURE) computed entirely from static signals
already collected elsewhere in the pipeline. No live connection, no LLM
call, no dependency on llm_self_reported_confidence.

All synthetic/placeholder data, no real project or metric names.
"""
from __future__ import annotations

from semabridge.api.services.project_mapping_engine import (
    STATIC_RISK_KNOWN_RISKY_PATTERN,
    STATIC_RISK_LABELS,
    STATIC_RISK_NO_KNOWN_RISK,
    STATIC_RISK_PREDICTED_FAILURE,
    STATUS_PREDICTED_FAILURE,
    _compute_static_risk_tier,
    build_entity_mappings,
)
from semabridge.converter.dax_ast_parser import ADVISORY_CATEGORY_UNREACHABLE_DIMENSION


# ---------------------------------------------------------------------------
# _compute_static_risk_tier unit tests
# ---------------------------------------------------------------------------

def test_non_metric_entity_gets_no_tier_at_all():
    """Nothing to statically risk-assess about a bare column/table."""
    entity = {"entity_kind": "column", "complexity_tier": 5}
    assert _compute_static_risk_tier(entity, status="auto") is None


def test_predicted_failure_status_maps_straight_through():
    """STATIC_RISK_PREDICTED_FAILURE is an alias of STATUS_PREDICTED_FAILURE,
    not a recomputation -- any status already resolved to
    STATUS_PREDICTED_FAILURE (by _entity_status or
    reconcile_predicted_failures_with_drops) must map through unchanged,
    regardless of anything else on the entity."""
    entity = {"entity_kind": "metric", "complexity_tier": 1}
    tier = _compute_static_risk_tier(entity, status=STATUS_PREDICTED_FAILURE)
    assert tier == STATIC_RISK_PREDICTED_FAILURE
    assert STATIC_RISK_LABELS[tier] == "Failed a static check — predicted failure"


def test_tier_1_4_metric_with_clean_status_is_no_known_risk():
    entity = {"entity_kind": "metric", "complexity_tier": 2, "source_expression": "SUM(Fact[Amount])"}
    tier = _compute_static_risk_tier(entity, status="auto")
    assert tier == STATIC_RISK_NO_KNOWN_RISK
    assert STATIC_RISK_LABELS[tier] == "No known risk signals"


def test_clean_tier5_metric_with_no_retries_and_no_risky_dax_is_no_known_risk():
    entity = {
        "entity_kind": "metric",
        "complexity_tier": 5,
        "validation_notes": [],
        "source_expression": "CALCULATE(SUM(Fact[Amount]), Dim[Region]=\"West\")",
    }
    assert _compute_static_risk_tier(entity, status="auto") == STATIC_RISK_NO_KNOWN_RISK


def test_tier5_metric_with_validation_notes_is_known_risky_pattern():
    """A non-empty validation_notes means at least one earlier candidate
    for THIS metric was rejected by a static check before a later attempt
    succeeded -- a real signal, not a guess."""
    entity = {
        "entity_kind": "metric",
        "complexity_tier": 5,
        "validation_notes": ["openai: response is not a safe scalar metric SQL shape — rejected"],
        "source_expression": "SUM(Fact[Amount])",
    }
    tier = _compute_static_risk_tier(entity, status="auto")
    assert tier == STATIC_RISK_KNOWN_RISKY_PATTERN
    assert STATIC_RISK_LABELS[tier] == "Known risky pattern detected"


def test_tier5_metric_with_rolling_window_dax_is_known_risky_pattern():
    entity = {
        "entity_kind": "metric",
        "complexity_tier": 5,
        "validation_notes": [],
        "source_expression": "CALCULATE(SUM(Fact[Amount]), DATESINPERIOD(Cal[Date], LASTDATE(Cal[Date]), -12, MONTH))",
    }
    assert _compute_static_risk_tier(entity, status="auto") == STATIC_RISK_KNOWN_RISKY_PATTERN


def test_tier5_metric_with_time_intelligence_function_is_known_risky_pattern():
    entity = {
        "entity_kind": "metric",
        "complexity_tier": 5,
        "validation_notes": [],
        "source_expression": "CALCULATE(SUM(Fact[Amount]), PREVIOUSMONTH(Cal[Date]))",
    }
    assert _compute_static_risk_tier(entity, status="auto") == STATIC_RISK_KNOWN_RISKY_PATTERN


def test_tier_1_4_metric_with_risky_looking_dax_text_is_still_no_known_risk():
    """The risky-DAX-keyword signal only applies to Tier-5 results -- a
    Tier 1-4 metric already went through the deterministic AST renderer,
    not the LLM fallback, so the same keyword carries no risk signal
    here."""
    entity = {
        "entity_kind": "metric",
        "complexity_tier": 3,
        "validation_notes": [],
        "source_expression": "CALCULATE(SUM(Fact[Amount]), PREVIOUSMONTH(Cal[Date]))",
    }
    assert _compute_static_risk_tier(entity, status="auto") == STATIC_RISK_NO_KNOWN_RISK


# ---------------------------------------------------------------------------
# Independence test (explicitly required): a high self-reported confidence
# must NEVER upgrade a metric out of predicted_failure.
# ---------------------------------------------------------------------------

def test_high_self_reported_confidence_does_not_override_predicted_failure():
    """The exact scenario this session's confidence-vs-correctness
    experiment was run to guard against: a metric that fails a hard static
    check (unreachable dimension) but whose Tier-5 provider was highly
    "confident" must still be labeled predicted_failure. static_risk_tier
    must be computed with zero dependency on llm_self_reported_confidence."""
    model = {
        "unique_name": "SyntheticModel",
        "datasets": [
            {"unique_name": "SelectorDim", "columns": [{"unique_name": "Choice", "data_type": "int"}]},
        ],
        "metrics": [
            {
                "unique_name": "OverconfidentButBrokenMetric",
                "dataset": "SelectorDim",
                "expression": "CALCULATE([Volume],'Other'[Year]=1)",
                "sync_enabled": True,
                "sync_failure_reason": None,
                "advisory_notes": [
                    "This metric's CALCULATE(...) filters by table 'Other', but "
                    "'SelectorDim' has no relationship path to 'Other'."
                ],
                "advisory_categories": [ADVISORY_CATEGORY_UNREACHABLE_DIMENSION],
                "complexity_tier": 5,
                "validation_notes": [],
                # The high self-reported number this test is guarding
                # against ever influencing the tier below.
                "llm_self_reported_confidence": 0.95,
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

    row = next(r for r in built["mappings"] if r["source_name"] == "OverconfidentButBrokenMetric")
    assert row["status"] == STATUS_PREDICTED_FAILURE
    assert row["static_risk_tier"] == STATIC_RISK_PREDICTED_FAILURE
    assert row["static_risk_label"] == "Failed a static check — predicted failure"
    # The number itself still passes through for display -- it just must
    # never have been read by the tier computation.
    assert row["llm_self_reported_confidence"] == 0.95


def test_build_entity_mappings_wires_static_risk_tier_for_ordinary_metrics():
    """End-to-end sanity check through the real build_entity_mappings
    entry point (not just the unit-level _compute_static_risk_tier), for
    both a clean Tier-1 metric and a Tier-5 metric with a risky DAX
    shape."""
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
                "complexity_tier": 1,
            },
            {
                "unique_name": "RiskyTier5Metric",
                "dataset": "Fact",
                "expression": "CALCULATE(SUM(Fact[Amount]), PREVIOUSMONTH(Cal[Date]))",
                "sync_enabled": True,
                "complexity_tier": 5,
                "validation_notes": ["anthropic: confidence 0.40 below min_confidence 0.55"],
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
    assert rows_by_name["CleanMetric"]["static_risk_tier"] == STATIC_RISK_NO_KNOWN_RISK
    assert rows_by_name["RiskyTier5Metric"]["static_risk_tier"] == STATIC_RISK_KNOWN_RISKY_PATTERN
    # Non-metric rows carry the key but it's always None.
    table_or_column_rows = [r for r in built["mappings"] if r["entity_kind"] != "metric"]
    assert table_or_column_rows  # sanity: there is at least one non-metric row
    assert all(r["static_risk_tier"] is None for r in table_or_column_rows)


def test_reconcile_predicted_failures_keeps_static_risk_tier_in_sync():
    """A metric promoted to predicted_failure via the DropLedger
    reconciliation path (not via advisory_categories) must ALSO have its
    static_risk_tier updated -- static_risk_tier is computed once inside
    build_entity_mappings, before reconciliation runs, so without this
    sync a DropLedger-only failure would show status=predicted_failure but
    a stale static_risk_tier=no_known_risk."""
    from semabridge.api.services.project_mapping_engine import (
        reconcile_predicted_failures_with_drops,
    )

    model = {
        "unique_name": "SyntheticModel",
        "datasets": [
            {"unique_name": "Fact", "columns": [{"unique_name": "Amount", "data_type": "double"}]},
        ],
        "metrics": [
            {
                "unique_name": "DroppedAtDdlTime",
                "dataset": "Fact",
                "expression": "SUM(Fact[Amount])",
                "sync_enabled": True,
                "complexity_tier": 1,
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
    row = next(r for r in built["mappings"] if r["source_name"] == "DroppedAtDdlTime")
    assert row["status"] == "auto"
    assert row["static_risk_tier"] == STATIC_RISK_NO_KNOWN_RISK

    dropped_entities = [
        {
            "entity_kind": "metric",
            "entity_name": "DroppedAtDdlTime",
            "dataset": "Fact",
            "stage": "ddl_emission",
            "reason": "some emission-time failure",
            "by_design": False,
        },
    ]
    reconcile_predicted_failures_with_drops(built["mappings"], dropped_entities)

    assert row["status"] == STATUS_PREDICTED_FAILURE
    assert row["static_risk_tier"] == STATIC_RISK_PREDICTED_FAILURE
    assert row["static_risk_label"] == "Failed a static check — predicted failure"


# ---------------------------------------------------------------------------
# reconcile_enrichment_unverifiable_advisories -- the other half of the
# real incident fix: build_entity_mappings() runs BEFORE the trial
# DDL-build pass, so it can never see an advisory category
# metrics_clause_builder.py only adds to the LIVE trial_sml metric object
# during that later pass. This merges it back onto the already-built row.
# ---------------------------------------------------------------------------

def test_reconcile_enrichment_unverifiable_advisories_merges_onto_matching_row():
    from types import SimpleNamespace
    from semabridge.api.services.project_mapping_engine import (
        reconcile_enrichment_unverifiable_advisories,
    )
    from semabridge.converter.time_intelligence_shapes import (
        ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE,
    )

    mappings = [
        {
            "entity_kind": "metric",
            "source_name": "Total Units YTD",
            "advisory_categories": [],
            "advisory_notes": [],
        },
        {
            "entity_kind": "metric",
            "source_name": "Untouched Metric",
            "advisory_categories": [],
            "advisory_notes": [],
        },
    ]
    trial_metrics = [
        SimpleNamespace(
            unique_name="Total Units YTD",
            advisory_categories=[ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE],
            advisory_notes=["Cannot verify in dry-run — resolved by live enrichment at deploy time."],
        ),
        SimpleNamespace(unique_name="Untouched Metric", advisory_categories=[], advisory_notes=[]),
    ]

    reconcile_enrichment_unverifiable_advisories(mappings, trial_metrics)

    assert mappings[0]["advisory_categories"] == [ADVISORY_CATEGORY_ENRICHMENT_COLUMN_UNVERIFIABLE]
    assert "Cannot verify in dry-run" in mappings[0]["advisory_notes"][0]
    assert mappings[1]["advisory_categories"] == []  # untouched metric stays untouched


def test_reconcile_enrichment_unverifiable_advisories_is_a_union_not_an_overwrite():
    """A category the extraction-time pass already found (build_entity_
    mappings' own reading of the pre-trial-pass model) must survive even
    if the trial metric object doesn't happen to carry it too."""
    from types import SimpleNamespace
    from semabridge.api.services.project_mapping_engine import (
        reconcile_enrichment_unverifiable_advisories,
    )

    mappings = [
        {
            "entity_kind": "metric",
            "source_name": "Some Metric",
            "advisory_categories": ["pre_existing_category"],
            "advisory_notes": ["pre-existing note"],
        },
    ]
    trial_metrics = [
        SimpleNamespace(
            unique_name="Some Metric",
            advisory_categories=["enrichment_column_unverifiable"],
            advisory_notes=["a new note"],
        ),
    ]

    reconcile_enrichment_unverifiable_advisories(mappings, trial_metrics)

    assert mappings[0]["advisory_categories"] == ["pre_existing_category", "enrichment_column_unverifiable"]
    assert mappings[0]["advisory_notes"] == ["pre-existing note", "a new note"]


def test_reconcile_enrichment_unverifiable_advisories_handles_empty_inputs_without_raising():
    from semabridge.api.services.project_mapping_engine import (
        reconcile_enrichment_unverifiable_advisories,
    )

    reconcile_enrichment_unverifiable_advisories([], None)
    reconcile_enrichment_unverifiable_advisories([{"entity_kind": "metric", "source_name": "X"}], [])
    reconcile_enrichment_unverifiable_advisories(None, None)  # must not raise even on None mappings
