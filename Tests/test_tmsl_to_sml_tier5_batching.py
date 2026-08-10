"""Regression test for the same dry-run-timeout-class fix applied to the
deploy path: TMSLTransformer.transform() must resolve every Tier-5-eligible
measure in ONE batched Tier5Service call, never one individual
Tier5Service.translate() call per measure.

Before this fix, _parse_measure() (Step 2a, once per measure across every
table) and _resolve_metric_dependencies() (Step 2c, up to 3 more passes)
both called DAXTranslator.translate() without skip_tier5, so each measure
that Tiers 1-4 declined got its own individual, sequential
Tier5Service.translate() call -- the exact per-metric-before-batch pattern
already fixed in osi_to_sml.py (see test_osi_to_sml_tier5_batching.py),
just on the TMSL/deploy path (core/executor.py, CLI syncs) instead of the
OSI/dry-run path.
"""
from __future__ import annotations

from types import SimpleNamespace

import semabridge.dax_translation.tier5.service as tier5_service_module
from semabridge.converter.tmsl_to_sml import TMSLTransformer


class _FakeTier5Service:
    """Stands in for the real Tier5Service (monkeypatched at the same lazy
    import site DAXTranslator._get_tier5_service() uses)."""

    translate_call_count = 0
    translate_batch_call_count = 0
    translate_batch_request_counts: list[int] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def translate(self, request):
        _FakeTier5Service.translate_call_count += 1
        return None

    def translate_batch(self, requests):
        _FakeTier5Service.translate_batch_call_count += 1
        _FakeTier5Service.translate_batch_request_counts.append(len(requests))
        return [None] * len(requests)


def _reset_fake_tier5_counters():
    _FakeTier5Service.translate_call_count = 0
    _FakeTier5Service.translate_batch_call_count = 0
    _FakeTier5Service.translate_batch_request_counts = []


def _build_tmsl_with_n_tier5_only_measures(n: int) -> dict:
    """Every measure's DAX is deliberately a shape Tiers 1-4 (regex/AST,
    free) all decline on -- see the identical helper in
    test_osi_to_sml_tier5_batching.py for the full reasoning."""
    return {
        "model": {
            "name": "TierBatchingModel",
            "tables": [
                {
                    "name": "Fact",
                    "columns": [
                        {"name": f"Revenue{i}", "dataType": "double"} for i in range(n)
                    ],
                    "measures": [
                        {
                            "name": f"Metric_{i}",
                            "expression": f"SOMENONSENSEFUNC('Fact'[Revenue{i}])",
                        }
                        for i in range(n)
                    ],
                }
            ],
        }
    }


def test_transform_batches_all_tier5_eligible_measures_into_one_call(monkeypatch):
    _reset_fake_tier5_counters()
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _FakeTier5Service)

    n = 8
    tmsl = _build_tmsl_with_n_tier5_only_measures(n)
    sml_model = TMSLTransformer().transform(tmsl, "ws-1", "ds-1")

    # >= not ==: _auto_detect_metrics (a separate, pre-existing heuristic
    # unrelated to this fix) can add extra synthetic metrics for numeric
    # columns it doesn't recognize as already covered by an explicit
    # measure. What this test cares about is that all n explicit measures
    # made it through, and exactly n went to the Tier-5 batch (asserted
    # below) -- not the model's total metric count.
    assert sml_model.metric_count >= n

    assert _FakeTier5Service.translate_call_count == 0, (
        "TMSLTransformer.transform() must never call Tier5Service.translate() "
        "(one measure per call) -- this is exactly the per-metric sequential "
        "API-call cost the osi_to_sml.py dry-run-timeout fix eliminated on "
        "the dry-run path; it must be eliminated here too on the deploy path."
    )
    assert _FakeTier5Service.translate_batch_call_count == 1, (
        f"Expected exactly one Tier5Service.translate_batch() call for a "
        f"{n}-measure model, not {_FakeTier5Service.translate_batch_call_count}"
    )
    assert _FakeTier5Service.translate_batch_request_counts == [n]


def test_transform_still_delegates_a_single_translate_batch_call_beyond_one_chunk(monkeypatch):
    """25 Tier-5-only measures (more than Tier5Config's default
    max_batch_size=20) must still produce zero individual translate() calls
    and exactly one translate_batch() call -- chunking at 20 is
    Tier5Service.translate_batch()'s own internal responsibility, already
    covered by Tests/dax_translation/tier5/test_service.py::
    test_translate_batch_chunks_at_max_batch_size."""
    _reset_fake_tier5_counters()
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _FakeTier5Service)

    n = 25
    tmsl = _build_tmsl_with_n_tier5_only_measures(n)
    sml_model = TMSLTransformer().transform(tmsl, "ws-1", "ds-1")

    # >= not ==: _auto_detect_metrics (a separate, pre-existing heuristic
    # unrelated to this fix) can add extra synthetic metrics for numeric
    # columns it doesn't recognize as already covered by an explicit
    # measure. What this test cares about is that all n explicit measures
    # made it through, and exactly n went to the Tier-5 batch (asserted
    # below) -- not the model's total metric count.
    assert sml_model.metric_count >= n
    assert _FakeTier5Service.translate_call_count == 0
    assert _FakeTier5Service.translate_batch_call_count == 1
    assert _FakeTier5Service.translate_batch_request_counts == [n]


class _FakeTier5ServiceWithDependencyChain:
    """A's request succeeds (simulating the LLM translating the Tier-5-only
    measure); any other request fails -- so if B ever reaches this fake
    with its raw, unresolved DAX, the test fails loudly."""

    translate_call_count = 0
    translate_batch_call_count = 0
    seen_metric_names: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    def translate(self, request):
        _FakeTier5ServiceWithDependencyChain.translate_call_count += 1
        return None

    def translate_batch(self, requests):
        _FakeTier5ServiceWithDependencyChain.translate_batch_call_count += 1
        _FakeTier5ServiceWithDependencyChain.seen_metric_names.extend(
            r.metric_name for r in requests
        )
        results = []
        for r in requests:
            if r.metric_name == "Metric_A":
                results.append(SimpleNamespace(
                    is_success=True,
                    sql='SUM(fact."REVENUE")',
                    tier=5,
                    provider="fake",
                    translation_provider_confidence=0.9,
                ))
            else:
                results.append(None)
        return results


def test_tier1_4_measure_depending_on_a_tier5_only_measure_still_resolves(monkeypatch):
    """Metric_B = [Metric_A] is a pure Tier 1-4 dependency substitution once
    Metric_A has SQL. Metric_A itself needs Tier 5. Mirrors
    test_osi_to_sml_tier5_batching.py's identical scenario for the OSI/dry-
    run pipeline, applied here to the TMSL/deploy pipeline."""
    _FakeTier5ServiceWithDependencyChain.translate_call_count = 0
    _FakeTier5ServiceWithDependencyChain.translate_batch_call_count = 0
    _FakeTier5ServiceWithDependencyChain.seen_metric_names = []
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _FakeTier5ServiceWithDependencyChain)

    tmsl = {
        "model": {
            "name": "DependencyChainModel",
            "tables": [
                {
                    "name": "Fact",
                    "columns": [{"name": "Revenue", "dataType": "double"}],
                    "measures": [
                        # B defined BEFORE A in source order, deliberately --
                        # forces reliance on the post-batch convergence pass
                        # rather than Step 2a's own growing-metrics_context.
                        {"name": "Metric_B", "expression": "[Metric_A]"},
                        {
                            "name": "Metric_A",
                            "expression": "SOMENONSENSEFUNC('Fact'[Revenue])",
                        },
                    ],
                }
            ],
        }
    }

    sml_model = TMSLTransformer().transform(tmsl, "ws-1", "ds-1")
    sql_by_name = {m.unique_name: m.sql_expression for m in sml_model.metrics}

    assert _FakeTier5ServiceWithDependencyChain.translate_call_count == 0
    assert "Metric_A" in _FakeTier5ServiceWithDependencyChain.seen_metric_names

    assert sql_by_name["Metric_A"] == 'SUM(fact."REVENUE")'
    assert sql_by_name["Metric_B"] == 'SUM(fact."REVENUE")', (
        "Metric_B ([Metric_A]) must resolve via deterministic substitution "
        "once Metric_A has SQL (the post-batch _resolve_metric_dependencies "
        "pass), not be left stuck with whatever (correctly unresolved) "
        "result the batch itself returned for its raw, context-free request."
    )
