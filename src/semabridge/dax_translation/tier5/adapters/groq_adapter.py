"""Groq provider adapter.

Based on multi_model_translator.py's _translate_with_groq leg — tries
LangChain's ChatGroq first, falls back to a direct OpenAI-compatible
client pointed at Groq's endpoint. That file's hardcoded model default
(mixtral-8x7b-32768) is replaced by the central config's
llama-3.3-70b-versatile — this is exactly the point of centralizing
provider config instead of leaving it hardcoded per call site.

No real confidence-scoring existed for Groq in the original — same
placeholder-confidence rationale as the OpenAI adapter.
"""
from __future__ import annotations

import os
from typing import Optional

from semabridge.utils.logger import get_logger
from semabridge.dax_translation.tier5.adapters.base import RawResult, strip_markdown_fences
from semabridge.dax_translation.tier5.config import ProviderSettings

logger = get_logger(__name__)

_PLACEHOLDER_CONFIDENCE = 0.7  # no real scoring existed for Groq historically


class GroqAdapter:
    name = "groq"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def is_available(self) -> bool:
        return bool(os.getenv(self.settings.enabled_env))

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        api_key = os.getenv(self.settings.enabled_env)
        if not api_key:
            return None

        model_name = self.settings.model or "llama-3.3-70b-versatile"

        # 1. LangChain ChatGroq
        try:
            from langchain_groq import ChatGroq

            client = ChatGroq(
                api_key=api_key,
                model=model_name,
                temperature=0.1,
                max_tokens=500,
                timeout=float(self.settings.timeout_seconds or 30),
            )
            response = client.invoke([("system", system_message), ("user", prompt)])
            sql = strip_markdown_fences(response.content)
            if sql:
                return RawResult(text=sql, confidence=_PLACEHOLDER_CONFIDENCE)
        except Exception as exc:
            logger.warning("Groq adapter (ChatGroq) failed: %s", exc)

        # 2. Direct OpenAI-compatible client fallback
        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=500,
                timeout=float(self.settings.timeout_seconds or 30),
            )
            sql = strip_markdown_fences(response.choices[0].message.content)
            if sql:
                return RawResult(text=sql, confidence=_PLACEHOLDER_CONFIDENCE)
        except Exception as exc:
            logger.warning("Groq adapter (direct client) failed: %s", exc)

        return None
