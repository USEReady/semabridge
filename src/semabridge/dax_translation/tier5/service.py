"""Tier 5 orchestrator — validation is mandatory, no bypass, for every
provider, every dialect.

This is the key behavioral change from every existing LLM call site: today,
only Pipeline B's MetricExpressionTranslator runs real schema-existence
validation (_validate_metric_column_references); Pipeline A
(converter/dax_translator.py) and Pipeline C (databricks_publisher.py) only
ever check response shape. Here, the semantic validator runs for every
candidate from every adapter — no call site can opt out.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from semabridge.utils.logger import get_logger
from semabridge.dax_translation.types import TranslationRequest, TranslationResult
from semabridge.dax_translation.tier5.config import Tier5Config, ProviderSettings
from semabridge.dax_translation.tier5.prompt import (
    build_prompt,
    build_system_message,
    build_batch_prompt,
    build_batch_system_message,
    batch_key,
    parse_batch_payload,
)
from semabridge.dax_translation.tier5.validation import (
    fix_common_llm_issues,
    _is_scalar_metric_sql,
    _dax_divide_lost_its_division,
    normalize_metric_column_references,
    validate_metric_column_references,
)
from semabridge.dax_translation.tier5.adapters.base import ProviderAdapter
from semabridge.dax_translation.tier5.adapters.openai_adapter import OpenAIAdapter
from semabridge.dax_translation.tier5.adapters.gemini_adapter import GeminiAdapter
from semabridge.dax_translation.tier5.adapters.groq_adapter import GroqAdapter
from semabridge.dax_translation.tier5.adapters.featherless_adapter import FeatherlessAdapter

logger = get_logger(__name__)

_ADAPTER_CLASSES = {
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "groq": GroqAdapter,
    "featherless": FeatherlessAdapter,
    # "anthropic" deliberately omitted — deferred, no adapter built yet.
}


def _build_adapters(config: Tier5Config) -> Dict[str, ProviderAdapter]:
    adapters: Dict[str, ProviderAdapter] = {}
    for name, settings in config.providers.items():
        adapter_cls = _ADAPTER_CLASSES.get(name)
        if adapter_cls is None:
            logger.warning("Tier5Service: no adapter class registered for provider %r", name)
            continue
        adapters[name] = adapter_cls(settings)
    return adapters


class Tier5Service:
    def __init__(self, config: Optional[Tier5Config] = None) -> None:
        self.config = config or Tier5Config.default()
        self._adapters = _build_adapters(self.config)

    def translate(self, request: TranslationRequest) -> Optional[TranslationResult]:
        prompt = build_prompt(request)
        system_message = build_system_message(request.dialect)
        validation_notes: List[str] = []

        for provider_name in self.config.enabled_provider_order():
            adapter = self._adapters.get(provider_name)
            if adapter is None or not adapter.is_available():
                continue

            try:
                raw = adapter.translate(prompt, system_message)
            except Exception as exc:
                logger.warning("Tier5Service: provider %r raised, treating as unavailable: %s", provider_name, exc)
                continue

            if raw is None:
                continue

            if raw.confidence < self.config.min_confidence:
                validation_notes.append(
                    f"{provider_name}: confidence {raw.confidence:.2f} below min_confidence "
                    f"{self.config.min_confidence:.2f}"
                )
                continue

            repaired = fix_common_llm_issues(raw.text, request.dax)

            if _dax_divide_lost_its_division(request.dax, repaired):
                validation_notes.append(f"{provider_name}: DIVIDE() in DAX but no '/' in SQL — rejected")
                continue

            if not _is_scalar_metric_sql(repaired, dialect=request.dialect):
                validation_notes.append(f"{provider_name}: response is not a safe scalar metric SQL shape — rejected")
                continue

            normalized = normalize_metric_column_references(repaired, request)

            ok, err = validate_metric_column_references(normalized, request)
            if not ok:
                validation_notes.append(f"{provider_name}: {err}")
                continue

            return TranslationResult(
                sql=normalized,
                tier=5,
                original_dax=request.dax,
                provider=provider_name,
                translation_provider_confidence=raw.confidence,
                validation_notes=validation_notes,
            )

        return None

    def translate_batch(self, requests: List[TranslationRequest]) -> List[Optional[TranslationResult]]:
        """Batched form of translate(): one prompt, one provider call, one
        JSON map of metric-key -> SQL for up to config.max_batch_size
        requests, instead of one API call per metric.

        Every individual candidate still runs through the exact same
        repair/validation pipeline as translate() (see
        _validate_one_batch_candidate) — batching only changes how many
        provider calls are made, never how a result is judged. Reuses the
        existing ProviderAdapter.translate(prompt, system_message) contract
        unchanged, so this works for all four configured providers with no
        adapter-level changes.

        Returns a list positionally aligned with `requests`. An entry is
        None if that metric could not be resolved — either this provider's
        response didn't include/validate a usable SQL candidate for it, or
        every configured provider's response for its chunk was unusable.
        Never raises and never silently drops an entry: len(result) ==
        len(requests) always, so the caller can tell exactly which metrics
        still need a per-metric translate() retry or should be given up on.
        """
        if not requests:
            return []

        max_batch_size = max(1, int(getattr(self.config, "max_batch_size", 20) or 20))
        results: List[Optional[TranslationResult]] = []
        for start in range(0, len(requests), max_batch_size):
            chunk = requests[start:start + max_batch_size]
            results.extend(self._translate_one_batch_chunk(chunk))
        return results

    def _translate_one_batch_chunk(
        self, requests: List[TranslationRequest]
    ) -> List[Optional[TranslationResult]]:
        prompt = build_batch_prompt(requests)
        system_message = build_batch_system_message(requests[0].dialect)
        expected_keys = {batch_key(i) for i in range(len(requests))}

        for provider_name in self.config.enabled_provider_order():
            adapter = self._adapters.get(provider_name)
            if adapter is None or not adapter.is_available():
                continue

            try:
                raw = adapter.translate(prompt, system_message)
            except Exception as exc:
                logger.warning(
                    "Tier5Service.translate_batch: provider %r raised, treating as unavailable: %s",
                    provider_name, exc,
                )
                continue

            if raw is None:
                continue
            if raw.confidence < self.config.min_confidence:
                continue

            parsed = parse_batch_payload(raw.text)
            # Not valid JSON at all, or a JSON value that shares no key
            # with this chunk, means the provider didn't follow the batch
            # contract — a genuine malformed-response signal, distinct
            # from "some individual metrics didn't validate". Move to the
            # next provider for the whole chunk rather than accepting a
            # response that isn't answering the question asked.
            if parsed is None or not (expected_keys & set(parsed)):
                logger.warning(
                    "Tier5Service.translate_batch: provider %r returned an unparseable "
                    "or unrecognizable batch response for %d metrics — trying next provider",
                    provider_name, len(requests),
                )
                continue

            return [
                self._validate_one_batch_candidate(
                    req, parsed.get(batch_key(i)), provider_name, raw.confidence
                )
                for i, req in enumerate(requests)
            ]

        return [None] * len(requests)

    @staticmethod
    def _validate_one_batch_candidate(
        request: TranslationRequest,
        raw_sql: Optional[str],
        provider_name: str,
        confidence: float,
    ) -> Optional[TranslationResult]:
        """Applies the identical per-item checks translate() applies to a
        single-item response — never a weaker pass just because the
        candidate arrived inside a batch response."""
        if not raw_sql:
            return None

        repaired = fix_common_llm_issues(raw_sql, request.dax)
        if _dax_divide_lost_its_division(request.dax, repaired):
            return None
        if not _is_scalar_metric_sql(repaired, dialect=request.dialect):
            return None

        normalized = normalize_metric_column_references(repaired, request)
        ok, _err = validate_metric_column_references(normalized, request)
        if not ok:
            return None

        return TranslationResult(
            sql=normalized,
            tier=5,
            original_dax=request.dax,
            provider=provider_name,
            translation_provider_confidence=confidence,
        )
