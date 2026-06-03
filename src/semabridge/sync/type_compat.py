"""
Type compatibility matrix for SemaBridge conflict detection.

Replaces the blanket CRITICAL severity for all type changes with a nuanced
severity based on whether the type change is a safe widening, precision loss,
or destructive conversion.
"""
from __future__ import annotations

import re

# Import lazily to avoid circular import at module level
# ConflictSeverity is defined in sync/conflict_resolver.py


# (source_type_upper, target_type_upper) → severity string
# Severity strings match ConflictSeverity enum values
TYPE_COMPAT_MATRIX: dict[tuple[str, str], str] = {
    # Safe widenings — INFO only
    ("INTEGER", "BIGINT"):      "INFO",
    ("INTEGER", "DECIMAL"):     "INFO",
    ("INTEGER", "FLOAT"):       "INFO",
    ("INTEGER", "DOUBLE"):      "INFO",
    ("INTEGER", "NUMBER"):      "INFO",
    ("BIGINT", "DECIMAL"):      "INFO",
    ("BIGINT", "FLOAT"):        "INFO",
    ("BIGINT", "DOUBLE"):       "INFO",
    ("DECIMAL", "FLOAT"):       "INFO",
    ("DECIMAL", "DOUBLE"):      "INFO",
    ("FLOAT", "DOUBLE"):        "INFO",
    ("INT", "BIGINT"):          "INFO",
    ("INT", "DECIMAL"):         "INFO",
    ("INT", "FLOAT"):           "INFO",
    ("INT", "NUMBER"):          "INFO",
    ("DATE", "TIMESTAMP"):      "WARNING",     # Widening but downstream views may break
    ("DATE", "TIMESTAMP_NTZ"):  "WARNING",
    ("DATE", "TIMESTAMP_LTZ"):  "WARNING",

    # Precision loss — WARNING
    ("DOUBLE", "FLOAT"):        "WARNING",
    ("FLOAT", "DECIMAL"):       "WARNING",
    ("BIGINT", "INTEGER"):      "WARNING",
    ("BIGINT", "INT"):          "WARNING",
    ("DECIMAL", "INTEGER"):     "WARNING",
    ("DECIMAL", "INT"):         "WARNING",

    # Destructive — CRITICAL (explicit for clarity; also default)
    ("INTEGER", "STRING"):      "CRITICAL",
    ("INTEGER", "VARCHAR"):     "CRITICAL",
    ("FLOAT", "INTEGER"):       "CRITICAL",
    ("FLOAT", "INT"):           "CRITICAL",
    ("STRING", "INTEGER"):      "CRITICAL",
    ("VARCHAR", "INTEGER"):     "CRITICAL",
    ("STRING", "INT"):          "CRITICAL",
    ("TIMESTAMP", "DATE"):      "CRITICAL",    # Truncation
    ("TIMESTAMP_NTZ", "DATE"):  "CRITICAL",
    ("BOOLEAN", "INTEGER"):     "WARNING",     # Technically valid but semantically odd
    ("INTEGER", "BOOLEAN"):     "CRITICAL",
}

# Normalize common aliases to canonical names
_TYPE_ALIASES: dict[str, str] = {
    "INT": "INTEGER",
    "INT64": "BIGINT",
    "INT32": "INTEGER",
    "INT16": "INTEGER",
    "SMALLINT": "INTEGER",
    "TINYINT": "INTEGER",
    "FLOAT4": "FLOAT",
    "FLOAT8": "DOUBLE",
    "REAL": "FLOAT",
    "NUMERIC": "DECIMAL",
    "NUMBER": "DECIMAL",
    "CHAR": "VARCHAR",
    "TEXT": "VARCHAR",
    "NVARCHAR": "VARCHAR",
    "STRING": "VARCHAR",
    "BOOL": "BOOLEAN",
    "DATETIME": "TIMESTAMP",
    "TIMESTAMPTZ": "TIMESTAMP_LTZ",
}


def _normalize(type_str: str) -> str:
    """Normalize a type string: strip precision params, upper-case, resolve aliases."""
    t = re.sub(r'\s*\(.*\)', '', type_str).strip().upper()
    return _TYPE_ALIASES.get(t, t)


def column_type_conflict_severity(source_type: str, target_type: str) -> str:
    """Return conflict severity string for a source→target type change.

    Returns one of: "INFO", "WARNING", "CRITICAL"

    Usage:
        severity_str = column_type_conflict_severity("INTEGER", "DECIMAL")
        # → "INFO"
    """
    s = _normalize(source_type)
    t = _normalize(target_type)
    if s == t:
        return "INFO"  # no change
    return TYPE_COMPAT_MATRIX.get((s, t), "CRITICAL")
