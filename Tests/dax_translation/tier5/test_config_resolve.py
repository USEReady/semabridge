"""Regression tests for Tier5Config.resolve() and the once-per-run
caching it depends on.

This is the same bug class as the two "fresh instance, lost cache" bugs
already fixed this session: the Groq-batch-spam fix (test coverage:
Tests/test_dax_translator_dead_provider_cache.py — a fresh DAXTranslator()
per metric meant a fresh, empty Tier5Service._unavailable_providers cache
per metric) and the encryption-warning-spam fix (auth/encryption.py's
_get_fernet(), @lru_cache()'d so the dev-mode warning logs once per
process instead of once per encrypt_token()/decrypt_token() call).

Tier5Config.resolve() is the new place a similar mistake could recur: if
something caused it to be re-invoked once per metric instead of once per
Tier5Service (i.e. once per run), every translate()/translate_batch() call
would re-read Settings-configured provider credentials from the database,
exactly mirroring the Groq bug's shape one layer up. There is deliberately
NO separate cache primitive inside resolve() itself (see its docstring for
why a process-wide @lru_cache() would be the WRONG fix here — Settings
config can legitimately change between runs) — instead, "once per run" is
provided by Tier5Service.__init__ being the only call site, and
Tier5Service itself already being constructed exactly once per run at
every real call site (DAXTranslator, DatabricksPublisher,
DaxTranslationService). These tests prove that chain holds.
"""
from __future__ import annotations

from semabridge.dax_translation.tier5.config import Tier5Config
from semabridge.dax_translation.tier5.service import Tier5Service
from semabridge.dax_translation.types import TranslationRequest

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


def _delenv_all_providers(monkeypatch, config: Tier5Config) -> None:
    """No real provider must be reachable during these tests — they
    exercise the resolution/caching contract, not real network calls."""
    for settings in config.providers.values():
        monkeypatch.delenv(settings.enabled_env, raising=False)


def test_resolve_calls_apply_settings_overrides_exactly_once_per_invocation(monkeypatch):
    """Tier5Config.resolve() itself: one call in, one Settings-DB read
    out — the base case, before even involving Tier5Service."""
    calls = []
    monkeypatch.setattr(
        "semabridge.repository.llm_provider_credentials.apply_settings_overrides",
        lambda config: calls.append(config),
    )

    Tier5Config.resolve()

    assert len(calls) == 1


def test_resolve_falls_back_to_default_when_settings_read_fails(monkeypatch):
    """A DB-unavailable failure while reading Settings config must not
    break Tier 5 entirely — .env-only config (default()) must still come
    back, matching every other defensive fallback in this module
    (e.g. Tier5Config.load()'s except-return-default())."""
    def _boom(config):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        "semabridge.repository.llm_provider_credentials.apply_settings_overrides",
        _boom,
    )

    config = Tier5Config.resolve()

    assert config.provider_order == Tier5Config.default().provider_order
    assert config.providers["openai"].api_key is None


def test_tier5_service_resolves_settings_config_exactly_once_across_many_translate_calls(monkeypatch):
    """The actual regression under test, one layer up: constructing ONE
    Tier5Service (config=None, so it goes through Tier5Config.resolve())
    and calling .translate() many times must read Settings-configured
    provider credentials exactly ONCE — at construction — never once per
    metric. Mirrors test_dax_translator_dead_provider_cache.py's
    "instances_created == 1" proof, at the config-resolution layer."""
    calls = []
    monkeypatch.setattr(
        "semabridge.repository.llm_provider_credentials.apply_settings_overrides",
        lambda config: calls.append(config),
    )
    _delenv_all_providers(monkeypatch, Tier5Config.default())

    service = Tier5Service()  # config=None -> Tier5Config.resolve()

    service.translate(_request(metric_name="Metric_A"))
    service.translate(_request(metric_name="Metric_B"))
    service.translate(_request(metric_name="Metric_C"))
    service.translate_batch([_request(metric_name="Metric_D"), _request(metric_name="Metric_E")])

    assert len(calls) == 1, (
        "Settings-configured provider credentials must be resolved once per "
        "Tier5Service (once per run), not once per translate()/translate_batch() call"
    )


def test_a_new_tier5_service_instance_resolves_settings_again_fresh(monkeypatch):
    """The other half of the guarantee, mirroring
    test_auth_failure_cache_does_not_persist_across_a_new_run in
    test_service.py: a NEW run (a fresh Tier5Service()) must NOT inherit
    a previous run's resolved config — otherwise an admin's Settings
    change would never take effect without a full process restart, the
    exact staleness class @lru_cache() would have reintroduced if used
    here instead of per-run resolution."""
    calls = []
    monkeypatch.setattr(
        "semabridge.repository.llm_provider_credentials.apply_settings_overrides",
        lambda config: calls.append(config),
    )
    _delenv_all_providers(monkeypatch, Tier5Config.default())

    Tier5Service()
    Tier5Service()
    Tier5Service()

    assert len(calls) == 3, "each new Tier5Service() run must re-resolve Settings config fresh"


def test_explicit_config_argument_bypasses_resolve_entirely(monkeypatch):
    """Tests (and any future caller) that pass config= explicitly must
    never touch the Settings DB at all — resolve() is only reached via
    the config=None default path."""
    calls = []
    monkeypatch.setattr(
        "semabridge.repository.llm_provider_credentials.apply_settings_overrides",
        lambda config: calls.append(config),
    )

    Tier5Service(Tier5Config.default())

    assert calls == []
