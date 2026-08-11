"""Regression test for the real incident: '% Unit Market Share YOY Change'
([% Units Market Share]-[% Units Market Share SPLY]) kept showing the
stale placeholder reason "DAX translation deferred to Tier-5 batch
(Tier N)" -- set BEFORE the Tier-5 batch call even runs -- even after the
batch genuinely completed and could not resolve it, because nothing in
osi_to_sml.py's Step 3d/3e ever revisited a metric's own reason once the
deferral was actually over.

Root cause confirmed live (real PBIX, real Anthropic key): '% Units
Market Share SPLY' (CALCULATE([% Units Market Share],
SAMEPERIODLASTYEAR('Date'[Date]))) fails because the deterministic
time-intelligence renderer only knows how to wrap a plain aggregate
measure in a lag/period filter, not a ratio/conditional one ('% Units
Market Share' is IF(...,DIVIDE(...))) -- and by design (this session's
own earlier LAG-window-function incident fix), raw SAMEPERIODLASTYEAR DAX
is never handed to Tier 5 either. '% Unit Market Share YOY Change' then
fails purely as a CASCADE of that dependency having no SQL -- its own
DAX (plain bracket subtraction) has nothing wrong with it.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.converter.osi_to_sml import OSIToSMLConverter


def _metric(unique_name, expression, sql_expression=None, sync_failure_reason=None):
    return SimpleNamespace(
        unique_name=unique_name,
        expression=expression,
        sql_expression=sql_expression,
        sync_failure_reason=sync_failure_reason,
    )


def test_dependency_blocked_metric_gets_an_honest_reason_not_the_stale_placeholder():
    ratio_sply = _metric(
        "% Units Market Share SPLY",
        "CALCULATE([% Units Market Share], SAMEPERIODLASTYEAR('Date'[Date]))",
        sql_expression=None,
        sync_failure_reason="Complex Time Intelligence (SAMEPERIODLASTYEAR) not automatically translatable",
    )
    yoy_change = _metric(
        "% Unit Market Share YOY Change",
        "[% Units Market Share]-[% Units Market Share SPLY]",
        sql_expression=None,
        sync_failure_reason="DAX translation deferred to Tier-5 batch (Tier 4)",
    )
    sml = SimpleNamespace(metrics=[ratio_sply, yoy_change])

    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(sml)

    # The genuinely specific reason (set by the deterministic renderer
    # itself, not the stale batch placeholder) must be left untouched.
    assert ratio_sply.sync_failure_reason == (
        "Complex Time Intelligence (SAMEPERIODLASTYEAR) not automatically translatable"
    )
    # The cascade metric must now name its real, unresolved dependency --
    # not the stale "deferred to Tier-5 batch" wording.
    assert "deferred to Tier-5 batch" not in yoy_change.sync_failure_reason
    assert "% Units Market Share SPLY" in yoy_change.sync_failure_reason
    assert "Complex Time Intelligence (SAMEPERIODLASTYEAR)" in yoy_change.sync_failure_reason


def test_metric_with_no_unresolved_dependency_gets_the_batch_attempted_reason():
    """The metric's OWN DAX has no bracket reference to an unresolved
    sibling at all -- the honest reason is simply that ITS OWN Tier-5
    batch attempt produced nothing usable, not a dependency cascade."""
    lonely_metric = _metric(
        "Some Standalone Metric",
        "SUM([Units]) + 1",  # no [Bracket] measure reference at all
        sql_expression=None,
        sync_failure_reason="DAX translation deferred to Tier-5 batch (Tier 4)",
    )
    sml = SimpleNamespace(metrics=[lonely_metric])

    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(sml)

    assert "deferred to Tier-5 batch" not in lonely_metric.sync_failure_reason
    assert "Tier-5 (LLM) batch translation was attempted" in lonely_metric.sync_failure_reason


def test_dependency_that_did_resolve_does_not_trigger_the_dependency_wording():
    """Negative control: if every bracket reference DID get SQL, the stale
    placeholder must fall back to the "batch attempted" wording, not
    falsely claim a dependency is still unresolved."""
    resolved_dep = _metric("Total Units", "SUM([Units])", sql_expression="SUM(FACT.UNITS)")
    metric = _metric(
        "Total Units Plus One",
        "[Total Units] + 1",
        sql_expression=None,
        sync_failure_reason="DAX translation deferred to Tier-5 batch (Tier 4)",
    )
    sml = SimpleNamespace(metrics=[resolved_dep, metric])

    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(sml)

    assert "deferred to Tier-5 batch" not in metric.sync_failure_reason
    assert "Tier-5 (LLM) batch translation was attempted" in metric.sync_failure_reason
    assert "Total Units" not in metric.sync_failure_reason  # no false dependency claim


def test_already_resolved_metric_is_left_completely_untouched():
    resolved = _metric("Resolved Metric", "SUM([Units])", sql_expression="SUM(FACT.UNITS)")
    sml = SimpleNamespace(metrics=[resolved])

    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(sml)

    assert resolved.sql_expression == "SUM(FACT.UNITS)"
    assert resolved.sync_failure_reason is None


def test_non_stale_reason_is_never_overwritten():
    """Only the exact stale placeholder gets replaced -- any other reason
    a different part of the pipeline already set (however it got there)
    is left alone."""
    metric = _metric(
        "Some Metric",
        "SUM([Units])",
        sql_expression=None,
        sync_failure_reason="Some completely different, already-specific reason.",
    )
    sml = SimpleNamespace(metrics=[metric])

    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(sml)

    assert metric.sync_failure_reason == "Some completely different, already-specific reason."


def test_handles_empty_or_no_metrics_without_raising():
    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(SimpleNamespace(metrics=[]))
    OSIToSMLConverter()._replace_stale_tier5_deferred_reason(SimpleNamespace(metrics=None))
