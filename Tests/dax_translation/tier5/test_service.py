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

    def translate(self, prompt: str, system_message: str, max_tokens=None):
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


def test_settings_configured_anthropic_is_tried_before_env_only_openai(monkeypatch):
    """Real-provider-name regression for the Part 2 dispatch-order fix:
    openai is only enabled via a (possibly stale/placeholder) .env key and
    sits earlier in the real default provider_order; anthropic is enabled
    via a Settings-configured api_key (as Tier5Config.resolve() would set
    it after apply_settings_overrides() reads a real Settings-page save)
    and sits later. anthropic must be tried first, and openai's adapter
    must never be called at all.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-dotenv-placeholder")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    openai_adapter = _FakeAdapter(None, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    anthropic_adapter = _FakeAdapter(None, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)

    config = Tier5Config(
        provider_order=["openai", "gemini", "groq", "featherless", "anthropic"],
        min_confidence=0.55,
        providers={
            "openai": ProviderSettings(enabled_env="OPENAI_API_KEY"),
            "anthropic": ProviderSettings(enabled_env="ANTHROPIC_API_KEY", api_key="sk-ant-from-settings"),
        },
    )
    original = dict(tier5_service_module._ADAPTER_CLASSES)
    tier5_service_module._ADAPTER_CLASSES.update({
        "openai": lambda settings: openai_adapter,
        "anthropic": lambda settings: anthropic_adapter,
    })
    try:
        # Sanity-check the scenario itself before trusting the assertion below.
        assert config.provider_order.index("openai") < config.provider_order.index("anthropic")
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.provider == "anthropic"
    assert anthropic_adapter.call_count == 1
    assert openai_adapter.call_count == 0, (
        "openai must never be called — anthropic (Settings-configured) must be "
        "tried and accepted before openai (.env-only) is even reached"
    )


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

    def translate(self, prompt: str, system_message: str, max_tokens=None):
        self.calls.append((prompt, system_message, max_tokens))
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


def test_translate_batch_recovers_partial_results_from_truncated_mid_object_response():
    """Real, live-confirmed incident this test guards against: a 13-metric
    batch got cut off mid-response by a flat output-token cap, and the
    WHOLE chunk was discarded even though most of it was perfectly good
    JSON (see prompt.py's _salvage_partial_batch_json). The complete
    entry(ies) before the cutoff must now be accepted directly from the
    first provider -- NOT by falling through to a second provider, since
    real, validated SQL was already recovered. The truncated metric
    resolves to None for the caller to retry individually (matching the
    real incident's actual downstream behavior: connectors/translator.py's
    per-metric fallback recovering exactly the metrics a partial batch
    couldn't)."""
    requests = _batch_requests(2)
    truncated = _FakeBatchAdapter(
        None, responses=['{"m0": "SUM(sometable.SOMECOLUMN)", "m1": "AVG(CASE WHEN x > 1 THEN']
    )
    good = _FakeBatchAdapter(None, responses=["should never be called"])
    config, original = _config_with_fake_adapters(
        {"fake_truncated": lambda settings: truncated, "fake_good": lambda settings: good},
        provider_order=["fake_truncated", "fake_good"],
    )
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(truncated.calls) == 1
    assert len(good.calls) == 0  # never tried -- a usable partial result was already accepted
    assert results[0] is not None and results[0].is_success and results[0].provider == "fake_truncated"
    assert results[1] is None  # truncated mid-value -- correctly not recovered


def test_translate_batch_recovers_fully_despite_trailing_comma():
    """A provider that (incorrectly) trails every dict with a comma, as if
    it were emitting a Python literal rather than strict JSON -- a
    harmless malformation with nothing actually missing. Discarding a
    fully-recoverable batch over one stray comma would be exactly the
    over-eager rejection this fix closes."""
    requests = _batch_requests(2)
    trailing_comma = _FakeBatchAdapter(
        None, responses=['{"m0": "SUM(sometable.SOMECOLUMN)", "m1": "AVG(sometable.SOMECOLUMN)",}']
    )
    good = _FakeBatchAdapter(None, responses=["should never be called"])
    config, original = _config_with_fake_adapters(
        {"fake_trailing_comma": lambda settings: trailing_comma, "fake_good": lambda settings: good},
        provider_order=["fake_trailing_comma", "fake_good"],
    )
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(trailing_comma.calls) == 1
    assert len(good.calls) == 0  # never tried -- both entries were fully recoverable
    assert all(r is not None and r.is_success and r.provider == "fake_trailing_comma" for r in results)


def test_translate_batch_unescaped_quote_salvage_produces_garbage_that_per_metric_validation_still_rejects():
    """A provider that generates SQL containing a literal `"` inside a
    string comparison without escaping it breaks the enclosing JSON at
    that exact point -- unlike a token-cap truncation (cutoff at the END),
    this is an internal corruption, so the salvaged value for the
    corrupted key is itself garbage (see
    test_prompt.py's ..._unescaped_quote_salvages_only_the_corrupted_first_entry).
    This is the safety net that matters: garbage syntactically-unbalanced
    SQL still goes through the SAME per-metric validation pipeline as any
    other candidate and gets rejected there, exactly like a fully rejected
    batch would have -- it is never silently accepted as a real
    translation just because parse_batch_payload could technically
    extract *something*."""
    requests = _batch_requests(2)
    bad_quote = _FakeBatchAdapter(
        None, responses=['{"m0": "SUM(CASE WHEN x="bad" THEN 1 END)", "m1": "AVG(y)"}']
    )
    good = _FakeBatchAdapter(None, responses=["should never be called"])
    config, original = _config_with_fake_adapters(
        {"fake_bad_quote": lambda settings: bad_quote, "fake_good": lambda settings: good},
        provider_order=["fake_bad_quote", "fake_good"],
    )
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert len(bad_quote.calls) == 1
    # The corrupted "m0" candidate ("SUM(CASE WHEN x=" -- unbalanced
    # parens) fails _is_scalar_metric_sql/validation like any other bad
    # candidate; "m1" was never salvaged at all. Both resolve to None.
    assert results == [None, None]
    # Not falling through to a second provider is still correct here: the
    # chunk-level decision (accept vs. try next provider) is made on
    # whether ANY expected key was recoverable at all, not on whether the
    # per-metric validation later accepts it -- same principle as a batch
    # response that parses perfectly but references an unknown column.
    assert len(good.calls) == 0


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
        # skipped without another translate() invocation. Uses a DIFFERENT
        # dax than the first call so this is a genuine second translation
        # attempt, not a Tier5TranslationCache hit on the first call's
        # result -- metric_name alone is deliberately NOT part of the cache
        # key (see tier5/cache.py), so two requests differing only in
        # metric_name would otherwise be indistinguishable to the cache.
        result_2 = service.translate(_request(metric_name="Metric_B", dax="AVG('SomeTable'[SomeColumn])"))
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


def test_null_cast_placeholder_response_is_rejected_not_accepted_as_success():
    """Every Tier 5 prompt instructs providers to return
    CAST(NULL AS DOUBLE) when a pattern is impossible to translate. That
    sentinel must never be accepted as a successful candidate -- it has to
    be rejected exactly like any other invalid response, so the caller
    falls through to 'no translation' (and, one level up, records a
    DropLedger entry) instead of emitting a dead metric silently."""
    factory = lambda settings: _FakeAdapter(settings, response_text="CAST(NULL AS DOUBLE)", confidence=0.9)
    config, original = _config_with_fake_adapters({"fake_gives_up": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is None


def test_null_cast_placeholder_falls_through_to_next_provider():
    """The rejection must fall through the provider order, same as any
    other invalid candidate -- a second provider with a real answer still
    wins."""
    giveup_factory = lambda settings: _FakeAdapter(settings, response_text="CAST(NULL AS DOUBLE)", confidence=0.9)
    good_factory = lambda settings: _FakeAdapter(settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapters(
        {"fake_gives_up": giveup_factory, "fake_good": good_factory},
        provider_order=["fake_gives_up", "fake_good"],
    )
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.provider == "fake_good"


def test_null_cast_placeholder_with_type_variation_and_synonyms_is_still_rejected():
    """The guard is a shape check, not a literal-string match against
    'DOUBLE' specifically -- any target type, and a trailing WITH SYNONYMS
    clause, must still be recognized as the placeholder."""
    factory = lambda settings: _FakeAdapter(
        settings, response_text="CAST( NULL AS DECIMAL(38,10) )  WITH SYNONYMS = ('Foo')", confidence=0.9
    )
    config, original = _config_with_fake_adapters({"fake_gives_up": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is None


def test_real_sql_containing_null_keyword_is_not_falsely_rejected():
    """No false positives: real SQL that legitimately mentions NULL (e.g.
    an IS NULL / NULLIF guard) is a completely different shape from the
    bare CAST(NULL AS <type>) placeholder and must still be accepted."""
    factory = lambda settings: _FakeAdapter(
        settings,
        response_text='NULLIF(sometable."SOMECOLUMN", 0)',
        confidence=0.9,
    )
    config, original = _config_with_fake_adapters({"fake_good": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    # _normalize_metric_column_references only quotes when needed (reserved
    # word or "$" in the name) -- SOMECOLUMN needs neither, so the real,
    # unmodified normalizer legitimately drops the quotes here (same
    # behavior documented in test_accepts_first_valid_candidate above).
    assert result.sql == 'NULLIF(sometable.SOMECOLUMN, 0)'


def test_translate_batch_rejects_null_cast_placeholder_per_metric():
    """_validate_one_batch_candidate must apply the identical guard as
    translate() -- a batch response entry that is the NULL-cast placeholder
    resolves to None for that metric, not a false 'success'."""
    requests = _batch_requests(2)
    import json as _json
    response = _json.dumps({
        "m0": 'SUM(sometable."SOMECOLUMN")',
        "m1": "CAST(NULL AS DOUBLE)",
    })

    fake = _FakeBatchAdapter(None, responses=[response])
    factory = lambda settings: fake
    config, original = _config_with_fake_adapters({"fake_batch": factory})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(requests)
    finally:
        _restore_adapter_classes(original)

    assert results[0] is not None and results[0].is_success
    assert results[1] is None


# ---------------------------------------------------------------------------
# Self-reported confidence: parsed from the structured {"sql":...,
# "confidence":...} response, stored as llm_self_reported_confidence --
# strictly separate from translation_provider_confidence (raw.confidence),
# which continues to drive the existing min_confidence accept/reject gate
# completely unchanged.
# ---------------------------------------------------------------------------

def test_llm_self_reported_confidence_is_parsed_from_structured_response():
    factory = lambda settings: _FakeAdapter(
        settings,
        response_text='{"sql": "SUM(sometable.\\"SOMECOLUMN\\")", "confidence": 0.82}',
        confidence=0.9,  # adapter-level (translation_provider_confidence) -- deliberately different number
    )
    config, original = _config_with_fake_adapters({"fake_structured": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.translation_provider_confidence == 0.9  # unchanged adapter-level gate value
    assert result.llm_self_reported_confidence == 0.82  # the new, separate, self-reported value


def test_llm_self_reported_confidence_is_none_when_provider_ignores_the_json_contract():
    """Backward-compatible case: a provider returns bare SQL instead of
    the requested JSON object. Translation must still succeed;
    llm_self_reported_confidence is simply None, never guessed."""
    factory = lambda settings: _FakeAdapter(
        settings, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9
    )
    config, original = _config_with_fake_adapters({"fake_bare_sql": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.llm_self_reported_confidence is None


def test_llm_self_reported_confidence_does_not_affect_the_accept_reject_gate():
    """A LOW self-reported confidence inside the structured response must
    NOT be used for the min_confidence accept/reject gate -- that gate
    reads only raw.confidence (the adapter-level value), which is high
    here. The translation must be accepted."""
    factory = lambda settings: _FakeAdapter(
        settings,
        response_text='{"sql": "SUM(sometable.\\"SOMECOLUMN\\")", "confidence": 0.01}',
        confidence=0.9,  # adapter-level -- above min_confidence, so this must be accepted
    )
    config, original = _config_with_fake_adapters({"fake_low_self_reported": factory})
    try:
        result = tier5_service_module.Tier5Service(config).translate(_request())
    finally:
        _restore_adapter_classes(original)

    assert result is not None
    assert result.llm_self_reported_confidence == 0.01


def test_batch_llm_self_reported_confidence_is_parsed_per_metric():
    import json as _json
    response = _json.dumps({
        "m0": {"sql": 'SUM(sometable."SOMECOLUMN")', "confidence": 0.7},
        "m1": {"sql": 'AVG(sometable."SOMECOLUMN")', "confidence": 0.3},
    })
    fake = _FakeBatchAdapter(None, responses=[response])
    config, original = _config_with_fake_adapters({"fake_batch": lambda settings: fake})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(_batch_requests(2))
    finally:
        _restore_adapter_classes(original)

    assert results[0] is not None and results[0].llm_self_reported_confidence == 0.7
    assert results[1] is not None and results[1].llm_self_reported_confidence == 0.3


def test_batch_llm_self_reported_confidence_is_none_for_bare_string_values():
    """Mixed batch: one key follows the new JSON-per-key contract, one key
    is a bare string (provider partially ignored the format). Both must
    still resolve to correct SQL; only the bare-string one has
    llm_self_reported_confidence=None."""
    import json as _json
    response = _json.dumps({
        "m0": {"sql": 'SUM(sometable."SOMECOLUMN")', "confidence": 0.6},
        "m1": 'AVG(sometable."SOMECOLUMN")',
    })
    fake = _FakeBatchAdapter(None, responses=[response])
    config, original = _config_with_fake_adapters({"fake_batch": lambda settings: fake})
    try:
        results = tier5_service_module.Tier5Service(config).translate_batch(_batch_requests(2))
    finally:
        _restore_adapter_classes(original)

    assert results[0] is not None and results[0].llm_self_reported_confidence == 0.6
    assert results[1] is not None and results[1].llm_self_reported_confidence is None
