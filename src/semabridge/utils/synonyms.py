from __future__ import annotations
from typing import List
import re


def merge_synonyms(
    user_defined: List[str],
    auto_generated: List[str],
    max_auto: int = 3,
) -> List[str]:
    """
    Merge user-defined and auto-generated synonyms into a single deduplicated list.

    Priority:
        1. User-defined synonyms (from TMSL JSON) — appear first, all kept.
        2. Auto-generated synonyms (from name heuristics) — appended after,
           capped at `max_auto` entries, only if not already present.

    Args:
        user_defined:  Synonyms from the source TMSL "synonyms" field.
        auto_generated: Synonyms produced by _auto_synonyms() heuristic.
        max_auto:      Maximum number of auto-generated synonyms to append (default 3).

    Returns:
        Deduplicated merged list, user-defined entries first.
    """
    import unicodedata
    seen: set[str] = set()
    result: List[str] = []

    # Handle potentially None inputs
    user_defined = user_defined or []
    auto_generated = auto_generated or []

    for s in user_defined:
        if not isinstance(s, str):
            continue
        # Normalize and strip (NFC is standard for visual equivalence)
        s_norm = unicodedata.normalize("NFC", s.strip())
        if s_norm and s_norm.lower() not in seen:
            seen.add(s_norm.lower())
            result.append(s.strip()) # Keep original casing from user

    auto_added = 0
    for s in auto_generated:
        if not isinstance(s, str):
            continue
        s_norm = unicodedata.normalize("NFC", s.strip())
        if s_norm and s_norm.lower() not in seen and auto_added < max_auto:
            seen.add(s_norm.lower())
            result.append(s.strip())
            auto_added += 1

    return result


def generate_auto_synonyms(name: str) -> list[str]:
    """
    Generate simple synonym candidates from a column/measure name.
    Useful for automated metadata generation when user synonyms are missing.

    Example: "CustomerID" → ["Customer ID"]
             "sale_amount" → ["Sale Amount"]
    """
    # 1. Clean up special characters like () []
    clean_name = re.sub(r'[^a-zA-Z0-9_\s]', ' ', name)
    # 2. Convert snake_case to spaces
    snake_separated = clean_name.replace("_", " ").strip()
    # 3. Handle camelCase, PascalCase, and Acronyms (e.g. VATRate -> VAT Rate)
    # Insert space between lower and upper: aB -> a B
    c1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", snake_separated)
    # Insert space between upper and upper-lower: ABc -> A Bc
    camel_separated = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", c1)
    
    title_form = camel_separated.title().strip()

    synonyms: list[str] = []
    if title_form and title_form.lower() != name.lower():
        synonyms.append(title_form)

    # Common business abbreviation expansions
    _abbrev_map = {
        "Cust": "Customer", "Acct": "Account", "Amt": "Amount",
        "Qty": "Quantity", "Num": "Number", "Id": "ID",
        "Desc": "Description", "Dt": "Date", "Yr": "Year",
        "Mth": "Month", "Qtr": "Quarter", "Wk": "Week",
    }
    for abbrev, expansion in _abbrev_map.items():
        # Only replace if it's a whole word to avoid Customer -> Customeromer
        pattern = rf"\b{re.escape(abbrev)}\b"
        if re.search(pattern, title_form, re.IGNORECASE):
            expanded = re.sub(pattern, expansion, title_form, flags=re.IGNORECASE)
            if expanded.lower() != name.lower():
                synonyms.append(expanded)

    # Return deduplicated list (up to 3 synonyms)
    seen: list[str] = []
    for s in synonyms:
        if s not in seen and s.lower() != name.lower():
            seen.append(s)
    return seen[:3]
