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

    def is_enabled(self) -> bool:
        return bool(os.getenv(self.enabled_env))


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
        """provider_order filtered to providers whose enabled_env is set."""
        return [
            name for name in self.provider_order
            if name in self.providers and self.providers[name].is_enabled()
        ]

    @classmethod
    def default(cls) -> "Tier5Config":
        return cls(
            provider_order=["openai", "gemini", "groq", "featherless"],
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
                # Anthropic deliberately omitted — deferred per Step 1 scope
                # (no key, no adapter built yet; add both together later).
            },
        )

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
