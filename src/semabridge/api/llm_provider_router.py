"""Settings-page endpoints for Tier 5 LLM provider configuration
(OpenAI/Gemini/Groq/Featherless/Anthropic).

Storage and precedence logic live in
repository/llm_provider_credentials.py and
dax_translation/tier5/config.py's Tier5Config.resolve() -- this router is
a thin HTTP layer over those, following settings_api.py's existing
conventions (Depends(get_current_user) on every endpoint, HTTPException
for error responses, no os.environ mutation).

Model discovery calls out to each adapter's list_available_models() with
whatever key is currently effective for that provider (Settings, else
.env) -- see get_effective_api_key(). Failures are classified via the
shared is_auth_error() (tier5/adapters/base.py) into a bad-key response
distinct from a network/API error, per the "clear, honest UI feedback,
don't silently fail" requirement -- never a blanket 500.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from semabridge.api.deps import get_current_user
from semabridge.dax_translation.tier5.adapters.base import is_auth_error
from semabridge.dax_translation.tier5.adapters import (
    anthropic_adapter,
    featherless_adapter,
    gemini_adapter,
    groq_adapter,
    openai_adapter,
)
from semabridge.dax_translation.tier5.config import ProviderSettings, Tier5Config
from semabridge.repository.llm_provider_credentials import (
    delete_api_key,
    get_all_provider_status,
    get_effective_api_key,
    save_api_key,
    save_model,
)
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/settings/llm-providers", tags=["settings", "llm-providers"])

# One list_available_models function per provider name (see each
# adapter's own module docstring/function for its filtering rationale).
# Keyed by the same provider names Tier5Config.default().providers uses --
# not a separately hand-maintained list, so a provider can never exist in
# one place and not the other.
_LIST_MODELS_FUNCS = {
    "openai": openai_adapter.list_available_models,
    "gemini": gemini_adapter.list_available_models,
    "groq": groq_adapter.list_available_models,
    "featherless": featherless_adapter.list_available_models,
    "anthropic": anthropic_adapter.list_available_models,
}


class LlmProviderApiKeyCreate(BaseModel):
    api_key: str = Field(min_length=1, description="Raw provider API key -- encrypted before storage, never returned.")


class LlmProviderModelSelect(BaseModel):
    model: str = Field(min_length=1, description="Model ID to use for this provider, as returned by discover-models.")


class LlmProviderStatus(BaseModel):
    """One provider's current configuration, for the Settings UI to
    render. `hint` is only populated when `source == "settings"` --
    same masked-last-4 convention as settings_api.py's SecretResponse."""

    provider: str
    source: str  # "settings" | "env" | "none"
    configured: bool
    model: Optional[str] = None
    hint: Optional[str] = None


class DiscoverModelsResponse(BaseModel):
    provider: str
    models: List[str]
    truncated: bool = False
    total_available: Optional[int] = None


def _known_providers() -> Dict[str, ProviderSettings]:
    # default() is enough here -- callers only need provider_order/
    # enabled_env, and each read below (get_all_provider_status,
    # get_effective_api_key) does its own Settings-DB lookup already.
    # Resolving the full Settings-overridden config would mean a second,
    # redundant DB read for no benefit.
    return Tier5Config.default().providers


def _require_known_provider(provider: str) -> ProviderSettings:
    providers = _known_providers()
    settings = providers.get(provider)
    if settings is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown provider '{provider}'. Supported: {sorted(providers)}",
        )
    return settings


@router.get("", response_model=List[LlmProviderStatus])
def get_llm_provider_config(
    current_user=Depends(get_current_user),
) -> List[LlmProviderStatus]:
    """All 5 providers' current status -- which source (Settings/.env/
    none) is supplying the key, and whether a model has been selected."""
    config = Tier5Config.default()
    return [LlmProviderStatus(**s) for s in get_all_provider_status(config)]


@router.post("/{provider}/api-key", status_code=status.HTTP_204_NO_CONTENT)
def save_llm_provider_api_key(
    provider: str,
    body: LlmProviderApiKeyCreate,
    current_user=Depends(get_current_user),
) -> None:
    _require_known_provider(provider)
    save_api_key(provider, body.api_key)
    logger.info("Settings API key saved for LLM provider '%s'.", provider)


@router.delete("/{provider}/api-key", status_code=status.HTTP_204_NO_CONTENT)
def delete_llm_provider_api_key(
    provider: str,
    current_user=Depends(get_current_user),
) -> None:
    """Revert `provider` to .env-only by removing its Settings-stored key
    and model. Not destructive to .env -- if a .env value already exists
    for this provider, it keeps working exactly as before."""
    _require_known_provider(provider)
    delete_api_key(provider)
    logger.info("Settings API key removed for LLM provider '%s' (reverted to .env, if any).", provider)


@router.post("/{provider}/model", status_code=status.HTTP_204_NO_CONTENT)
def save_llm_provider_model(
    provider: str,
    body: LlmProviderModelSelect,
    current_user=Depends(get_current_user),
) -> None:
    """Save the selected model. Not validated against the discovered
    list server-side -- the Settings UI is expected to only offer
    discovered models in its dropdown; this endpoint trusts that
    choice rather than re-querying the provider on every save."""
    _require_known_provider(provider)
    save_model(provider, body.model)
    logger.info("Model '%s' saved for LLM provider '%s'.", body.model, provider)


@router.post("/{provider}/discover-models", response_model=DiscoverModelsResponse)
def discover_llm_provider_models(
    provider: str,
    current_user=Depends(get_current_user),
) -> DiscoverModelsResponse:
    """Real model discovery against the provider's currently *effective*
    key -- Settings if configured, else whatever .env already supplies --
    so discovery works even for a provider that's only ever been
    configured via .env, not just Settings."""
    settings = _require_known_provider(provider)

    api_key = get_effective_api_key(provider, settings.enabled_env)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for '{provider}' (Settings or .env). Save a key first.",
        )

    list_fn = _LIST_MODELS_FUNCS.get(provider)
    if list_fn is None:
        # Should be unreachable -- _LIST_MODELS_FUNCS is keyed off the
        # same provider names as Tier5Config.default().providers, which
        # _require_known_provider already validated against. Kept as an
        # explicit 501 rather than a bare KeyError in case the two ever
        # drift (e.g. a new provider added to one but not the other).
        raise HTTPException(status_code=501, detail=f"Model discovery is not implemented for '{provider}'.")

    try:
        result = list_fn(api_key)
    except Exception as exc:
        if is_auth_error(exc):
            raise HTTPException(
                status_code=401,
                detail=f"'{provider}' rejected the configured API key: {exc}",
            ) from exc
        logger.warning("Model discovery failed for provider '%s': %s", provider, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach {provider}'s model-listing API: {exc}",
        ) from exc

    return DiscoverModelsResponse(
        provider=provider,
        models=result.models,
        truncated=result.truncated,
        total_available=result.total_available,
    )
