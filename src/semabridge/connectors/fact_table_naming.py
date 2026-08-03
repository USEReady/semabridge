"""Shared whole-word fact-table-name detection.

Used by both snowflake_emitter.py (_identify_fact_table) and
tables_clause_builder.py (anchor-injection gating) so a fix to one can't
silently diverge from the other — tables_clause_builder.py used to have
its own unbounded substring check ("fact" in name.lower(), which matches
"Manufacturer"/"Artifact"/"Satisfaction") that never got updated when
snowflake_emitter.py's copy was fixed to match whole words only.
"""
from __future__ import annotations

import re

FACT_KEYWORDS = frozenset({"FACT", "FACTS", "SALES", "TRANSACTION", "TRANSACTIONS", "AGGREGATE", "AGGREGATES"})

_WORD_RE = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+')


def tokenize_dataset_name(name: str) -> list[str]:
    """
    Split a dataset name into whole "words" so keyword matching can't be
    fooled by a keyword appearing mid-word (e.g. "Manufacturer" contains
    "fact" as a substring, "Artifact" and "Satisfaction" do too, but none
    of them are actual fact tables).

    Splits on non-alphanumeric separators (underscores, spaces, hyphens)
    and on camelCase/PascalCase boundaries, e.g. "SalesFact" -> ["Sales",
    "Fact"], but "Manufacturer" stays a single token since it has no
    internal case transition.
    """
    tokens: list[str] = []
    for part in re.split(r'[^A-Za-z0-9]+', name or ""):
        if part:
            tokens.extend(_WORD_RE.findall(part))
    return [t.upper() for t in tokens if t]


def is_fact_like_name(name: str) -> bool:
    """True if `name` contains one of FACT_KEYWORDS as a whole word."""
    return bool(set(tokenize_dataset_name(name)) & FACT_KEYWORDS)
