"""Snowflake semantic-view synonym clause rendering."""

from __future__ import annotations


def _quote_synonym(value: str) -> str:
    text = str(value or "").strip()
    return "'" + text.replace("'", "''") + "'"


def synonyms_clause(synonyms: list[str] | None) -> str:
    """Snowflake CREATE SEMANTIC VIEW DDL does not support a SYNONYMS clause in SQL DDL.

    Synonyms are maintained in SML metadata and exported to Cortex Analyst YAML specs.
    Returns empty string for SQL DDL emission.
    """
    return ""
