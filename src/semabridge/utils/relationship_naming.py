"""Utility helpers for deterministic semantic relationship names."""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


def _sanitize_identifier(value: str) -> str:
    """Normalize an identifier to Snowflake-compatible format using IdentifierSanitizer."""
    if not value:
        return ""
    from semabridge.utils.identifiers import IdentifierSanitizer
    sanitizer = IdentifierSanitizer(force_uppercase=True, suppress_reserved=False)
    return sanitizer.sanitize_column(value)


def generate_relationship_name(
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
) -> str:
    """Generate deterministic relationship name: REL_<FROM>_<FROM_COL>__<TO>_<TO_COL>."""
    return (
        f"REL_{_sanitize_identifier(from_table)}_{_sanitize_identifier(from_column)}"
        f"__{_sanitize_identifier(to_table)}_{_sanitize_identifier(to_column)}"
    )


class RelationshipNameTracker:
    """Track deterministic relationship names keyed by full relationship endpoint."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str, str], str] = {}

    def next_name(self, from_table: str, from_column: str, to_table: str, to_column: str) -> str:
        key = (
            _sanitize_identifier(from_table),
            _sanitize_identifier(from_column),
            _sanitize_identifier(to_table),
            _sanitize_identifier(to_column),
        )
        if key not in self._cache:
            self._cache[key] = generate_relationship_name(
                from_table,
                from_column,
                to_table,
                to_column,
            )
        return self._cache[key]
