"""Regression test: MetricExpressionTranslator._try_basic_dax_metric_fallback_expression
must reuse one DAXTranslator instance across every metric processed in a run.

Before the fix, this method constructed a fresh DAXTranslator() on every call
(see connectors/translator.py). Since DAXTranslator itself lazily builds and
caches a Tier5Service per DAXTranslator instance (converter/dax_translator.py),
a fresh DAXTranslator() per metric meant a fresh, empty
Tier5Service._unavailable_providers cache per metric too — so a provider whose
credentials are already known-dead (e.g. an expired Groq key) got retried, and
re-logged its auth-failure warning pair, once per metric instead of once for
the whole run.

This test stands in a fake DAXTranslator (monkeypatched at the same lazy
import site translator.py uses) that mimics that exact per-instance dead-cache
shape, so the test is isolated from Tier 1-4/AST/Tier5 internals and only
proves the caching contract: constructing 20 metrics' worth of fallback calls
through one MetricExpressionTranslator instance must create exactly one
DAXTranslator and touch the "provider" exactly once.
"""
from __future__ import annotations

from types import SimpleNamespace

import semabridge.converter.dax_translator as dax_translator_mod
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer


class _FakeDAXTranslationResult:
    def __init__(self):
        self.is_success = False
        self.sql = None


class _FakeDAXTranslator:
    """Stands in for the real DAXTranslator. Each instance simulates its own
    Tier5Service._unavailable_providers cache: the first .translate() call on
    a given instance hits the (already-dead) provider and fails; every
    subsequent call on the SAME instance is served from that instance's own
    cache and never touches the provider again — exactly DAXTranslator's real
    per-instance-lifetime behavior."""

    instances_created = 0
    provider_call_count = 0

    def __init__(self):
        _FakeDAXTranslator.instances_created += 1
        self._provider_marked_dead = False

    def translate(self, *args, **kwargs):
        if not self._provider_marked_dead:
            _FakeDAXTranslator.provider_call_count += 1
            self._provider_marked_dead = True
        return _FakeDAXTranslationResult()


def _metric(name: str, dax: str):
    return SimpleNamespace(unique_name=name, name=name, dataset="SomeTable", expression=dax)


def test_dead_provider_attempted_once_across_batch_of_20_metrics(monkeypatch):
    """Simulates one run translating 20 metrics that all fall through to the
    basic-DAX-fallback path. Before the fix: 20 DAXTranslator instances, 20
    provider attempts. After the fix: 1 instance, 1 provider attempt."""
    _FakeDAXTranslator.instances_created = 0
    _FakeDAXTranslator.provider_call_count = 0
    monkeypatch.setattr(dax_translator_mod, "DAXTranslator", _FakeDAXTranslator)

    translator = MetricExpressionTranslator(IdentifierSanitizer())

    # A DAX shape that _try_basic_dax_metric_fallback_expression's own
    # earlier COUNTROWS/COUNTBLANK/bare-aggregation regexes don't match, and
    # that isn't the bracket-arithmetic-without-an-aggregate shape (which
    # returns None early when model=None) — so every call reliably falls
    # through to the DAXTranslator().translate() call under test.
    dax = "CALCULATE(SUM([Sales]), FILTER('SomeTable', [Sales] > 0))"
    col_lookup = {"SomeTable": {"Sales"}}

    for i in range(20):
        translator._try_basic_dax_metric_fallback_expression(
            _metric(f"Metric_{i}", dax),
            "sometable",
            col_lookup,
            model=None,
            dataset_by_name=None,
        )

    assert _FakeDAXTranslator.instances_created == 1, (
        "MetricExpressionTranslator must reuse one DAXTranslator instance "
        "across every metric in a run, not construct a fresh one per metric"
    )
    assert _FakeDAXTranslator.provider_call_count == 1, (
        "A known-dead provider must be attempted once per run, not once per metric"
    )


def test_get_dax_translator_is_lazy_and_cached():
    """_get_dax_translator() must construct DAXTranslator at most once and
    return the same instance on every subsequent call."""
    translator = MetricExpressionTranslator(IdentifierSanitizer())
    assert translator._dax_translator is None

    first = translator._get_dax_translator()
    second = translator._get_dax_translator()
    assert first is second
