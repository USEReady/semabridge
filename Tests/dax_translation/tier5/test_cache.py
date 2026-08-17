"""Unit tests for semabridge.dax_translation.tier5.cache and its wiring
into Tier5Service.

See tier5/cache.py's module docstring for the real incident this closes: a
rolling-window metric's Tier-5 translation succeeded in one sync and failed
in a later sync of the identical DAX shape, because every sync re-asked the
LLM fresh with no memory of a past validated success. These tests use only
synthetic placeholder DAX/schema data -- no real project or metric names,
and no live LLM or Snowflake connection is needed to run them.
"""
from __future__ import annotations

from semabridge.dax_translation.types import Dialect, TranslationRequest
from semabridge.dax_translation.tier5.cache import Tier5TranslationCache, cache_key
from semabridge.dax_translation.tier5.config import Tier5Config, ProviderSettings
from semabridge.dax_translation.tier5 import service as tier5_service_module
from semabridge.dax_translation.tier5.adapters.base import RawResult

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


# ---------------------------------------------------------------------------
# cache_key() -- pure content-driven, never metric/project/model name
# ---------------------------------------------------------------------------

def test_cache_key_is_identical_across_different_metric_and_dataset_names():
    """The whole point: two requests for entirely different metrics (and
    even different dataset_name fields, which the key deliberately ignores)
    in different models must hash identically as long as the DAX text,
    dialect, and physical schema shape (table_alias/dataset_aliases/
    dataset_col_lookup) match -- proving the key generalizes across
    projects instead of being scoped to one."""
    req_a = _request(metric_name="Metric_A", dataset_name="ModelA_Table")
    req_b = _request(metric_name="Totally Different Metric", dataset_name="ModelB_Table")
    assert cache_key(req_a) == cache_key(req_b)


def test_cache_key_differs_when_dax_text_differs():
    req_a = _request(dax="SUM('SomeTable'[SomeColumn])")
    req_b = _request(dax="AVG('SomeTable'[SomeColumn])")
    assert cache_key(req_a) != cache_key(req_b)


def test_cache_key_ignores_incidental_whitespace_differences_in_dax():
    req_a = _request(dax="SUM('SomeTable'[SomeColumn])")
    req_b = _request(dax="  SUM('SomeTable'[SomeColumn])  \n")
    assert cache_key(req_a) == cache_key(req_b)


def test_cache_key_differs_by_dialect():
    req_snowflake = _request(dialect=Dialect.SNOWFLAKE)
    req_databricks = _request(dialect=Dialect.DATABRICKS)
    assert cache_key(req_snowflake) != cache_key(req_databricks)


def test_cache_key_differs_when_schema_shape_differs():
    """A different physical column set for the same DAX text must NOT
    collide -- reusing a cached translation across a genuinely different
    schema would bake in wrong identifiers."""
    req_a = _request(dataset_col_lookup={"SomeTable": {"SOMECOLUMN"}})
    req_b = _request(dataset_col_lookup={"SomeTable": {"SOMECOLUMN", "OTHERCOLUMN"}})
    assert cache_key(req_a) != cache_key(req_b)


def test_cache_key_differs_when_table_alias_differs():
    req_a = _request(table_alias="sometable")
    req_b = _request(table_alias="othertable")
    assert cache_key(req_a) != cache_key(req_b)


# ---------------------------------------------------------------------------
# Tier5TranslationCache -- in-memory and disk round-trip
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, sql, provider="fake_provider", confidence=0.9, self_reported=None):
        self.sql = sql
        self.provider = provider
        self.translation_provider_confidence = confidence
        self.llm_self_reported_confidence = self_reported


def test_in_memory_cache_get_returns_none_before_any_put():
    cache = Tier5TranslationCache()
    assert cache.get(_request()) is None


def test_in_memory_cache_put_then_get_round_trips():
    cache = Tier5TranslationCache()
    request = _request()
    cache.put(request, _FakeResult('SUM(sometable."SOMECOLUMN")'))
    entry = cache.get(request)
    assert entry is not None
    assert entry["sql"] == 'SUM(sometable."SOMECOLUMN")'
    assert entry["provider"] == "fake_provider"


def test_disk_backed_cache_persists_across_separate_instances(tmp_path):
    """The actual cross-run persistence contract: a SECOND, freshly
    constructed Tier5TranslationCache pointed at the same file sees an
    entry a prior instance wrote -- simulating a later sync run picking up
    a previous run's validated translation."""
    cache_file = tmp_path / "tier5_cache_test.jsonl"
    request = _request()

    first_instance = Tier5TranslationCache(cache_file=cache_file)
    first_instance.put(request, _FakeResult('SUM(sometable."SOMECOLUMN")', provider="anthropic"))

    second_instance = Tier5TranslationCache(cache_file=cache_file)
    entry = second_instance.get(request)
    assert entry is not None
    assert entry["sql"] == 'SUM(sometable."SOMECOLUMN")'
    assert entry["provider"] == "anthropic"


def test_cache_with_no_file_given_never_touches_disk(tmp_path, monkeypatch):
    """The default Tier5Service()-level cache (no explicit cache_file) must
    be pure in-memory -- no disk read/write at all -- so every existing
    test that constructs a fresh Tier5Service() per test case stays fully
    isolated with zero shared state."""
    monkeypatch.chdir(tmp_path)
    cache = Tier5TranslationCache()
    cache.put(_request(), _FakeResult('SUM(sometable."SOMECOLUMN")'))
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere


# ---------------------------------------------------------------------------
# Tier5Service integration: cache consulted before any LLM call, cache miss
# falls through normally, and a stale/invalid cached entry is never trusted.
# ---------------------------------------------------------------------------

class _FakeAdapter:
    def __init__(self, settings, *, response_text=None, confidence=0.9):
        self.settings = settings
        self._response_text = response_text
        self._confidence = confidence
        self.call_count = 0

    def is_available(self) -> bool:
        return True

    def translate(self, prompt, system_message, max_tokens=None):
        self.call_count += 1
        if self._response_text is None:
            return None
        return RawResult(text=self._response_text, confidence=self._confidence)


def _config_with_fake_adapter(adapter):
    import os
    providers = {"fake_provider": ProviderSettings(enabled_env="ALWAYS_ON_FOR_TEST")}
    config = Tier5Config(provider_order=["fake_provider"], min_confidence=0.55, providers=providers)
    os.environ["ALWAYS_ON_FOR_TEST"] = "1"
    original = dict(tier5_service_module._ADAPTER_CLASSES)
    tier5_service_module._ADAPTER_CLASSES["fake_provider"] = lambda settings: adapter
    return config, original


def _restore(original):
    tier5_service_module._ADAPTER_CLASSES.clear()
    tier5_service_module._ADAPTER_CLASSES.update(original)


def test_a_second_service_with_same_shared_cache_reuses_result_without_calling_adapter_again():
    """(a) An identical DAX+dialect(+schema) key on a second call -- even
    from a brand-new Tier5Service instance, simulating a separate sync run
    -- reuses the cached, validated result instead of making a new LLM
    call."""
    shared_cache = Tier5TranslationCache()
    adapter = _FakeAdapter(None, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapter(adapter)
    try:
        first_run = tier5_service_module.Tier5Service(config, cache=shared_cache)
        result_1 = first_run.translate(_request(metric_name="Metric_A"))
        assert result_1 is not None and result_1.provider == "fake_provider"
        assert adapter.call_count == 1

        # A brand-new Tier5Service -- a different "run" -- sharing only the
        # cache, asked to translate a DIFFERENT metric_name with the exact
        # same DAX+schema shape.
        second_run = tier5_service_module.Tier5Service(config, cache=shared_cache)
        result_2 = second_run.translate(_request(metric_name="Metric_B"))
    finally:
        _restore(original)

    assert result_2 is not None
    assert result_2.sql == result_1.sql
    assert adapter.call_count == 1, "cache hit must not trigger a second LLM call"
    assert "cache" in " ".join(result_2.validation_notes).lower()


def test_cache_miss_falls_through_to_a_real_llm_call_exactly_as_today():
    """(b) A request with no matching cache entry must behave exactly like
    the pre-cache code path: the adapter is called and its validated result
    is returned."""
    shared_cache = Tier5TranslationCache()
    adapter = _FakeAdapter(None, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapter(adapter)
    try:
        service = tier5_service_module.Tier5Service(config, cache=shared_cache)
        result = service.translate(_request())
    finally:
        _restore(original)

    assert result is not None
    assert result.provider == "fake_provider"
    assert adapter.call_count == 1


def test_stale_cache_entry_that_fails_revalidation_is_not_trusted():
    """(c) An entry already sitting in the cache (as if written by a past
    run, before a validation rule existed or was tightened) that is now a
    nested-aggregate shape -- exactly the real incident detect_nested_aggregate
    closes -- must NOT be blindly returned. It's treated as a miss: the
    live adapter is still called, and its fresh, valid result both answers
    this call and overwrites the stale entry."""
    shared_cache = Tier5TranslationCache()
    request = _request()
    # Seed the cache directly, bypassing put()'s normal validated-write
    # path, to simulate a pre-existing stale/invalid entry.
    shared_cache.put(request, _FakeResult('SUM(CASE WHEN MAX(sometable."SOMECOLUMN") > 1 THEN 1 ELSE 0 END)'))

    adapter = _FakeAdapter(None, response_text='SUM(sometable."SOMECOLUMN")', confidence=0.9)
    config, original = _config_with_fake_adapter(adapter)
    try:
        service = tier5_service_module.Tier5Service(config, cache=shared_cache)
        result = service.translate(request)
    finally:
        _restore(original)

    assert result is not None
    assert adapter.call_count == 1, "stale/invalid cache entry must not short-circuit the live call"
    assert "MAX" not in result.sql
    assert result.sql == 'SUM(sometable.SOMECOLUMN)'

    # The stale entry is now corrected in place for the next reader.
    refreshed = shared_cache.get(request)
    assert refreshed["sql"] == result.sql


def test_translate_batch_also_consults_cache_per_request_before_any_provider_call():
    """The batch path must apply the identical cache-first behavior --
    a pre-cached metric is resolved with zero provider calls even when
    batched alongside genuinely new metrics."""
    shared_cache = Tier5TranslationCache()
    cached_request = _request(metric_name="Cached_Metric", dax="SUM('SomeTable'[SomeColumn])")
    shared_cache.put(cached_request, _FakeResult('SUM(sometable."SOMECOLUMN")'))

    import json as _json
    new_request = _request(metric_name="New_Metric", dax="AVG('SomeTable'[SomeColumn])")
    batch_response = _json.dumps({"m0": 'AVG(sometable."SOMECOLUMN")'})

    class _FakeBatchAdapter:
        def __init__(self, settings):
            self.calls = 0

        def is_available(self):
            return True

        def translate(self, prompt, system_message, max_tokens=None):
            self.calls += 1
            return RawResult(text=batch_response, confidence=0.9)

    import os
    providers = {"fake_provider": ProviderSettings(enabled_env="ALWAYS_ON_FOR_TEST")}
    config = Tier5Config(provider_order=["fake_provider"], min_confidence=0.55, providers=providers)
    os.environ["ALWAYS_ON_FOR_TEST"] = "1"
    original = dict(tier5_service_module._ADAPTER_CLASSES)
    fake_adapter_holder = {}
    tier5_service_module._ADAPTER_CLASSES["fake_provider"] = lambda settings: fake_adapter_holder.setdefault(
        "adapter", _FakeBatchAdapter(settings)
    )
    try:
        service = tier5_service_module.Tier5Service(config, cache=shared_cache)
        results = service.translate_batch([cached_request, new_request])
    finally:
        _restore(original)

    assert len(results) == 2
    assert results[0] is not None and results[0].sql == 'SUM(sometable.SOMECOLUMN)'
    assert results[1] is not None and results[1].sql == 'AVG(sometable.SOMECOLUMN)'
    assert fake_adapter_holder["adapter"].calls == 1  # only the uncached metric triggered a provider call
