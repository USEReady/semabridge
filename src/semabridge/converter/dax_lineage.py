"""Lineage analysis for DAX expressions to identify underlying aggregated tables."""

from __future__ import annotations

import re
from typing import Dict, Optional, Set

_IGNORE_FUNCS = (
    "ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "ISINSCOPE",
    "SELECTEDVALUE", "VALUES", "HASONEVALUE", "ISFILTERED", "TREATAS", "USERELATIONSHIP"
)


def _args_of(expr: str, start: int):
    d, i = 1, start
    while i < len(expr) and d:
        d += {"(": 1, ")": -1}.get(expr[i], 0)
        i += 1
    return expr[start:i - 1], i


def _split_args(s: str):
    out, d, cur, q = [], 0, [], None
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch in "([":
            d += 1
        elif ch in ")]":
            d -= 1
        elif ch == "," and d == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def resolve_dax_lineage_tables(
    dax: str,
    measure_dax_map: Optional[Dict[str, str]] = None,
    default_home_table: Optional[str] = None,
    seen: Optional[Set[str]] = None,
) -> Set[str]:
    """Extract tables whose columns are aggregated in the DAX expression,
    following measure references and ignoring filter/selector arguments."""
    if not dax:
        return set()
    seen = seen or set()
    clean_dax = re.sub(r"(--|//)[^\n]*", "", dax)

    # Filter and selector arguments never decide the anchor
    for f in _IGNORE_FUNCS:
        while True:
            m = re.search(rf"\b{f}\s*\(", clean_dax, re.IGNORECASE)
            if not m:
                break
            _, end = _args_of(clean_dax, m.end())
            clean_dax = clean_dax[:m.start()] + "0" + clean_dax[end:]

    # CALCULATE(<expr>, <filters>...) and FILTER(<table>, <predicate>): only <expr> / <table> decide the anchor;
    # aggregates inside filters (e.g. 'Date'[Period] = MAX('Date'[Period])) are conditions, not what is measured
    for f in ("CALCULATE", "CALCULATETABLE", "FILTER"):
        pos = 0
        while True:
            m = re.compile(rf"\b{f}\s*\(", re.IGNORECASE).search(clean_dax, pos)
            if not m:
                break
            inner, end = _args_of(clean_dax, m.end())
            parts = _split_args(inner)
            keep = parts[0] if parts else ""
            clean_dax = clean_dax[:m.end()] + keep + ")" + clean_dax[end:]
            pos = m.end()

    # In RANKX(<table>, <expr>), the table is the ranking set; the expression decides
    clean_dax = re.sub(r"\bRANKX\s*\(\s*[^,]+,", "RANKX(0,", clean_dax, flags=re.IGNORECASE)

    out: Set[str] = set()
    # Simple aggregates
    for m in re.finditer(r"\b(SUM|AVERAGE|AVG|MIN|MAX|COUNT|COUNTA|COUNTBLANK|DISTINCTCOUNT|MEDIAN)\s*\(", clean_dax, re.IGNORECASE):
        a, _ = _args_of(clean_dax, m.end())
        t = re.match(r"\s*(?:'([^']+)'|([A-Za-z_][\w]*))\s*\[", a)
        if t:
            out.add((t.group(1) or t.group(2)).strip())
        elif re.match(r"\s*\[", a) and default_home_table:
            out.add(default_home_table.strip())

    # Iterators
    for m in re.finditer(r"\b(COUNTROWS|SUMX|AVERAGEX|MINX|MAXX|COUNTX|COUNTAX|FILTER|KEEPFILTERS)\s*\(", clean_dax, re.IGNORECASE):
        a, _ = _args_of(clean_dax, m.end())
        first = _split_args(a)[0] if a.strip() else ""
        t = re.match(r"\s*(?:'([^']+)'|([A-Za-z_][\w ]*))\s*$", first)
        if t:
            out.add((t.group(1) or t.group(2)).strip())

    # Measure references
    if measure_dax_map:
        for m in re.finditer(r"(?<![\w'\]])\[([^\]]+)\]", clean_dax):
            ref_name = m.group(1)
            if ref_name in seen:
                continue
            ref_dax = measure_dax_map.get(ref_name)
            if ref_dax:
                new_seen = seen | {ref_name}
                out |= resolve_dax_lineage_tables(ref_dax, measure_dax_map, default_home_table, new_seen)

    return out

