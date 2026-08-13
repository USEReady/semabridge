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


def comment_clause(description: str | None) -> str:
    """Render a Snowflake semantic-view per-item COMMENT fragment.

    Empty/blank description (the common case today -- see this
    function's caller sites, all gated on this same check) returns "",
    so nothing changes for any dimension/metric that has no description
    authored in the source model.
    """
    text = str(description or "").strip()
    if not text:
        return ""
    return f" COMMENT {_quote_synonym(text)}"
