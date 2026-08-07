"""Settings-page storage for LLM provider (Tier 5) API keys and selected
models.

Reuses the existing generic credential table (``semabridge_credentials``,
via CredentialManager) rather than a new table. CredentialManager's own
``value`` column is plaintext at rest -- ``is_secret`` there only controls
UI masking, not encryption. This module is what actually encrypts the API
key value, using the SAME Fernet mechanism already used for
``Account.encrypted_token`` (``auth/encryption.py``'s ``encrypt_token`` /
``decrypt_token``), applied here at the read/write boundary rather than
inside CredentialManager itself -- so CredentialManager's existing
behavior for fabric/snowflake/databricks is completely unchanged; only new
``llm_*`` service rows ever pass through encryption.

Scope: global (``owner_id=0``), not per-authenticated-user. These are
admin-configured, deployment-wide settings -- the same scope ``.env``
already has (one canonical value for the whole process), not a per-user
secret. That distinction matters: ``settings_api.py``'s ``save_secret()``
explicitly avoids mutating ``os.environ`` because a *per-user* secret
written there would leak across concurrent users. A genuinely global
setting doesn't have that leak risk -- but this module still avoids
``os.environ`` entirely in favor of ``ProviderSettings.api_key``, to dodge
the narrower concurrent-run race described in ``Tier5Config.resolve()``'s
docstring.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Dict, List, Optional

from semabridge.auth.encryption import decrypt_token, encrypt_token
from semabridge.repository.credential_manager import CredentialManager
from semabridge.utils.logger import get_logger

if TYPE_CHECKING:
    from semabridge.dax_translation.tier5.config import Tier5Config

logger = get_logger(__name__)

_SERVICE_PREFIX = "llm_"
_GLOBAL_OWNER_ID = 0


def _service_name(provider_name: str) -> str:
    return f"{_SERVICE_PREFIX}{provider_name}"


def _get_stored(provider_name: str) -> Dict[str, str]:
    manager = CredentialManager()
    return manager.get_credentials(
        _service_name(provider_name),
        mask_secrets=False,
        user_id=_GLOBAL_OWNER_ID,
        include_env_fallback=False,
    )


def save_api_key(provider_name: str, raw_api_key: str) -> None:
    """Encrypt and store `raw_api_key` as the Settings-configured key for
    `provider_name`. Overwrites any previously stored key for this
    provider."""
    manager = CredentialManager()
    manager.save_credentials(
        _service_name(provider_name),
        {"api_key": encrypt_token(raw_api_key)},
        user_id=_GLOBAL_OWNER_ID,
    )


def delete_api_key(provider_name: str) -> int:
    """Remove the Settings-configured key (and any saved model) for
    `provider_name`, reverting that provider to .env-only. Returns the
    number of rows removed."""
    manager = CredentialManager()
    return manager.delete_credentials(_service_name(provider_name), user_id=_GLOBAL_OWNER_ID)


def save_model(provider_name: str, model: str) -> None:
    """Store the selected model for `provider_name`. Not a secret --
    stored as-is, no encryption."""
    manager = CredentialManager()
    manager.save_credentials(
        _service_name(provider_name),
        {"model": model},
        user_id=_GLOBAL_OWNER_ID,
    )


def get_decrypted_api_key(provider_name: str) -> Optional[str]:
    """The raw, decrypted Settings-configured key for `provider_name`, or
    None if none is stored. Does NOT fall back to .env -- callers that
    want that fallback too should use get_effective_api_key."""
    encrypted = _get_stored(provider_name).get("api_key")
    if not encrypted:
        return None
    return decrypt_token(encrypted) or None


def get_saved_model(provider_name: str) -> Optional[str]:
    return _get_stored(provider_name).get("model") or None


def get_effective_api_key(provider_name: str, enabled_env: str) -> Optional[str]:
    """Settings key if configured, else whatever .env has already put in
    os.environ for this provider's enabled_env. Used by the "discover
    models" endpoint so discovery works even for a provider that's only
    ever been configured via .env, not just Settings."""
    settings_key = get_decrypted_api_key(provider_name)
    if settings_key:
        return settings_key
    return os.environ.get(enabled_env) or None


def apply_settings_overrides(config: "Tier5Config") -> None:
    """Mutate `config.providers[*]` in place: for every provider with a
    Settings-configured key, populate `.api_key` (decrypted) and `.model`
    (if one has been saved). A provider with no Settings key is left
    completely untouched, so it keeps working from .env exactly as
    before -- this function only ever adds information, never removes a
    provider's ability to work from .env alone."""
    for provider_name, settings in config.providers.items():
        stored = _get_stored(provider_name)

        encrypted_key = stored.get("api_key")
        if encrypted_key:
            decrypted = decrypt_token(encrypted_key)
            if decrypted:
                settings.api_key = decrypted
            else:
                logger.warning(
                    "llm_provider_credentials: stored key for %r failed to decrypt "
                    "(corrupt row or encryption key rotated) -- leaving this provider "
                    "on .env only for this run",
                    provider_name,
                )

        model = stored.get("model")
        if model:
            settings.model = model


def get_all_provider_status(config: "Tier5Config") -> List[dict]:
    """One status dict per provider in `config.providers`, for the
    Settings UI's "read full config" endpoint: which source (Settings vs
    .env vs none) is currently supplying the key, whether a model has
    been selected, and a masked hint of the Settings key if present."""
    statuses = []
    for provider_name, settings in config.providers.items():
        stored = _get_stored(provider_name)
        encrypted_key = stored.get("api_key")
        env_value = os.environ.get(settings.enabled_env)

        hint = None
        if encrypted_key:
            source = "settings"
            decrypted = decrypt_token(encrypted_key)
            if decrypted:
                hint = _make_hint(decrypted)
        elif env_value:
            source = "env"
        else:
            source = "none"

        statuses.append({
            "provider": provider_name,
            "source": source,
            "configured": source != "none",
            "model": stored.get("model") or None,
            "hint": hint,
        })
    return statuses


def _make_hint(value: str) -> str:
    """Same masking convention as settings_api.py's _make_hint (last 4
    chars only)."""
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"
