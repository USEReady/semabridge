"""Gemini provider adapter.

Wraps converter/gemini_api_service.py's GeminiAPIService.call() /
call_gemini() transport with no material rewrite — it is already a
proper, centralized, rate-limited (5 RPM) provider transport with retry
and backoff, unlike the other providers' scattered implementations.

Deliberately does NOT call GeminiDAXTranslator.translate() (the higher-
level convenience wrapper in gemini_dax_translator.py): that method builds
its own prompt internally, which would bypass the unified tier5/prompt.py
template this consolidation exists to introduce. Only the transport layer
is reused.

_score_confidence is salvaged verbatim from GeminiDAXTranslator
(gemini_dax_translator.py:585-607) — it's a stateless, provider-agnostic
heuristic (checks output SQL shape + input DAX length, nothing
Gemini-specific), so it's copied here as a free function rather than
requiring the whole class.
"""
from __future__ import annotations

import re
from typing import Optional

from semabridge.utils.logger import get_logger
from semabridge.dax_translation.tier5.adapters.base import RawResult, strip_markdown_fences
from semabridge.dax_translation.tier5.config import ProviderSettings

logger = get_logger(__name__)


def score_confidence(sql: str, original_dax: str) -> float:
    """Verbatim salvage of GeminiDAXTranslator._score_confidence
    (gemini_dax_translator.py:585-607)."""
    confidence = 0.5  # Start with base confidence

    # Increase confidence for known good patterns
    if 'SUM(' in sql.upper():
        confidence += 0.1
    if 'AVG(' in sql.upper() or 'AVERAGE(' in sql.upper():
        confidence += 0.1
    if 'COUNT(' in sql.upper():
        confidence += 0.05

    # Penalize SELECT statements (invalid for METRICS clause)
    if 'SELECT' in sql.upper() or 'FROM' in sql.upper() or 'WHERE' in sql.upper():
        confidence -= 0.3
        logger.warning("Gemini adapter: translation contains SELECT/FROM/WHERE - will likely fail validation")

    # Heuristic: longer DAX may be more complex = lower confidence
    if len(original_dax) > 200:
        confidence -= 0.1

    return max(0.0, min(confidence, 1.0))


_DAX_MARKER_RE = re.compile(r"DAX:\n(.*?)(?:\n\n|\n═|$)", re.DOTALL)


def _extract_dax_for_confidence(prompt: str) -> str:
    """Best-effort extraction of the bare DAX text from the unified prompt
    (tier5/prompt.py always includes a "DAX:\\n{dax}" section), so
    score_confidence's DAX-length heuristic reflects the actual expression
    rather than the whole prompt (schema + rules + few-shot text)."""
    match = _DAX_MARKER_RE.search(prompt)
    return match.group(1).strip() if match else prompt


class GeminiAdapter:
    name = "gemini"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings

    def is_available(self) -> bool:
        import os
        return bool(os.getenv(self.settings.enabled_env))

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        if not self.is_available():
            return None

        from semabridge.converter.gemini_api_service import get_gemini_service

        service = get_gemini_service()
        if not service.is_available:
            return None

        def _fallback(_prompt: str) -> None:
            return None

        try:
            full_prompt = f"{system_message}\n\n{prompt}" if system_message else prompt
            response = service.call(full_prompt, fallback_fn=_fallback)
        except Exception as exc:
            logger.warning("Gemini adapter call failed: %s", exc)
            return None

        if not isinstance(response, str) or not response.strip():
            return None

        sql = strip_markdown_fences(response)
        if not sql:
            return None

        confidence = score_confidence(sql, _extract_dax_for_confidence(prompt))
        return RawResult(text=sql, confidence=confidence)
