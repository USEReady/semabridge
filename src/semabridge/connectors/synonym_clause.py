"""Snowflake semantic-view synonym clause rendering."""

from __future__ import annotations


def _quote_synonym(value: str) -> str:
    text = str(value or "").strip()
    return "'" + text.replace("'", "''") + "'"


def synonyms_clause(synonyms: list[str] | None) -> str:
    cleaned = [str(s or "").strip() for s in (synonyms or []) if str(s or "").strip()]
    if not cleaned:
        return ""
    return f" WITH SYNONYMS = ({', '.join(_quote_synonym(s) for s in cleaned)})"
