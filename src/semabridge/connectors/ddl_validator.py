"""Semantic view DDL validator for Snowflake constraints."""
from __future__ import annotations

import re
from typing import List


def _extract_block(ddl: str, header: str) -> List[str]:
    lines = ddl.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip().upper() == header:
            start = i
            break
    if start is None:
        return []
    end = start + 1
    while end < len(lines) and not lines[end].strip().startswith(")"):
        end += 1
    return [l.strip() for l in lines[start + 1:end] if l.strip()]


def validate_semantic_ddl(ddl: str) -> List[str]:
    errors: List[str] = []
    if not ddl:
        errors.append("DDL is empty")
        return errors

    # Basic parentheses balance
    if ddl.count("(") != ddl.count(")"):
        errors.append("Unbalanced parentheses in DDL")

    metrics = _extract_block(ddl, "METRICS (")
    forbidden = (" SELECT ", " FROM ", " JOIN ", " WITH ", " UNION ", ";")
    for line in metrics:
        # Strip WITH SYNONYMS to avoid matching forbidden " WITH " CTE keyword
        clean_line = re.sub(r'(?i)\bWITH\s+SYNONYMS\s*=\s*\(.*?\)', '', line)
        clean_line = re.sub(r'(?i)\bWITH\s+SYNONYMS\b.*', '', clean_line)
        upper = f" {clean_line.upper()} "
        if any(tok in upper for tok in forbidden):
            errors.append(f"Non-scalar SQL detected in METRICS: {line[:160]}")
            break

    return errors
