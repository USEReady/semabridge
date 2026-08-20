"""Unit tests for DAXTranslator.batch_translate_tier5's new `relationships`
param: each candidate's TranslationRequest gets a per-metric
reachable_table_aliases map (every OTHER dataset in dataset_aliases that
has_relationship_path confirms is reachable from that metric's own
dataset_name), letting Tier 5 legally reference a relationship-reachable
dimension's column instead of always declining cross-table filters.

Additive/opt-in: relationships=None (the default) must produce the exact
same TranslationRequest.reachable_table_aliases=None/empty as before this
param existed.
"""
from __future__ import annotations

from semabridge.converter import dax_translator as dax_translator_module
from semabridge.dax_translation.tier5 import service as tier5_service_module
from semabridge.sml.models import SMLRelationship


def _rel(from_ds, from_cols, to_ds, to_cols):
    return SMLRelationship(
        unique_name=f"REL_{from_ds}_{to_ds}",
        from_dataset=from_ds,
        from_columns=from_cols,
        to_dataset=to_ds,
        to_columns=to_cols,
    )


class _CapturingFakeTier5Service:
    """Stands in for the real Tier5Service, capturing every TranslationRequest
    passed to translate_batch() for inspection -- same monkeypatch site as
    Tests/test_osi_to_sml_tier5_batching.py."""

    captured_requests = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def translate_batch(self, requests):
        _CapturingFakeTier5Service.captured_requests = list(requests)
        return [None] * len(requests)


def _force_all_metrics_to_llm(monkeypatch):
    # is_simple_metric would route a plain SUM(...) to rule-based
    # translation, never reaching the LLM path this test needs to inspect --
    # force every candidate through the LLM branch instead.
    monkeypatch.setattr(dax_translator_module, "is_simple_metric", lambda dax: False)


def test_relationships_supplied_populates_reachable_table_aliases(monkeypatch):
    _force_all_metrics_to_llm(monkeypatch)
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _CapturingFakeTier5Service)
    _CapturingFakeTier5Service.captured_requests = None

    translator = dax_translator_module.DAXTranslator()
    metrics_list = [("SpecialProductAmount", "SOMENONSENSEFUNC([X])", "fact", "Fact")]
    dataset_aliases = {"Fact": "fact", "Product": "product", "Unrelated": "unrelated"}
    relationships = [_rel("Fact", ["ProductId"], "Product", ["ProductId"])]

    translator.batch_translate_tier5(
        metrics_list,
        dataset_col_lookup={},
        dataset_aliases=dataset_aliases,
        relationships=relationships,
    )

    assert _CapturingFakeTier5Service.captured_requests is not None
    request = _CapturingFakeTier5Service.captured_requests[0]
    assert request.reachable_table_aliases == {"Product": "product"}
    assert "Unrelated" not in request.reachable_table_aliases
    assert "Fact" not in request.reachable_table_aliases  # never includes its own dataset


def test_relationships_omitted_preserves_prior_behavior_exactly(monkeypatch):
    """Additive/opt-in: no relationships param -> empty reachable_table_aliases,
    identical to how every call site behaved before this param existed."""
    _force_all_metrics_to_llm(monkeypatch)
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _CapturingFakeTier5Service)
    _CapturingFakeTier5Service.captured_requests = None

    translator = dax_translator_module.DAXTranslator()
    metrics_list = [("SomeMetric", "SOMENONSENSEFUNC([X])", "fact", "Fact")]
    dataset_aliases = {"Fact": "fact", "Product": "product"}

    translator.batch_translate_tier5(
        metrics_list,
        dataset_col_lookup={},
        dataset_aliases=dataset_aliases,
    )

    request = _CapturingFakeTier5Service.captured_requests[0]
    assert request.reachable_table_aliases == {}


def test_unreachable_dataset_is_excluded(monkeypatch):
    _force_all_metrics_to_llm(monkeypatch)
    monkeypatch.setattr(tier5_service_module, "Tier5Service", _CapturingFakeTier5Service)
    _CapturingFakeTier5Service.captured_requests = None

    translator = dax_translator_module.DAXTranslator()
    metrics_list = [("SomeMetric", "SOMENONSENSEFUNC([X])", "fact", "Fact")]
    dataset_aliases = {"Fact": "fact", "Disconnected": "disconnected"}
    relationships = [_rel("Other", ["Id"], "Disconnected", ["Id"])]  # no path from Fact

    translator.batch_translate_tier5(
        metrics_list,
        dataset_col_lookup={},
        dataset_aliases=dataset_aliases,
        relationships=relationships,
    )

    request = _CapturingFakeTier5Service.captured_requests[0]
    assert request.reachable_table_aliases == {}
