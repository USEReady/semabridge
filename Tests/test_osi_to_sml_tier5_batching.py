"""Regression test for the dry-run timeout fix: OSIToSMLConverter.from_osi()
must resolve every Tier-5-eligible metric in ONE batched Tier5Service call,
never one individual Tier5Service.translate() call per metric.

Before this fix, _convert_metric() (Step 3a, once per metric) and
_resolve_metric_dependencies() (Step 3b, up to 3 more passes) both called
DAXTranslator.translate() without skip_tier5, so each metric that Tiers 1-4
declined got its own individual, sequential Tier5Service.translate() call --
trying every enabled provider (dead-key failovers included) before the
existing Step 3c/3d "batch" collection ever got a chance to see it (by which
point Tier 5 had already been attempted and had either succeeded or been
exhausted for that metric). This mirrors the existing deploy-path batch-call-
count tests (Tests/dax_translation/tier5/test_service.py's
test_translate_batch_uses_one_call_for_multiple_metrics and
Tests/test_dax_translator_dead_provider_cache.py), applied to the dry-run
(OSI->SML) path instead of the deploy (DDL) path.
"""
from __future__ import annotations

from types import SimpleNamespace

import semabridge.dax_translation.tier5.service as tier5_service_module
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIColumn,
    OSIDataType,
    OSIDataset,
    OSIMetric,
    OSIModel,
    OSIAggregationType,
)


class _FakeTier5Service:
    """Stands in for the real Tier5Service (monkeypatched at the same lazy
    import site DAXTranslator._get_tier5_service() uses). Tracks calls to
    .translate() (the individual, one-metric-per-call path) separately from
    .translate_batch() (the batched path) so the test can assert on call
    *shape*, not just total count."""

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


def _build_model_with_n_tier5_only_metrics(n: int) -> OSIModel:
    """Every metric's DAX is deliberately a shape Tiers 1-4 (regex/AST,
    free) all decline on: not a direct aggregation, not CALCULATE-with-
    simple-filter, not bracket arithmetic, not a recognized time-
    intelligence function, and not a real DAX AST node -- so each one can
    only ever be resolved via Tier 5, exactly the population this test
    needs to prove gets batched instead of called individually."""
    return OSIModel(
        unique_name="dry-run-batching-model",
        label="Dry Run Batching Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Fact",
                columns=[
                    OSIColumn(unique_name=f"Revenue{i}", data_type=OSIDataType.FLOAT)
                    for i in range(n)
                ],
            )
        ],
        metrics=[
            OSIMetric(
                unique_name=f"Metric_{i}",
                label=f"Metric_{i}",
                dataset="Fact",
                expression=f"SOMENONSENSEFUNC('Fact'[Revenue{i}])",
                aggregation=OSIAggregationType.NONE,
            )
            for i in range(n)
        ],
    )


def test_from_osi_batches_all_tier5_eligible_metrics_into_one_call(monkeypatch):
    _reset_fake_tier5_counters()
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _FakeTier5Service)

    n = 8
    osi_model = _build_model_with_n_tier5_only_metrics(n)
    converter = OSIToSMLConverter()
    sml_model = converter.from_osi(osi_model)

    assert sml_model.metric_count == n

    # The root-cause fix: zero individual Tier5Service.translate() calls --
    # every metric that needs Tier 5 must go through translate_batch() only.
    assert _FakeTier5Service.translate_call_count == 0, (
        "OSIToSMLConverter.from_osi() must never call Tier5Service.translate() "
        "(one metric per call) -- this is exactly the per-metric sequential "
        "API-call cost that caused the dry-run endpoint to exceed its "
        "180s client-side timeout with a live LLM key configured."
    )
    # Exactly one batched call, covering every eligible metric at once
    # (well under Tier5Config.max_batch_size=20, so no chunking needed here).
    assert _FakeTier5Service.translate_batch_call_count == 1, (
        "Expected exactly one Tier5Service.translate_batch() call for a "
        f"{n}-metric model (under the default max_batch_size=20), not "
        f"{_FakeTier5Service.translate_batch_call_count}"
    )
    assert _FakeTier5Service.translate_batch_request_counts == [n]


def test_from_osi_still_delegates_a_single_translate_batch_call_beyond_one_chunk(monkeypatch):
    """25 Tier-5-only metrics (more than Tier5Config's default
    max_batch_size=20) must still produce zero individual translate()
    calls and exactly one translate_batch() call from OSIToSMLConverter's/
    DAXTranslator.batch_translate_tier5()'s side -- i.e. chunking at 20 is
    Tier5Service.translate_batch()'s own internal responsibility (already
    covered by Tests/dax_translation/tier5/test_service.py::
    test_translate_batch_chunks_at_max_batch_size), and this fix must not
    reintroduce a per-chunk loop at the DAXTranslator/OSIToSMLConverter
    layer above it."""
    _reset_fake_tier5_counters()
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _FakeTier5Service)

    n = 25
    osi_model = _build_model_with_n_tier5_only_metrics(n)
    converter = OSIToSMLConverter()
    sml_model = converter.from_osi(osi_model)

    assert sml_model.metric_count == n
    assert _FakeTier5Service.translate_call_count == 0
    assert _FakeTier5Service.translate_batch_call_count == 1
    assert _FakeTier5Service.translate_batch_request_counts == [n]


# ---------------------------------------------------------------------------
# Correctness: a metric resolvable via pure Tiers 1-4 dependency substitution
# (e.g. [A]) that depends on a metric which itself needs Tier 5 (A) must
# still resolve correctly after this fix -- via the post-batch deterministic
# convergence pass (Step 3e in osi_to_sml.py), not by being sent to the LLM
# itself with an unresolved [A] bracket reference it has no context to make
# sense of. This is the specific dependency-ordering risk the fix's design
# had to account for: deferring Tier 5 to a single batch call moves WHEN A
# gets resolved to *after* Step 3b's dependency passes instead of before.
# ---------------------------------------------------------------------------

class _FakeTier5ServiceWithDependencyChain:
    """A's request succeeds (simulating the LLM translating the Tier-5-only
    metric); any other request (i.e. a metric that should have been
    resolved deterministically, not sent to the LLM at all) fails -- so if
    B ever reaches this fake with its raw, unresolved DAX, the test fails
    loudly instead of silently accepting a coincidentally-plausible result."""

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


def test_tier1_4_metric_depending_on_a_tier5_only_metric_still_resolves(monkeypatch):
    """Metric_B = [Metric_A] is a pure Tier 1-4 dependency substitution once
    Metric_A has SQL. Metric_A itself needs Tier 5. Before this fix, A was
    resolved individually in Step 3a, so Step 3b's very next pass resolved
    B deterministically in the same from_osi() call. After this fix, A is
    only resolved by the Step 3d batch (which runs after Step 3b) -- so B
    must be picked up by the new post-batch pass (Step 3e) instead."""
    _FakeTier5ServiceWithDependencyChain.translate_call_count = 0
    _FakeTier5ServiceWithDependencyChain.translate_batch_call_count = 0
    _FakeTier5ServiceWithDependencyChain.seen_metric_names = []
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _FakeTier5ServiceWithDependencyChain)

    osi_model = OSIModel(
        unique_name="dependency-chain-model",
        label="Dependency Chain Model",
        source_platform="fabric",
        datasets=[
            OSIDataset(
                unique_name="Fact",
                columns=[OSIColumn(unique_name="Revenue", data_type=OSIDataType.FLOAT)],
            )
        ],
        metrics=[
            OSIMetric(
                unique_name="Metric_B", label="Metric_B", dataset="Fact",
                expression="[Metric_A]", aggregation=OSIAggregationType.NONE,
            ),
            OSIMetric(
                unique_name="Metric_A", label="Metric_A", dataset="Fact",
                expression="SOMENONSENSEFUNC('Fact'[Revenue])",
                aggregation=OSIAggregationType.NONE,
            ),
        ],
    )

    converter = OSIToSMLConverter()
    sml_model = converter.from_osi(osi_model)
    sql_by_name = {m.unique_name: m.sql_expression for m in sml_model.metrics}

    assert _FakeTier5ServiceWithDependencyChain.translate_call_count == 0
    assert "Metric_A" in _FakeTier5ServiceWithDependencyChain.seen_metric_names

    # The correctness contract this test exists to prove: B's own raw
    # request to the batch (its [Metric_A] reference is unresolved at that
    # point, so the fake correctly returns None for it, same as a real
    # provider would) must NOT be the final word on B. The post-batch
    # deterministic pass (Step 3e) must overwrite that failure with the
    # correct substitution once A's SQL is available -- same end result as
    # before this fix, just reached one step later.
    assert sql_by_name["Metric_A"] == 'SUM(fact."REVENUE")'
    assert sql_by_name["Metric_B"] == 'SUM(fact."REVENUE")', (
        "Metric_B ([Metric_A]) must resolve via deterministic substitution "
        "once Metric_A has SQL (the Step 3e post-batch pass), not be left "
        "stuck with whatever (correctly unresolved) result the batch itself "
        "returned for its raw, context-free request."
    )
