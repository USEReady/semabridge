"""Central Tier 5 provider-order config — one place instead of five
hardcoded orders (Pipeline A: OpenAI→Gemini; Pipeline B: rule→OpenAI-cache→
Featherless→multi-model→OpenAI→Gemini; Pipeline C: OpenAI→Gemini).

No existing pipeline reads this yet (Step 1 is additive-only). This module
defines the shape and a sensible default; a YAML file can be layered on
top later via ProviderConfig.load(path=...) without changing callers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ProviderSettings:
    enabled_env: str
    model: Optional[str] = None
    timeout_seconds: Optional[float] = None
    max_retries: Optional[int] = None
    rate_limit_rpm: Optional[int] = None
    models: Optional[List[str]] = None  # ordered failover list (featherless)
    # Resolved, in-memory only — decrypted Settings-page API key for this
    # provider, if one is configured there (see
    # repository/llm_provider_credentials.py and Tier5Config.resolve()
    # below). Deliberately NOT written to os.environ: settings_api.py's
    # save_secret() already documents why mutating process-global
    # os.environ from a stored credential is unsafe for concurrent
    # requests; threading the key through this field instead means every
    # adapter's is_available()/translate() just prefers it over
    # os.getenv(enabled_env), with no global mutation at all. Also
    # deliberately excluded from repr (field(repr=False)) — a plain
    # dataclass repr would otherwise print the decrypted key into any log
    # line or traceback that stringifies a ProviderSettings/Tier5Config.
    api_key: Optional[str] = field(default=None, repr=False)

    def is_enabled(self) -> bool:
        return bool(self.api_key) or bool(os.getenv(self.enabled_env))


@dataclass
class Tier5Config:
    provider_order: List[str]
    min_confidence: float
    providers: Dict[str, ProviderSettings]
    skills_dir_env: str = "SEMABRIDGE_LLM_SKILLS_DIR"
    # Matches the chunk size Pipeline B's original OpenAI batch-prefetch
    # used (20 metrics per JSON-map call) — restored as a general Tier 5
    # batching limit, not tied to any one provider or pipeline.
    max_batch_size: int = 20

    def enabled_provider_order(self) -> List[str]:
        """provider_order filtered to enabled providers, with Settings-
        configured providers tried before .env-only providers.

        A provider is "Settings-configured" when its `api_key` field is
        set — i.e. Tier5Config.resolve() found a key saved via the
        Settings UI (see repository/llm_provider_credentials.py's
        apply_settings_overrides()). A provider is ".env-only" when it's
        enabled solely because os.getenv(enabled_env) is set, with no
        Settings/DB key on top.

        Without this split, a fixed provider_order (openai, gemini, groq,
        featherless, anthropic) would try an earlier, .env-only provider
        before a later provider the user explicitly configured via
        Settings — e.g. a stale/placeholder .env OPENAI_API_KEY would be
        tried before a Settings-saved Anthropic key, even though the user
        who went to the trouble of configuring Anthropic via the UI
        reasonably expects it to be tried first. provider_order still
        applies as the tie-breaker *within* each source tier — it just no
        longer decides priority *across* the two tiers.
        """
        enabled = [
            name for name in self.provider_order
            if name in self.providers and self.providers[name].is_enabled()
        ]
        settings_configured = [name for name in enabled if self.providers[name].api_key]
        env_only = [name for name in enabled if not self.providers[name].api_key]
        return settings_configured + env_only

    @classmethod
    def default(cls) -> "Tier5Config":
        return cls(
            provider_order=["openai", "gemini", "groq", "featherless", "anthropic"],
            min_confidence=0.55,
            providers={
                "openai": ProviderSettings(
                    enabled_env="OPENAI_API_KEY",
                    model="gpt-4o-mini",
                    timeout_seconds=30,
                    max_retries=2,
                ),
                "gemini": ProviderSettings(
                    enabled_env="GEMINI_API_KEY",
                    model="gemini-1.5-flash",
                    rate_limit_rpm=5,
                ),
                "groq": ProviderSettings(
                    enabled_env="GROQ_API_KEY",
                    model="llama-3.3-70b-versatile",
                ),
                "featherless": ProviderSettings(
                    enabled_env="FEATHERLESS_API_KEY",
                    models=[
                        "deepseek-ai/DeepSeek-V4-Pro",
                        "Qwen/Qwen3.6-27B",
                        "mistralai/Mistral-7B-Instruct-v0.3",
                        "meta-llama/Llama-3.2-3B-Instruct",
                    ],
                ),
                "anthropic": ProviderSettings(
                    enabled_env="ANTHROPIC_API_KEY",
                    # model intentionally left unset — AnthropicAdapter
                    # performs live model discovery via the Models API to
                    # pick a cost-appropriate default (see
                    # adapters/anthropic_adapter.py). Set this explicitly
                    # to pin a model and skip discovery.
                    timeout_seconds=30,
                    max_retries=2,
                ),
            },
        )

    @classmethod
    def resolve(cls) -> "Tier5Config":
        """Build the config for one run: start from default() (provider
        order, timeouts, per-provider hardcoded fallbacks), then overlay
        any Settings-page-configured provider credentials/models on top.

        This is the ONLY place Settings-stored LLM provider config is
        read from the database, and it happens exactly once per
        Tier5Service instance (called from Tier5Service.__init__).
        Tier5Service itself is already constructed exactly once per
        run/deploy by every real call site (DAXTranslator,
        DatabricksPublisher, DaxTranslationService — each caches it as a
        lazy instance attribute via its own `_get_tier5_service()`/
        `__init__`, the fix from the Groq-batch-spam bug). So this DB
        read happens once per run, not once per metric, with no separate
        cache of its own needed here.

        Deliberately NOT wrapped in @lru_cache(): unlike
        auth/encryption.py's Fernet key (genuinely immutable for the
        process lifetime, which is why @lru_cache() is correct there),
        Settings-configured provider keys/models CAN change between runs
        — an admin edits them via the Settings UI while a long-lived
        server process keeps running — and a process-wide cache would
        keep serving stale config until a restart. Fresh resolution on
        every new Tier5Service() is what makes "a Settings change takes
        effect on the very next run" correct; caching would silently
        reintroduce the same staleness class the encryption-warning fix
        exists to describe (just at the config layer instead of a Fernet
        object).

        Failure to read Settings config (DB unavailable, etc.) degrades
        to default()'s .env-only behavior rather than breaking Tier 5
        entirely — Settings config is additive, .env must keep working
        standalone.
        """
        config = cls.default()
        try:
            from semabridge.repository.llm_provider_credentials import apply_settings_overrides
            apply_settings_overrides(config)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Tier5Config.resolve(): failed to read Settings-configured provider "
                "credentials, falling back to .env-only config for this run: %s", exc,
            )
        return config

    @classmethod
    def from_dict(cls, data: dict) -> "Tier5Config":
        tier5 = (data.get("dax_translation") or {}).get("tier5") or {}
        providers = {
            name: ProviderSettings(**cfg)
            for name, cfg in (tier5.get("providers") or {}).items()
        }
        return cls(
            provider_order=list(tier5.get("provider_order") or []),
            min_confidence=float(tier5.get("min_confidence", 0.55)),
            providers=providers,
            skills_dir_env=(data.get("dax_translation") or {}).get(
                "skills_dir_env", "SEMABRIDGE_LLM_SKILLS_DIR"
            ),
            max_batch_size=int(tier5.get("max_batch_size", 20)),
        )

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Tier5Config":
        """Load from a YAML file matching the documented shape, falling
        back to default() if no path is given or the file doesn't exist."""
        if not path:
            return cls.default()
        try:
            import yaml
            from pathlib import Path

            p = Path(path)
            if not p.exists():
                return cls.default()
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return cls.from_dict(data)
        except Exception:
            return cls.default()
