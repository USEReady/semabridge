"""Single documented source of truth for reserved-word column prefix handling and separator normalization."""

from __future__ import annotations

import re
from typing import Iterable, Optional, Set

# Prefix prepended by IdentifierSanitizer when a physical column name matches a Snowflake reserved word.
RESERVED_COLUMN_PREFIX = "COL_"


def normalize_separator_ident(name: Optional[str], *, pct_word: bool = False) -> str:
    """Normalize case and whitespace/underscore/hyphen separators while preserving symbols.

    When ``pct_word=True``, ``%`` is mapped to ``pct`` so display names containing ``%``
    match sanitized identifiers containing ``_PCT`` without colliding with names that lack it.
    """
    text = str(name or "").strip().casefold()
    if pct_word:
        text = text.replace("%", "pct")
    return re.sub(r"[\s_\-]+", "", text)


def strip_reserved_column_prefix(
    ident: str,
    valid_columns: Iterable[str],
) -> Optional[str]:
    """Strip ``RESERVED_COLUMN_PREFIX`` (``COL_``) only when the remainder matches a real column.

    Returns the matching real column name from ``valid_columns`` if found, otherwise ``None``.
    """
    raw = str(ident or "").strip().strip('"').strip("`")
    prefix_cf = RESERVED_COLUMN_PREFIX.casefold()
    if not raw.casefold().startswith(prefix_cf) or len(raw) <= len(prefix_cf):
        return None
    remainder_norm = normalize_separator_ident(raw[len(prefix_cf):])
    if not remainder_norm:
        return None
    for col in valid_columns:
        col_str = str(col or "").strip()
        if col_str and normalize_separator_ident(col_str) == remainder_norm:
            return col_str
    return None


def expand_column_aliases(target: str, table_columns: Iterable[str]) -> Set[str]:
    """Return ``target`` plus matching table columns via separator normalization or ``COL_`` stripping."""
    cols = [str(c).strip() for c in table_columns if str(c or "").strip()]
    aliases: Set[str] = {target}
    target_norm = normalize_separator_ident(target)
    stripped = strip_reserved_column_prefix(target, cols)
    for col in cols:
        col_norm = normalize_separator_ident(col)
        if col_norm == target_norm:
            aliases.add(col)
        elif stripped and col_norm == normalize_separator_ident(stripped):
            aliases.add(col)
        else:
            prefixed = f"{RESERVED_COLUMN_PREFIX}{target}".casefold()
            if col.casefold() == prefixed and col_norm.startswith(RESERVED_COLUMN_PREFIX.casefold()):
                aliases.add(col)
    return aliases

