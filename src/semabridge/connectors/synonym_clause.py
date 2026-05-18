from __future__ import annotations
from typing import List


def synonyms_clause(synonyms: List[str]) -> str:
    """
    Render a Snowflake WITH SYNONYMS clause string.

    Returns an empty string when synonyms is empty (clause is omitted).

    Examples:
        synonyms_clause([])                    → ""
        synonyms_clause(["Sales", "Income"])   → " WITH SYNONYMS = ('Sales', 'Income')"

    The leading space is intentional so callers can append directly to a DDL line:
        f'  {alias}."{name}" AS {ref}{synonyms_clause(col.synonyms)}'
    """
    # Escape single quotes and wrap each synonym in quotes (strip whitespace)
    quoted = ["'" + s.strip().replace("'", "''") + "'" for s in synonyms if s and s.strip()]
    
    if not quoted:
        return ""
        
    return f" WITH SYNONYMS = ({', '.join(quoted)})"
