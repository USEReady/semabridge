"""Utility helpers for deterministic semantic relationship names."""

from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)


def _sanitize_identifier(value: str) -> str:
    """
    Normalize an identifier to Snowflake-compatible format.

    Rules:
    1. Convert to UPPERCASE
    2. Replace invalid characters (not [A-Z0-9_$]) with underscore
    3. If name starts with digit, prefix with underscore
    4. Collapse consecutive underscores
    5. Preserve semantic meaning

    Examples:
        "18_MONTH_FORWARD" → "_18_MONTH_FORWARD"
        "Revenue % Growth" → "REVENUE__GROWTH"
        "customer-id" → "CUSTOMER_ID"

    Args:
        value: Raw identifier name

    Returns:
        Snowflake-compatible identifier (uppercase, digit-safe)
    """
    if not value:
        return ""

    # Step 1: Strip leading/trailing whitespace
    cleaned = value.strip()

    # Step 2: Replace invalid characters (keep only [A-Za-z0-9_$])
    # Replace spaces, hyphens, periods, percent, etc. with underscore
    cleaned = re.sub(r"[^A-Za-z0-9_$]", "_", cleaned)

    # Step 3: Collapse consecutive underscores
    cleaned = re.sub(r"_+", "_", cleaned)

    # Step 4: Remove leading/trailing underscores ONLY from replacements
    # but preserve them if they come from digit-prefixing logic
    cleaned = cleaned.strip("_")

    # Step 5: If name starts with digit, prefix with underscore (Snowflake requirement)
    if cleaned and cleaned[0].isdigit():
        cleaned = f"_{cleaned}"

    # Step 6: Uppercase
    result = cleaned.upper()

    # Step 7: Validate result contains only allowed characters
    if result and not re.match(r"^[A-Z_][A-Z0-9_$]*$", result):
        logger.warning(f"Sanitized identifier '{value}' → '{result}' contains unexpected characters")

    return result


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
