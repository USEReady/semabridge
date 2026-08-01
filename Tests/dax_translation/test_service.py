"""Unit tests for semabridge.dax_translation.service.DaxTranslationService —
synthetic data only. Proves Tier 1-4 is tried first and Tier 5 is only
reached when every deterministic tier declines.
"""
from semabridge.dax_translation.types import TranslationRequest
from semabridge.dax_translation.service import DaxTranslationService

_COL_LOOKUP = {"SomeTable": {"SOMECOLUMN"}}
_ALIASES = {"SomeTable": "sometable"}


def _request(dax, **overrides):
    defaults = dict(
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup=_COL_LOOKUP,
        dataset_aliases=_ALIASES,
    )
    defaults.update(overrides)
    return TranslationRequest(dax=dax, **defaults)


def test_tier1_result_returned_without_reaching_tier5(monkeypatch):
    service = DaxTranslationService()

    def _fail_if_called(*a, **k):
        raise AssertionError("Tier 5 should not be reached when Tier 1-4 succeeds")

    monkeypatch.setattr(service._tier5, "translate", _fail_if_called)

    result = service.translate_metric(_request("SUM('SomeTable'[SomeColumn])"))
    assert result.tier == 1
    assert result.sql == 'SUM(sometable."SOMECOLUMN")'


def test_falls_through_to_tier5_when_deterministic_tiers_decline(monkeypatch):
    service = DaxTranslationService()

    calls = []

    def _fake_tier5_translate(request):
        calls.append(request.dax)
        from semabridge.dax_translation.types import TranslationResult
        return TranslationResult(
            sql='SUM(sometable."SOMECOLUMN")',
            tier=5,
            original_dax=request.dax,
            provider="fake_provider",
            translation_provider_confidence=0.8,
        )

    monkeypatch.setattr(service._tier5, "translate", _fake_tier5_translate)

    result = service.translate_metric(_request("SOMENONSENSEFUNCTION(1,2,3)"))
    assert calls == ["SOMENONSENSEFUNCTION(1,2,3)"]
    assert result.tier == 5
    assert result.provider == "fake_provider"


def test_returns_unsuccessful_result_when_every_tier_declines(monkeypatch):
    service = DaxTranslationService()
    monkeypatch.setattr(service._tier5, "translate", lambda request: None)

    result = service.translate_metric(_request("SOMENONSENSEFUNCTION(1,2,3)"))
    assert result.is_success is False
    assert result.sql is None
    assert result.tier == 4


def test_translate_batch_preserves_order(monkeypatch):
    service = DaxTranslationService()
    monkeypatch.setattr(service._tier5, "translate_batch", lambda requests: [None] * len(requests))

    requests = [
        _request("SUM('SomeTable'[SomeColumn])"),
        _request("SOMENONSENSEFUNCTION(1,2,3)"),
    ]
    results = service.translate_batch(requests)
    assert len(results) == 2
    assert results[0].tier == 1
    assert results[0].is_success is True
    assert results[1].is_success is False


def test_translate_batch_sends_only_tier5_leftovers_to_translate_batch(monkeypatch):
    """Tier 1-4 still runs per-item first for every request (the
    tier-ordering invariant) — only the subset every deterministic tier
    declines on should ever reach Tier5Service.translate_batch()."""
    service = DaxTranslationService()

    seen_dax = []

    def _fake_tier5_translate_batch(requests):
        seen_dax.extend(r.dax for r in requests)
        from semabridge.dax_translation.types import TranslationResult
        return [
            TranslationResult(
                sql=f"SUM(sometable.RESOLVED_{i})",
                tier=5,
                original_dax=r.dax,
                provider="fake_provider",
            )
            for i, r in enumerate(requests)
        ]

    monkeypatch.setattr(service._tier5, "translate_batch", _fake_tier5_translate_batch)

    requests = [
        _request("SUM('SomeTable'[SomeColumn])"),  # Tier 1 resolves this
        _request("SOMENONSENSEFUNCTION(1,2,3)"),  # only this needs Tier 5
        _request("ANOTHERNONSENSEFUNCTION(4,5,6)"),  # and this
    ]
    results = service.translate_batch(requests)

    assert seen_dax == ["SOMENONSENSEFUNCTION(1,2,3)", "ANOTHERNONSENSEFUNCTION(4,5,6)"]
    assert len(results) == 3
    assert results[0].tier == 1
    assert results[1].tier == 5 and results[1].provider == "fake_provider"
    assert results[2].tier == 5 and results[2].provider == "fake_provider"


def test_translate_batch_empty_input(monkeypatch):
    service = DaxTranslationService()
    assert service.translate_batch([]) == []
