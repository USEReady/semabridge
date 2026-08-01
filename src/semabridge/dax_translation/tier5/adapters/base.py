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


class ProviderAdapter(Protocol):
    """Every Tier 5 provider adapter implements this shape."""

    name: str

    def is_available(self) -> bool:
        """True if this provider's API key/config is present."""
        ...

    def translate(self, prompt: str, system_message: str) -> Optional[RawResult]:
        """Call the provider. Returns None on any failure (missing key,
        network error, empty response) — never raises. Callers treat None
        as 'try the next provider', matching every existing call site's
        try/except-and-continue behavior."""
        ...
