"""ProviderAdapter protocol — the shape every Tier 5 provider must satisfy.

New interface, written for this consolidation (not a salvage). Each
concrete adapter wraps an existing provider-calling implementation
underneath (see openai_adapter.py / gemini_adapter.py / groq_adapter.py /
featherless_adapter.py for what was salvaged from where).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Protocol


def strip_markdown_fences(sql: Optional[str]) -> str:
    """Trivial response-format hygiene shared by every adapter (nearly
    every existing LLM call site has its own copy of this exact pattern —
    e.g. connectors/translator.py's _sanitize_sql_markdown,
    multi_model_translator.py's inline re.sub calls). Not a validation
    decision — that happens centrally in tier5/service.py via
    tier5/validation.py."""
    if not sql:
        return ""
    sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"```\s*", "", sql, flags=re.IGNORECASE)
    return sql.strip()


@dataclass
class RawResult:
    """A provider's raw response, before any repair/validation."""

    text: str
    confidence: float = 0.5


class ProviderAuthError(Exception):
    """Raised by an adapter when the provider rejects the request due to a
    bad/expired credential (401, invalid_api_key, etc.) — a failure that
    will recur identically on every subsequent call this run, unlike a
    transient failure (rate limit, timeout, network blip). Tier5Service
    catches this specifically to mark the provider unavailable for the
    rest of the current run instead of retrying it per-call."""

    def __init__(self, provider_name: str, original: BaseException) -> None:
        super().__init__(f"{provider_name}: authentication failed: {original}")
        self.provider_name = provider_name
        self.original = original


_AUTH_TYPE_NAME_MARKERS = (
    "authenticationerror",
    "permissiondenied",
    "unauthenticated",
    "unauthorizederror",
)

_AUTH_MESSAGE_MARKERS = (
    "invalid_api_key",
    "invalid api key",
    "expired_api_key",
    "expired api key",
    "incorrect api key",
    "api key not valid",
    "api_key_invalid",
    "unauthorized",
    "authentication failed",
    "invalid bearer token",
)


def is_auth_error(exc: BaseException) -> bool:
    """True if `exc` represents a bad/expired-credential (auth-class)
    failure rather than a transient one (rate limit, timeout, network
    error). Checked, in order: HTTP status code (401), the exception's
    type name (covers openai.AuthenticationError, google.api_core's
    PermissionDenied/Unauthenticated, groq's AuthenticationError, etc.
    without importing any provider SDK here), then a message-substring
    fallback for SDKs that wrap the real error in a generic exception
    type (e.g. Gemini's service wrapper)."""
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code == 401:
        return True

    type_name = type(exc).__name__.lower()
    if any(marker in type_name for marker in _AUTH_TYPE_NAME_MARKERS):
        return True

    message = str(exc).lower()
    return any(marker in message for marker in _AUTH_MESSAGE_MARKERS)


class ProviderAdapter(Protocol):
    """Every Tier 5 provider adapter implements this shape."""

    name: str

    def is_available(self) -> bool:
        """True if this provider's API key/config is present."""
        ...

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        """Call the provider. Returns None on an ordinary failure (missing
        key, network error, empty response, timeout, rate limit) — never
        raises for those. Callers treat None as 'try the next provider',
        matching every existing call site's try/except-and-continue
        behavior.

        Raises ProviderAuthError specifically when the provider rejects
        the request for a bad/expired credential — a distinct signal from
        an ordinary failure, since it will recur identically on every
        later call this run and should not be retried."""
        ...
