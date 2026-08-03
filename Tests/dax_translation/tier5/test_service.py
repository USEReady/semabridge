"""Unit tests for semabridge.dax_translation.tier5.service — the orchestrator.

Uses fake, injected adapters (never real network calls) to prove the
mandatory-validation, confidence-gate, and provider-order behavior in
isolation, with synthetic DAX/schema data only.
"""
from semabridge.dax_translation.types import TranslationRequest
from semabridge.dax_translation.tier5.config import Tier5Config, ProviderSettings
from semabridge.dax_translation.tier5 import service as tier5_service_module
from semabridge.dax_translation.tier5.adapters.base import ProviderAuthError, RawResult

_COL_LOOKUP = {"SomeTable": {"SOMECOLUMN"}}
_ALIASES = {"SomeTable": "sometable"}


def _request(**overrides):
    defaults = dict(
        dax="SUM('SomeTable'[SomeColumn])",
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup=_COL_LOOKUP,
        dataset_aliases=_ALIASES,
        metric_name="Metric_A",
    )
    defaults.update(overrides)
    return TranslationRequest(**defaults)


class _FakeAdapter:
    """Test double satisfying the ProviderAdapter protocol."""

    def __init__(self, settings, *, response_text=None, confidence=0.9, raises=False, raises_auth=False):
        self.settings = settings
        self._response_text = response_text
        self._confidence = confidence
        self._raises = raises
        self._raises_auth = raises_auth
        self.call_count = 0

    def is_available(self) -> bool:
        return True

    def translate(self, prompt: str, system_message: str):
        self.call_count += 1
        if self._raises_auth:
            raise ProviderAuthError("fake_provider", RuntimeError("401 invalid_api_key"))
        if self._raises:
            raise RuntimeError("simulated adapter failure")
        if self._response_text is None:
            return None
        return RawResult(text=self._response_text, confidence=self._confidence)


def _config_with_fake_adapters(adapter_factory_by_name: dict, provider_order=None) -> Tier5Config:
    providers = {name: ProviderSettings(enabled_env="ALWAYS_ON_FOR_TEST") for name in adapter_factory_by_name}
    config = Tier5Config(
        provider_order=provider_order or list(adapter_factory_by_name.keys()),
        min_confidence=0.55,
        providers=providers,
    )

    import os
    os.environ["ALWAYS_ON_FOR_TEST"] = "1"  # enabled_env just needs to be truthy

    original_classes = dict(tier5_service_module._ADAPTER_CLASSES)
    tier5_service_module._ADAPTER_CLASSES.update(adapter_factory_by_name)
    return config, original_classes


def _restore_adapter_classes(original_classes: dict) -> None:
    tier5_service_module._ADAPTER_CLASSES.clear()
    tier5_service_module._ADAPTER_CLASSES.update(original_classes)


def test_accepts_first_valid_candidate():
    factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapters({"fake_good": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.tier == 5
    assert result.provider == "fake_good"
    assert result.translation_provider_confidence == 0.9
    # _normalize_metric_column_references only quotes when needed (reserved
    # word or "$" in the name) — SOMECOLUMN needs neither, so the real,
    # unmodified normalizer legitimately drops the quotes here.
    assert result.sql == 'SUM(sometable.SOMECOLUMN)'


def test_rejects_candidate_referencing_unknown_column_even_though_adapter_succeeded():
    """The mandatory validation gate — this is the whole point of Tier 5's
    consolidation: a plausible-looking response that references a column
    that doesn't exist must be rejected, for every provider, no bypass."""
    factory = lambda settings: _FakeAdapter(settings, response_text='sometable."DOES_NOT_EXIST"', confidence=0.9)
    config, original = _config_with_fake_adapters({"fake_bad_column": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is None


def test_confidence_below_threshold_is_rejected():
    factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.1)
    config, original = _config_with_fake_adapters({"fake_low_confidence": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is None


def test_falls_through_to_next_provider_when_first_is_rejected():
    bad_factory = lambda settings: _FakeAdapter(settings, response_text='sometable."DOES_NOT_EXIST"', confidence=0.9)
    good_factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapters(
        {"fake_bad": bad_factory, "fake_good": good_factory},
        provider_order=["fake_bad", "fake_good"],
    )
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.provider == "fake_good"


def test_adapter_exception_does_not_crash_and_falls_through():
    raising_factory = lambda settings: _FakeAdapter(settings, raises=True)
    good_factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapters(
        {"fake_raises": raising_factory, "fake_good": good_factory},
        provider_order=["fake_raises", "fake_good"],
    )
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.provider == "fake_good"


def test_returns_none_when_no_provider_produces_a_response():
    empty_factory = lambda settings: _FakeAdapter(settings, response_text=None)
    config, original = _config_with_fake_adapters({"fake_empty": empty_factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is None


class _FakeBatchAdapter:
    """Test double for translate_batch() scenarios — returns one response
    text per call, in order, from `responses` (a queue), and records every
    prompt/system_message it was called with so tests can assert call
    count without depending on real provider behavior."""

    def __init__(self, settings, *, responses=None, confidence=0.9):
        self.settings = settings
        self._responses = list(responses or [])
        self._confidence = confidence
        self.calls = []

    def is_available(self) -> bool:
        return True

    def translate(self, prompt: str, system_message: str):
        self.calls.append((prompt, system_message))
        if not self._responses:
            return None
        text = self._responses.pop(0)
        if text is None:
            return None
        return RawResult(text=text, confidence=self._confidence)


def _batch_requests(n, dax_fmt="SUM('SomeTable'[SomeColumn])"):
    return [_request(metric_name=f"Metric_{i}", dax=dax_fmt) for i in range(n)]


def test_translate_batch_uses_one_call_for_multiple_metrics():
    """The real cost/latency point of batching: N metrics needing Tier 5
    must cost 1 provider call, not N."""
    requests = _batch_requests(3)
    import json as _json
    response = _json.dumps({f"m{i}": 'SUM(sometable."SOMECOLUMN")' for i in range(3)})

    fake = _FakeBatchAdapter(None, responses=[response])
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(fake.calls) == 1
    assert len(results) == 3
    assert all(r is not None and r.is_success for r in results)
    assert all(r.provider == "fake_batch" for r in results)


def test_translate_batch_mixed_resolvable_and_unresolvable_returns_correct_partial_results():
    """18/20-style scenario, scaled down: a batch response covering some
    but not all metrics, with one invalid entry, must still return correct
    per-metric successes/failures — never a whole-batch failure."""
    requests = _batch_requests(3)
    import json as _json
    response = _json.dumps({
        "m0": 'SUM(sometable."SOMECOLUMN")',      # valid
        "m1": 'sometable."DOES_NOT_EXIST"',        # parses, but references an unknown column
        # "m2" deliberately omitted — provider didn't answer for this one
    })

    fake = _FakeBatchAdapter(None, responses=[response])
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(results) == 3
    assert results[0] is not None and results[0].is_success
    assert results[1] is None  # per-metric validation still rejects it inside the batch
    assert results[2] is None  # missing key -> no candidate, not an error


def test_translate_batch_per_metric_validation_still_rejects_schema_invalid_result():
    """Explicit, narrow version of the above: batching must never bypass
    _validate_metric_column_references for any individual result."""
    requests = _batch_requests(1)
    import json as _json
    response = _json.dumps({"m0": 'sometable."DOES_NOT_EXIST"'})

    fake = _FakeBatchAdapter(None, responses=[response])
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert results == [None]


def test_translate_batch_malformed_response_falls_through_to_next_provider():
    requests = _batch_requests(2)
    import json as _json
    good_response = _json.dumps({f"m{i}": 'SUM(sometable."SOMECOLUMN")' for i in range(2)})

    bad = _FakeBatchAdapter(None, responses=["this is not json at all"])
    good = _FakeBatchAdapter(None, responses=[good_response])
    config, original = _config_with_fake_adapters(
        {"fake_bad": lambda settings: bad, "fake_good": lambda settings: good},
        provider_order=["fake_bad", "fake_good"],
    )
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(bad.calls) == 1
    assert len(good.calls) == 1
    assert all(r is not None and r.provider == "fake_good" for r in results)


def test_translate_batch_returns_all_none_when_every_provider_is_malformed():
    """Whole-batch clean failure — never a silently-dropped or
    partially-populated list. The caller can retry per-metric or move on."""
    requests = _batch_requests(3)

    bad_one = _FakeBatchAdapter(None, responses=["not json"])
    bad_two = _FakeBatchAdapter(None, responses=["also not json"])
    config, original = _config_with_fake_adapters(
        {"fake_bad_one": lambda settings: bad_one, "fake_bad_two": lambda settings: bad_two},
        provider_order=["fake_bad_one", "fake_bad_two"],
    )
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert results == [None, None, None]


def test_translate_batch_chunks_at_max_batch_size():
    """5 metrics with max_batch_size=2 must produce 3 provider calls
    (2 + 2 + 1), not 1 and not 5."""
    requests = _batch_requests(5)
    import json as _json
    chunk_responses = [
        _json.dumps({"m0": 'SUM(sometable."SOMECOLUMN")', "m1": 'SUM(sometable."SOMECOLUMN")'}),
        _json.dumps({"m0": 'SUM(sometable."SOMECOLUMN")', "m1": 'SUM(sometable."SOMECOLUMN")'}),
        _json.dumps({"m0": 'SUM(sometable."SOMECOLUMN")'}),
    ]

    fake = _FakeBatchAdapter(None, responses=chunk_responses)
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    config.max_batch_size = 2
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(fake.calls) == 3
    assert len(results) == 5
    assert all(r is not None and r.is_success for r in results)


def test_translate_batch_generalizes_to_databricks_dialect():
    """Batching must not assume Snowflake — per-item normalize/validate
    still applies the dialect-aware quote translation
    (tier5/validation.py's _to_snowflake_style_quoting /
    _from_snowflake_style_quoting) inside a batch response exactly like it
    does for a single translate() call."""
    requests = [
        TranslationRequest(
            dax="SUM('SomeTable'[SomeColumn])",
            dataset_name="SomeTable",
            table_alias="sometable",
            dataset_col_lookup=_COL_LOOKUP,
            dataset_aliases=_ALIASES,
            metric_name="Metric_A",
            dialect="databricks",
        )
    ]
    import json as _json
    response = _json.dumps({"m0": "SUM(`sometable`.`SOMECOLUMN`)"})

    fake = _FakeBatchAdapter(None, responses=[response])
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(results) == 1
    assert results[0] is not None and results[0].is_success
    assert "`" not in results[0].sql  # normalized back out of Snowflake-style quoting, no leaked backticks...
    assert '"' not in results[0].sql  # ...and no leaked double-quotes either


def test_translate_batch_empty_input_returns_empty_list_with_no_calls():
    fake = _FakeBatchAdapter(None, responses=["should never be used"])
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch([])
    finally:
        _restore_adapter_classes(original)

    assert results == []
    assert len(fake.calls) == 0


def test_auth_failure_is_cached_and_skipped_for_rest_of_run_without_calling_adapter_again():
    """The regression under test: a provider whose key is invalid/expired
    must be detected once (ProviderAuthError) and then skipped on every
    subsequent call to the *same* Tier5Service instance (i.e. the rest of
    one translation run) — no repeated network round trip to a provider
    that is guaranteed to fail identically again."""
    auth_failing_factory = lambda settings: _FakeAdapter(settings, raises_auth=True)
    good_factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapters(
        {"fake_bad_key": auth_failing_factory, "fake_good": good_factory},
        provider_order=["fake_bad_key", "fake_good"],
    )
    try:
        service = tier5_service_module.Tier5Service(config)
        bad_adapter = service._adapters["fake_bad_key"]
        good_adapter = service._adapters["fake_good"]

        # First call: the bad-key provider is actually attempted once,
        # fails authentication, and the run falls through to the next
        # configured provider.
        result_1 = service.translate(_request(metric_name="Metric_A"))
        assert result_1 is not None and result_1.provider == "fake_good"
        assert bad_adapter.call_count == 1
        assert good_adapter.call_count == 1

        # Second call, same instance/run: the bad-key provider must be
        # skipped without another translate() invocation.
        result_2 = service.translate(_request(metric_name="Metric_B"))
        assert result_2 is not None and result_2.provider == "fake_good"
        assert bad_adapter.call_count == 1  # unchanged — no second attempt
        assert good_adapter.call_count == 2
    finally:
        _restore_adapter_classes(original)


def test_auth_failure_cache_does_not_persist_across_a_new_run():
    """Credentials can be fixed between deploys — a fresh Tier5Service
    (a new run) must not inherit a previous run's "unavailable" marking
    and must try the provider again."""
    auth_failing_factory = lambda settings: _FakeAdapter(settings, raises_auth=True)
    config, original = _config_with_fake_adapters({"fake_bad_key": auth_failing_factory})
    try:
        first_run = tier5_service_module.Tier5Service(config)
        first_run.translate(_request())
        assert first_run._adapters["fake_bad_key"].call_count == 1

        second_run = tier5_service_module.Tier5Service(config)
        second_run.translate(_request())
        assert second_run._adapters["fake_bad_key"].call_count == 1  # tried fresh, not skipped
    finally:
        _restore_adapter_classes(original)


def test_non_auth_exception_is_not_cached_and_is_retried_every_call():
    """Transient failures (rate limits, timeouts, network blips) must keep
    being retried per the existing logic — only a clear authentication
    failure gets the "skip for the rest of this run" treatment."""
    flaky_factory = lambda settings: _FakeAdapter(settings, raises=True)
    config, original = _config_with_fake_adapters({"fake_flaky": flaky_factory})
    try:
        service = tier5_service_module.Tier5Service(config)
        flaky_adapter = service._adapters["fake_flaky"]

        service.translate(_request(metric_name="Metric_A"))
        service.translate(_request(metric_name="Metric_B"))

        assert flaky_adapter.call_count == 2  # retried both times, never cached as unavailable
    finally:
        _restore_adapter_classes(original)


def test_translate_batch_also_caches_auth_failure_across_chunks():
    """translate_batch's provider loop (_translate_one_batch_chunk) must
    apply the same per-run cache as translate() — chunk 2 must not retry a
    provider that failed authentication on chunk 1."""
    import json as _json

    auth_failing = _FakeBatchAdapter(None)
    auth_failing.translate = lambda prompt, system_message: (_ for _ in ()).throw(
        ProviderAuthError("fake_bad_key", RuntimeError("401 invalid_api_key"))
    )
    good_response = _json.dumps({"m0": 'SUM(sometable."SOMECOLUMN")', "m1": 'SUM(sometable."SOMECOLUMN")'})
    good = _FakeBatchAdapter(None, responses=[good_response, good_response])

    config, original = _config_with_fake_adapters(
        {"fake_bad_key": lambda settings: auth_failing, "fake_good": lambda settings: good},
        provider_order=["fake_bad_key", "fake_good"],
    )
    config.max_batch_size = 2
    try:
        service = tier5_service_module.Tier5Service(config)
        results = service.translate_batch(_batch_requests(4))
    finally:
        _restore_adapter_classes(original)

    assert len(good.calls) == 2  # one per chunk — the bad-key provider was never retried
    assert len(results) == 4
    assert all(r is not None and r.provider == "fake_good" for r in results)


def test_dax_divide_lost_its_division_is_enforced():
    """A response that drops the denominator (a known LLM failure mode)
    must be rejected even with high adapter-reported confidence."""
    factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapters({"fake_lost_division": factory})
    try:
        request = _request(dax="DIVIDE([Measure_A],[Measure_B])")
        result = tier5_service_module.Tier5Service(config).translate(request)
    finally:
        _restore_adapter_classes(original)

    assert result is None
