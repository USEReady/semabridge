"""Shared detector for the NULL-cast placeholder SQL shape.

Every DAX/SQL translation path in this codebase (LLM providers, the OpenAI
batch-prefetch cache, deployment-time DDL remediation, and any structural
DDL-string rewrite pass) can independently decide a metric/measure is
untranslatable and represent that decision with a bare
``CAST(NULL AS <type>)`` expression -- distinct call sites, but the exact
same SQL shape. Detecting that shape in ONE place lets every consumer treat
"translation returned this placeholder" the same way it already treats
"translation returned nothing": as a drop that must be recorded, never as a
successful result to emit (or count as accounted-for) as if it were real
SQL.

Structural (regex over SQL grammar), not tied to any specific metric,
model, table, or column name -- matches any CAST(NULL AS ...) shape
regardless of the target type, in any SQL dialect that spells NULL-casts
this way (Snowflake, Databricks/Spark, generic ANSI SQL).
"""
from __future__ import annotations

import re

_SYNONYMS_SUFFIX_RE = re.compile(r'\s*WITH\s+SYNONYMS\s*=?\s*\(.*\)\s*$', re.IGNORECASE)
_NULL_CAST_RE = re.compile(r'^\s*CAST\s*\(\s*NULL\s+AS\s+[A-Z0-9_ ,()]+\)\s*$', re.IGNORECASE)


def is_null_cast_sql(expr: str) -> bool:
    """True if `expr` is exactly a NULL-cast placeholder (any target type),
    ignoring a trailing WITH SYNONYMS(...) clause and surrounding whitespace.
    """
    if not expr:
        return False
    stripped = _SYNONYMS_SUFFIX_RE.sub("", str(expr).strip()).strip()
    return bool(_NULL_CAST_RE.match(stripped))
