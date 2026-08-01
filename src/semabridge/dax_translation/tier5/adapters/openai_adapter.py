"""OpenAI provider adapter.

Based on connectors/translator.py's OpenAI leg (_try_openai_dax_translation,
:612-696) — the most mature error handling among the OpenAI legs (Pipeline
A's, Pipeline B's, and databricks_publisher.py's are near-duplicates of
each other). The prompt is now built once, centrally, by tier5/prompt.py —
this adapter only sends whatever prompt/system_message it's given; it does
not build its own prompt like the original did.

Shape/semantic validation is NOT done here — that happens centrally in
tier5/service.py via tier5/validation.py, applied uniformly to every
provider's output. This adapter only strips markdown fences (response
hygiene, not a validation decision) and reports a response.

No real confidence-scoring exists for OpenAI in any of the original call
sites — this adapter reports a fixed placeholder confidence, honestly
lower than a real score would be, rather than inventing a heuristic that
was never there.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from semabridge.utils.logger import get_logger
from semabridge.dax_translation.tier5.adapters.base import RawResult, strip_markdown_fences
from semabridge.dax_translation.tier5.config import ProviderSettings

logger = get_logger(__name__)

_PLACEHOLDER_CONFIDENCE = 0.7  # no real scoring existed for OpenAI historically


class OpenAIAdapter:
    name = "openai"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def is_available(self) -> bool:
        return bool(os.getenv(self.settings.enabled_env))

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        api_key = os.getenv(self.settings.enabled_env)
        if not api_key:
            return None

        try:
            from openai import OpenAI
        except Exception as exc:
            logger.warning("OpenAI adapter: openai package unavailable: %s", exc)
            return None

        model_name = self.settings.model or "gpt-4o-mini"
        timeout = float(self.settings.timeout_seconds or 30)
        max_retries = int(self.settings.max_retries or 2)

        sql: Optional[str] = None
        for attempt in range(1, max_retries + 1):
            try:
                client = OpenAI(api_key=api_key, organization=os.getenv("OPENAI_ORGANIZATION") or None)
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=float(os.getenv("OPENAI_DAX_TEMPERATURE", "0.1")),
                    max_tokens=int(os.getenv("OPENAI_DAX_MAX_TOKENS", "500")),
                    timeout=timeout,
                )
                sql = (response.choices[0].message.content or "").strip()
                break
            except Exception as exc:
                logger.warning("OpenAI adapter attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 10))
                else:
                    return None

        sql = strip_markdown_fences(sql)
        if not sql:
            return None
        return RawResult(text=sql, confidence=_PLACEHOLDER_CONFIDENCE)
