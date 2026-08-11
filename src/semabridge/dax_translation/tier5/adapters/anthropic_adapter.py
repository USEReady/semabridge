"""Anthropic (Claude) provider adapter.

Unlike the other four Tier 5 adapters, there is no existing Anthropic call
site anywhere in this codebase to salvage from -- every previous LLM
integration in semabridge is OpenAI/Gemini/Groq/Featherless only. This
adapter is new code, not a migration.

Shape/semantic validation is NOT done here -- same contract as every other
Tier 5 adapter: tier5/service.py runs the identical fix_common_llm_issues /
_validate_metric_column_references pipeline (tier5/validation.py) against
this adapter's output, unmodified. This adapter only strips markdown
fences (response hygiene) and reports a response; no adapter-specific
validation branch exists anywhere in tier5/service.py, tier5/prompt.py, or
tier5/validation.py for this provider or any other.

Model selection: Anthropic's Models API (GET /v1/models) does not expose
pricing or a cost/capability tier in its response -- confirmed against the
documented model-object schema (id, display_name, created_at,
max_input_tokens, max_tokens, capabilities; no price field). Picking a
"cheapest and best" default therefore can't be derived from the API
response alone. This adapter still does *real* discovery -- it calls
client.models.list() to find out which models this key can actually use --
and then ranks only those confirmed-available models against
_KNOWN_MODEL_CATALOG below.

================================================================================
_KNOWN_MODEL_CATALOG IS A MANUALLY MAINTAINED FALLBACK, NOT A LIVE SOURCE.
================================================================================
It is a small, hand-authored {model_id: price} table -- nothing here
verifies it against Anthropic's actual current pricing. It WILL go stale as
Anthropic ships new models or reprices existing ones, silently, with no
error of any kind unless a run happens to exercise the warning path below.
Snapshot basis: the claude-api skill's cached shared/models.md pricing
table (cached 2026-06-24) / platform.claude.com/docs/en/pricing. Re-check
it there when models are added, retired, or repriced.

This is not just a code-comment warning: _resolve_model logs a WARNING
(not DEBUG) every time live discovery returns a model ID this table
doesn't recognize -- whether or not a known/cheaper model was also found --
naming the unrecognized IDs and what was selected instead. That is the
actual maintenance signal: if this table is stale, a real run against a
real API key will say so in the logs, not just leave a comment for someone
to stumble on later.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

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

_PLACEHOLDER_CONFIDENCE = 0.7  # no real scoring exists for Anthropic; same rationale as openai/groq adapters


@dataclass(frozen=True)
class _ModelCatalogEntry:
    """One row of the manually-maintained fallback price table (see module
    docstring). Prices are USD per million tokens; generation_rank is used
    only to break ties between models the table prices identically (higher
    = newer/more capable), never as the primary sort key -- price always
    wins first."""

    input_price_per_mtok: float
    output_price_per_mtok: float
    generation_rank: int
    thinking_on_by_default: bool = False


# MANUALLY MAINTAINED -- see the module docstring above, which explains why
# this can't be sourced from the live API and how staleness is surfaced.
# Deliberately excludes the premium reasoning tier (Claude Fable 5 / Claude
# Mythos 5, ~$10/$50 per MTok) -- this adapter exists to pick a cost-
# appropriate default for a deterministic SQL-generation task, not the most
# capable model available.
_KNOWN_MODEL_CATALOG: Dict[str, _ModelCatalogEntry] = {
    "claude-haiku-4-5": _ModelCatalogEntry(1.00, 5.00, generation_rank=1),
    "claude-sonnet-4-6": _ModelCatalogEntry(3.00, 15.00, generation_rank=2),
    "claude-sonnet-5": _ModelCatalogEntry(3.00, 15.00, generation_rank=3, thinking_on_by_default=True),
    "claude-opus-4-6": _ModelCatalogEntry(5.00, 25.00, generation_rank=2),
    "claude-opus-4-7": _ModelCatalogEntry(5.00, 25.00, generation_rank=2),
    "claude-opus-4-8": _ModelCatalogEntry(5.00, 25.00, generation_rank=3),
    "claude-opus-5": _ModelCatalogEntry(5.00, 25.00, generation_rank=4, thinking_on_by_default=True),
}

# Used only if live discovery itself fails outright (network/timeout on
# models.list() -- not an auth failure, which is raised instead) and no
# cataloged model could be confirmed available. Cheapest, broadly-available
# model in the catalog above, so a discovery outage degrades to "probably
# fine" rather than "adapter silently stops working".
_FALLBACK_DEFAULT_MODEL = "claude-haiku-4-5"


def select_cheapest_known_model(available_model_ids: Iterable[str]) -> Optional[str]:
    """Rank the models this key actually has access to (per live discovery)
    against the manually-maintained catalog: cheapest input price first,
    output price as the next tiebreaker, and generation_rank (newer wins)
    only as a last resort among models priced identically on both. Returns
    None if none of the available models are in the catalog at all --
    callers fall back to _FALLBACK_DEFAULT_MODEL in that case; this never
    picks an uncataloged model blind."""
    available = set(available_model_ids)
    candidates = [
        (model_id, entry)
        for model_id, entry in _KNOWN_MODEL_CATALOG.items()
        if model_id in available
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda pair: (
            pair[1].input_price_per_mtok,
            pair[1].output_price_per_mtok,
            -pair[1].generation_rank,
        )
    )
    return candidates[0][0]


def list_available_models(api_key: str) -> ModelDiscoveryResult:
    """Real model discovery for the Settings-page "Discover Models"
    button. Deliberately a separate, standalone function rather than a
    refactor of _resolve_model's inline discovery call above (which is
    already covered by 18 passing regression tests) -- duplicating one
    small API call here is a smaller risk than touching that tested path
    to force reuse. Returns the raw catalog; the cheapest-known-model
    ranking in select_cheapest_known_model is a fallback-selection
    heuristic for when no Settings model is chosen, not a filter over
    what the Settings UI should offer the admin to pick from."""
    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    ids = sorted(m.id for m in client.models.list())
    return ModelDiscoveryResult(models=ids, total_available=len(ids))


def _thinking_runs_by_default(model_name: str) -> bool:
    entry = _KNOWN_MODEL_CATALOG.get(model_name)
    return bool(entry and entry.thinking_on_by_default)


def _extract_text(response: object) -> str:
    """Anthropic's Messages API response.content is a LIST of typed
    blocks, not a single string, and a real response can legitimately
    contain more than one text-type block (in addition to non-text blocks
    like `thinking` on a model that reasons by default -- see
    _KNOWN_MODEL_CATALOG's thinking_on_by_default). This concatenates
    EVERY text-type block in order, rather than reading content[0] alone --
    reading only the first block would silently truncate a real response
    whenever the model splits its answer across more than one text block,
    or whenever a thinking block happens to sort first. A declined request
    can return no text block at all, handled the same way (empty string).
    """
    if getattr(response, "stop_reason", None) == "refusal":
        return ""
    parts = [
        block.text
        for block in (getattr(response, "content", None) or [])
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts).strip()


class AnthropicAdapter:
    name = "anthropic"

    def __init__(self, settings: ProviderSettings) -> None:
        self.settings = settings
        # Cached result of live model discovery -- resolved once per adapter
        # instance (not per call), since Tier5Service constructs one
        # adapter instance per provider and reuses it for every metric in a
        # run (see service.py); re-querying client.models.list() on every
        # translate() call would mean a second API round trip per metric
        # for no benefit, since the set of models this key can use does not
        # change mid-run. Never consulted at all when settings.model is set
        # explicitly.
        self._resolved_model: Optional[str] = None

    def is_available(self) -> bool:
        return bool(self.settings.api_key or os.getenv(self.settings.enabled_env))

    def _resolve_model(self, client_cls, api_key: str, timeout: float) -> str:
        if self.settings.model:
            return self.settings.model
        if self._resolved_model:
            return self._resolved_model

        chosen: Optional[str] = None
        try:
            discovery_client = client_cls(api_key=api_key, timeout=timeout)
            available_ids = {m.id for m in discovery_client.models.list()}

            # Visible staleness signal for _KNOWN_MODEL_CATALOG (see module
            # docstring): logged whenever discovery surfaces ANY model ID
            # this table doesn't recognize -- not only when every model is
            # unrecognized. A partial miss (some known, some not) still
            # means the catalog may be missing a newer/cheaper option, so
            # it gets the same visible WARNING as a total miss, just with
            # a different "what happened instead" tail.
            unknown_ids = available_ids - set(_KNOWN_MODEL_CATALOG)
            chosen = select_cheapest_known_model(available_ids)
            if unknown_ids:
                if chosen:
                    outcome = f"selected {chosen!r} from the known catalog"
                else:
                    outcome = f"falling back to {_FALLBACK_DEFAULT_MODEL!r}"
                logger.warning(
                    "Anthropic adapter: %d model(s) visible to this key are not in "
                    "the manually-maintained pricing catalog (_KNOWN_MODEL_CATALOG "
                    "in anthropic_adapter.py) and may represent a newer/cheaper "
                    "option this adapter can't see -- %s: %s",
                    len(unknown_ids), outcome, sorted(unknown_ids),
                )
        except Exception as exc:
            if is_auth_error(exc):
                raise
            logger.warning(
                "Anthropic adapter: live model discovery failed, falling back to %r: %s",
                _FALLBACK_DEFAULT_MODEL, exc,
            )

        self._resolved_model = chosen or _FALLBACK_DEFAULT_MODEL
        return self._resolved_model

    def translate(
        self, prompt: str, system_message: str, max_tokens: Optional[int] = None
    ) -> Optional[RawResult]:
        api_key = self.settings.api_key or os.getenv(self.settings.enabled_env)
        if not api_key:
            return None

        try:
            from anthropic import Anthropic
        except Exception as exc:
            logger.warning("Anthropic adapter: anthropic package unavailable: %s", exc)
            return None

        timeout = float(self.settings.timeout_seconds or 30)
        max_retries = int(self.settings.max_retries or 2)
        # max_tokens param (from Tier5Service.translate_batch, scaled to
        # batch size) takes priority over the single-metric env default --
        # see ProviderAdapter.translate's docstring for the real incident
        # this closes (a 13-metric batch truncated under the flat 500
        # default, discarding the whole batch).
        if max_tokens is None:
            max_tokens = int(os.getenv("ANTHROPIC_DAX_MAX_TOKENS", "500"))

        try:
            model_name = self._resolve_model(Anthropic, api_key, timeout)
        except Exception as exc:
            if is_auth_error(exc):
                logger.warning("Anthropic adapter model-discovery auth failure: %s", exc)
                raise ProviderAuthError("anthropic", exc) from exc
            logger.warning("Anthropic adapter: could not resolve a model, giving up: %s", exc)
            return None

        # temperature/top_p are deliberately never sent. Anthropic's
        # Messages API rejects non-default values for both outright on
        # several current-generation models (Claude Opus 5, Sonnet 5, Opus
        # 4.7/4.8 all 400 on a non-default temperature/top_p/top_k) while
        # accepting them on older ones -- and which model this adapter uses
        # is decided dynamically by _resolve_model above, so a fixed value
        # safe for one model can 400 on another. Omitting the parameter
        # entirely is safe on every model in the catalog and relies on the
        # same prompt-level determinism instructions (tier5/prompt.py's
        # "Rules:" section) every other Tier 5 provider already depends on
        # for consistent output shape.
        request_kwargs = {}
        if _thinking_runs_by_default(model_name):
            # Only disabled for models known to run adaptive thinking when
            # `thinking` is omitted -- thinking tokens count against
            # max_tokens, so leaving it unset for one of these models risks
            # the whole budget being spent on reasoning before any answer
            # text is produced. Omitted for every other model rather than
            # sent as {"type": "disabled"} unconditionally, since that
            # shape isn't documented as valid on models that never had a
            # `thinking` parameter at all.
            request_kwargs["thinking"] = {"type": "disabled"}

        sql: Optional[str] = None
        for attempt in range(1, max_retries + 1):
            try:
                client = Anthropic(api_key=api_key, timeout=timeout)
                response = client.messages.create(
                    model=model_name,
                    max_tokens=max_tokens,
                    system=system_message,
                    messages=[{"role": "user", "content": prompt}],
                    **request_kwargs,
                )
                sql = _extract_text(response)
                break
            except Exception as exc:
                if is_auth_error(exc):
                    logger.warning("Anthropic adapter auth failure, skipping remaining retries: %s", exc)
                    raise ProviderAuthError("anthropic", exc) from exc
                logger.warning("Anthropic adapter attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 10))
                else:
                    return None

        sql = strip_markdown_fences(sql)
        if not sql:
            return None
        return RawResult(text=sql, confidence=_PLACEHOLDER_CONFIDENCE)
