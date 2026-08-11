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
from semabridge.dax_translation.tier5.adapters.base import (
    ModelDiscoveryResult,
    ProviderAuthError,
    RawResult,
    is_auth_error,
    strip_markdown_fences,
)
from semabridge.dax_translation.tier5.config import ProviderSettings

logger = get_logger(__name__)


def list_available_models(api_key: str):
    """Real model discovery for the Settings-page "Discover Models"
    button. Uses google.generativeai directly (the same package
    gemini_api_service.py calls, not the newer google-genai package) for
    consistency with how this adapter actually talks to Gemini. Filtered
    to models whose supported_generation_methods includes
    "generateContent" -- unlike OpenAI, this IS a real, documented
    capability field on Gemini's model objects, so filtering on it is a
    fact, not a guess."""
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    ids = sorted(
        m.name.split("/", 1)[-1]
        for m in genai.list_models()
        if "generateContent" in getattr(m, "supported_generation_methods", [])
    )
    return ModelDiscoveryResult(models=ids, total_available=len(ids))


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
        return bool(self.settings.api_key) or bool(os.getenv(self.settings.enabled_env))

    def translate(
        self, prompt: str, system_message: str, max_tokens: Optional[int] = None
    ) -> Optional[RawResult]:
        if not self.is_available():
            return None

        full_prompt = f"{system_message}\n\n{prompt}" if system_message else prompt
        text: Optional[str]

        # Accepted for ProviderAdapter protocol conformance (see its
        # docstring) but not threaded into either call path below: neither
        # this adapter's Settings-key path nor its shared-singleton path
        # sets an explicit max_tokens/max_output_tokens today, so both
        # already default to the SDK's own generous cap (thousands of
        # tokens for gemini-1.5-flash) -- unlike the other four adapters'
        # flat 500-token default, Gemini was never exposed to the real
        # incident this parameter exists to fix.
        if self.settings.api_key:
            # A Settings-configured key bypasses the shared
            # get_gemini_service() singleton entirely, for two reasons:
            # (1) that singleton reads GEMINI_API_KEY from os.environ at
            # its own construction time and is a lazy process-lifetime
            # singleton (get_gemini_service() in gemini_api_service.py) --
            # it has no way to take a per-adapter key at all, and (2) its
            # 5 RPM rate limiter is calibrated to the free-tier quota
            # assumed for the .env-configured key; a Settings-configured
            # key could be on a different (paid) plan with a completely
            # different quota, so inheriting that hardcoded limiter would
            # be actively wrong, not just redundant.
            try:
                import google.generativeai as genai

                genai.configure(api_key=self.settings.api_key)
                model_name = self.settings.model or "gemini-1.5-flash"
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(full_prompt, stream=False)
                text = response.text if response else None
            except Exception as exc:
                if is_auth_error(exc):
                    raise ProviderAuthError("gemini", exc) from exc
                logger.warning("Gemini adapter (Settings key) call failed: %s", exc)
                return None
        else:
            from semabridge.converter.gemini_api_service import get_gemini_service

            service = get_gemini_service()
            if not service.is_available:
                return None

            try:
                # No fallback_fn here (unlike the original call site):
                # passing one makes GeminiAPIService.call() swallow every
                # failure and silently return the fallback value, which
                # would hide an auth-class error from the classification
                # below. Letting it raise GeminiAPIError/
                # GeminiRateLimitError instead has the same net effect for
                # this adapter (still returns None on any ordinary
                # failure), it just does so via the except branch.
                text = service.call(full_prompt, fallback_fn=None)
            except Exception as exc:
                if is_auth_error(exc):
                    raise ProviderAuthError("gemini", exc) from exc
                logger.warning("Gemini adapter call failed: %s", exc)
                return None

        if not isinstance(text, str) or not text.strip():
            return None

        sql = strip_markdown_fences(text)
        if not sql:
            return None

        confidence = score_confidence(sql, _extract_dax_for_confidence(prompt))
        return RawResult(text=sql, confidence=confidence)
