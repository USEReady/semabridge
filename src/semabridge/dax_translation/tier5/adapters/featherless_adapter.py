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
    ProviderAuthError,
    RawResult,
    is_auth_error,
    strip_markdown_fences,
)
from semabridge.dax_translation.tier5.config import ProviderSettings

logger = get_logger(__name__)

_PLACEHOLDER_CONFIDENCE = 0.65  # no real scoring existed for Featherless historically
_FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"


class FeatherlessAdapter:
    name = "featherless"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def is_available(self) -> bool:
        return bool(os.getenv(self.settings.enabled_env))

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        api_key = os.getenv(self.settings.enabled_env)
        if not api_key:
            return None

        models = self.settings.models or ["deepseek-ai/DeepSeek-V4-Pro"]
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
