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

from typing import Any, Dict, List, Optional, Set

from semabridge.utils.logger import get_logger
from semabridge.utils.null_sentinel import is_null_cast_sql
from semabridge.dax_translation.types import TranslationRequest, TranslationResult
from semabridge.dax_translation.tier5.config import Tier5Config, ProviderSettings
from semabridge.dax_translation.tier5.prompt import (
    build_prompt,
    build_system_message,
    build_batch_prompt,
    build_batch_system_message,
    batch_key,
    parse_batch_payload,
    parse_structured_response,
    _extract_sql_and_confidence,
)
from semabridge.dax_translation.tier5.validation import (
    fix_common_llm_issues,
    _is_scalar_metric_sql,
    _dax_divide_lost_its_division,
    normalize_metric_column_references,
    validate_metric_column_references,
)
from semabridge.dax_translation.tier5.adapters.base import ProviderAdapter, ProviderAuthError
from semabridge.dax_translation.tier5.adapters.openai_adapter import OpenAIAdapter
from semabridge.dax_translation.tier5.adapters.gemini_adapter import GeminiAdapter
from semabridge.dax_translation.tier5.adapters.groq_adapter import GroqAdapter
from semabridge.dax_translation.tier5.adapters.featherless_adapter import FeatherlessAdapter
from semabridge.dax_translation.tier5.adapters.anthropic_adapter import AnthropicAdapter

logger = get_logger(__name__)

_ADAPTER_CLASSES = {
    "openai": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "groq": GroqAdapter,
    "featherless": FeatherlessAdapter,
    "anthropic": AnthropicAdapter,
}

# Real incident: a 13-metric batch under every non-Gemini adapter's flat
# single-metric 500-token default got truncated mid-JSON-response,
# producing "unparseable batch response" and discarding the WHOLE chunk
# (the confidence-JSON contract made each entry noticeably bigger than a
# bare SQL string, which is why a batch size that used to just barely fit
# no longer does). PER_METRIC/OVERHEAD are deliberately generous -- a
# nested multi-branch CASE WHEN expression plus its JSON wrapper can run
# a few hundred tokens -- and the ceiling stays well inside what every
# configured provider's chat-completion API accepts.
_BATCH_TOKENS_PER_METRIC = 150
_BATCH_TOKENS_OVERHEAD = 200
_BATCH_TOKENS_CEILING = 4096


def _batch_max_tokens(metric_count: int) -> int:
    return min(_BATCH_TOKENS_CEILING, _BATCH_TOKENS_OVERHEAD + _BATCH_TOKENS_PER_METRIC * max(1, metric_count))


# Closes the "no raw LLM call logging" gap for this specific failure path:
# the real incident this session investigated had no raw response text
# anywhere in the logs, only the post-hoc "unparseable" warning -- making
# root-causing it after the fact impossible. Truncated (not omitted) so a
# large response doesn't flood the log, but long enough to see the actual
# JSON shape and where it broke.
def _truncate_for_log(text: Optional[str], limit: int = 4000) -> str:
    if not text:
        return "<empty>"
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, {len(text)} chars total]"


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
        # Tier5Config.resolve() (not .default()) so Settings-page-
        # configured provider keys/models take effect. This read happens
        # exactly once here, at construction — see resolve()'s docstring
        # for why that's "once per run" given how Tier5Service itself is
        # already cached at every real call site.
        self.config = config or Tier5Config.resolve()
        self._adapters = _build_adapters(self.config)
        # Providers that have raised a ProviderAuthError (bad/expired key)
        # during this instance's lifetime. Scoped to this instance, not
        # process-wide: since every real call site holds one Tier5Service
        # for the duration of one translation run (see call sites' own
        # lazy-instance caching) and constructs a fresh one for the next
        # run, this cache is exactly "for the rest of the current run" and
        # naturally clears once credentials are fixed and a new run starts.
        self._unavailable_providers: Set[str] = set()

    def translate(self, request: TranslationRequest) -> Optional[TranslationResult]:
        prompt = build_prompt(request)
        system_message = build_system_message(request.dialect)
        validation_notes: List[str] = []

        for provider_name in self.config.enabled_provider_order():
            if provider_name in self._unavailable_providers:
                continue

            adapter = self._adapters.get(provider_name)
            if adapter is None or not adapter.is_available():
                continue

            try:
                raw = adapter.translate(prompt, system_message)
            except ProviderAuthError as exc:
                logger.warning(
                    "Tier5Service: provider %r failed authentication, skipping for the rest of this run: %s",
                    provider_name, exc,
                )
                self._unavailable_providers.add(provider_name)
                continue
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

            # raw.confidence above is the adapter-level accept/reject gate
            # (placeholder for most providers) -- unchanged. The provider's
            # OWN self-reported estimate, requested via the structured
            # {"sql":..., "confidence":...} response contract
            # (tier5/prompt.py), is parsed here and carried through
            # separately as llm_self_reported_confidence: display-only,
            # never used in this gate or any other decision below.
            parsed_sql, llm_self_reported_confidence = parse_structured_response(raw.text)
            if not parsed_sql:
                validation_notes.append(f"{provider_name}: no SQL found in structured response — rejected")
                continue

            repaired = fix_common_llm_issues(parsed_sql, request.dax)

            if is_null_cast_sql(repaired):
                validation_notes.append(
                    f"{provider_name}: returned a NULL-cast placeholder (declined to translate) — rejected"
                )
                continue

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
                llm_self_reported_confidence=llm_self_reported_confidence,
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
            if provider_name in self._unavailable_providers:
                continue

            adapter = self._adapters.get(provider_name)
            if adapter is None or not adapter.is_available():
                continue

            try:
                raw = adapter.translate(
                    prompt, system_message, max_tokens=_batch_max_tokens(len(requests))
                )
            except ProviderAuthError as exc:
                logger.warning(
                    "Tier5Service.translate_batch: provider %r failed authentication, "
                    "skipping for the rest of this run: %s",
                    provider_name, exc,
                )
                self._unavailable_providers.add(provider_name)
                continue
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
            #
            # parse_batch_payload() itself now salvages whatever complete
            # top-level entries it can from a response that doesn't parse
            # as a whole (see its docstring) -- a real incident showed a
            # 13-metric batch getting silently truncated mid-response and
            # discarding every one of the 13 metrics, even though most of
            # the response before the cutoff was perfectly good JSON.
            # `parsed` below can therefore be a non-empty PARTIAL dict
            # even when the raw text didn't fully parse; only `None` (or a
            # dict sharing zero keys with this chunk) means truly nothing
            # was recoverable.
            if parsed is None or not (expected_keys & set(parsed)):
                logger.warning(
                    "Tier5Service.translate_batch: provider %r returned an unparseable "
                    "or unrecognizable batch response for %d metrics — trying next "
                    "provider. Raw response (truncated to 4000 chars): %s",
                    provider_name, len(requests), _truncate_for_log(raw.text),
                )
                continue
            if len(expected_keys & set(parsed)) < len(expected_keys):
                logger.warning(
                    "Tier5Service.translate_batch: provider %r returned a PARTIAL "
                    "batch response -- recovered %d/%d metrics (likely truncated "
                    "mid-response under this provider's output-token cap). Using "
                    "what was recovered instead of discarding the whole batch; the "
                    "missing metrics will be retried individually by the caller. "
                    "Raw response (truncated to 4000 chars): %s",
                    provider_name, len(expected_keys & set(parsed)), len(expected_keys),
                    _truncate_for_log(raw.text),
                )

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
        raw_value: Any,
        provider_name: str,
        confidence: float,
    ) -> Optional[TranslationResult]:
        """Applies the identical per-item checks translate() applies to a
        single-item response — never a weaker pass just because the
        candidate arrived inside a batch response.

        raw_value is whatever parse_batch_payload found at this metric's
        key -- either a {"sql":..., "confidence":...} dict (the requested
        per-key contract) or a bare SQL string (a provider that ignored
        it, tolerated the same way parse_structured_response tolerates it
        for the single-item path). _extract_sql_and_confidence handles
        both shapes uniformly.

        `confidence` here is the whole-response adapter-level value
        (translation_provider_confidence, already used by the caller's
        min_confidence gate) -- distinct from the per-item
        llm_self_reported_confidence extracted below, which is
        display-only and never gates anything.
        """
        sql, llm_self_reported_confidence = _extract_sql_and_confidence(raw_value)
        if not sql:
            return None

        repaired = fix_common_llm_issues(sql, request.dax)
        if is_null_cast_sql(repaired):
            return None
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
            llm_self_reported_confidence=llm_self_reported_confidence,
        )
