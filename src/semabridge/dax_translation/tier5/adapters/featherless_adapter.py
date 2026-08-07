"""Featherless provider adapter.

Based on featherless_translator.py's SECOND definition (:111-155) — the
live LangChain ChatOpenAI implementation with a 5-model failover list.
The FIRST definition in that file (:17-67, a direct requests.post HTTP
call) is dead: Python's top-to-bottom execution means the second
definition silently shadows and replaces the first in the module
namespace, so every real caller has only ever gotten this one. It is
deliberately NOT salvaged here, per the migration design.

No real confidence-scoring existed for Featherless in the original —
same placeholder-confidence rationale as the OpenAI/Groq adapters.
"""
from __future__ import annotations

import os
from typing import Optional

from semabridge.utils.logger import get_logger
from semabridge.dax_translation.tier5.adapters.base import (
    ModelDiscoveryResult,
    ProviderAuthError,
    RawResult,
    is_auth_error,
    strip_markdown_fences,
)
from semabridge.dax_translation.tier5.config import ProviderSettings

logger = get_logger(__name__)

_PLACEHOLDER_CONFIDENCE = 0.65  # no real scoring existed for Featherless historically
_FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"

# Blocklist, not a whitelist, and deliberately so: Featherless's whole
# product is serving open LLMs for text generation, so its catalog is
# presumptively mostly chat/instruct models already. Trying to whitelist
# "instruct"/"chat" naming conventions instead would be unreliable — HF
# model IDs aren't consistent about it (e.g. a base model with no
# "-Instruct" suffix at all can still be perfectly chat-usable). Excluding
# known non-text-generation model families is the safer default.
_NON_CHAT_ID_MARKERS = (
    "embed", "embedding", "whisper", "tts", "clip", "vae",
    "stable-diffusion", "diffusion", "flux", "rerank", "vision",
    "bark", "musicgen", "wav2vec", "audio",
)

# Featherless's real catalog is reportedly large (thousands of
# community-hosted models) with no reliable per-model capability metadata
# beyond the ID string itself. A plain unfiltered dropdown of that size is
# unusable, so the discovered list is capped here -- server-side, not
# left for the frontend to deal with -- at a size a dropdown can actually
# render. The true post-filter count and whether truncation happened are
# returned alongside (ModelDiscoveryResult.total_available/.truncated) so
# the caller can tell the admin "showing 200 of N, refine your search"
# instead of silently hiding the rest. Pairs with a frontend search box in
# the model dropdown so an admin can still reach a model outside the
# first 200 alphabetically.
_MAX_DISCOVERED_MODELS = 200


def list_available_models(api_key: str) -> ModelDiscoveryResult:
    """Real model discovery for the Settings-page "Discover Models"
    button. See _NON_CHAT_ID_MARKERS / _MAX_DISCOVERED_MODELS above for
    the filtering and capping rationale."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=_FEATHERLESS_BASE_URL)
    all_ids = [m.id for m in client.models.list()]
    filtered = sorted(
        model_id for model_id in all_ids
        if not any(marker in model_id.lower() for marker in _NON_CHAT_ID_MARKERS)
    )
    total = len(filtered)
    return ModelDiscoveryResult(
        models=filtered[:_MAX_DISCOVERED_MODELS],
        truncated=total > _MAX_DISCOVERED_MODELS,
        total_available=total,
    )


class FeatherlessAdapter:
    name = "featherless"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def is_available(self) -> bool:
        return bool(self.settings.api_key or os.getenv(self.settings.enabled_env))

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        api_key = self.settings.api_key or os.getenv(self.settings.enabled_env)
        if not api_key:
            return None

        # A Settings-selected model (singular) means the admin picked one
        # specific model via Discover Models -- honor that exactly, no
        # failover list. Only fall back to the hardcoded multi-model
        # failover chain (the historical, .env-only behavior) when no
        # Settings model has been chosen.
        models = [self.settings.model] if self.settings.model else (self.settings.models or ["deepseek-ai/DeepSeek-V4-Pro"])
        timeout = float(self.settings.timeout_seconds or 30)

        try:
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            logger.warning("Featherless adapter: langchain_openai unavailable: %s", exc)
            return None

        for model in models:
            try:
                llm = ChatOpenAI(
                    api_key=api_key,
                    model=model,
                    base_url=_FEATHERLESS_BASE_URL,
                    temperature=0.1,
                    max_tokens=500,
                    timeout=timeout,
                )
                response = llm.invoke([("system", system_message), ("user", prompt)])
                sql = strip_markdown_fences(response.content)
                if sql:
                    return RawResult(text=sql, confidence=_PLACEHOLDER_CONFIDENCE)
                logger.warning("Featherless (%s) returned empty/invalid SQL", model)
            except Exception as exc:
                if is_auth_error(exc):
                    # Same api_key for every model in the failover list —
                    # a rejected key fails the remaining models identically,
                    # so there's no point trying them.
                    logger.warning(
                        "Featherless (%s) auth failure, skipping remaining models in failover list: %s",
                        model, exc,
                    )
                    raise ProviderAuthError("featherless", exc) from exc
                logger.warning("Featherless (%s) failed: %s", model, exc)

        return None
