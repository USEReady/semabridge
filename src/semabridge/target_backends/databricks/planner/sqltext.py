"""Small, bounded helpers over the converter's measure SQL.

Not a SQL parser: they only tell code apart from string literals and quoted
identifiers, so alias references and COUNT(*) are found (and rewritten) in
code only, never inside a string such as 'SALES.`X`'.
"""

from __future__ import annotations

import re
from typing import Callable, List, Set, Tuple

# ALIAS.`column` in the converter's output (aliases are plain identifiers).
REF = re.compile(r"(?<![A-Za-z0-9_`.])([A-Za-z_][A-Za-z0-9_]*)\.`((?:[^`]|``)+)`")
COUNT_STAR = re.compile(r"\bCOUNT\s*\(\s*\*\s*\)", re.IGNORECASE)


def segments(sql: str) -> List[Tuple[bool, str]]:
    """Split into (is_string_literal, text) pieces. Backtick identifiers stay in
    code pieces (REF needs them); single-quoted literals ('' escapes) are separate."""
    out: List[Tuple[bool, str]] = []
    buf: List[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "`":
            j = i + 1
            while j < n:
                if sql[j] == "`":
                    if j + 1 < n and sql[j + 1] == "`":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == "'":
            if buf:
                out.append((False, "".join(buf)))
                buf = []
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append((True, sql[i:j + 1]))
            i = j + 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        out.append((False, "".join(buf)))
    return out


def sub_in_code(pattern: "re.Pattern[str]", repl: Callable[["re.Match[str]"], str], sql: str) -> str:
    return "".join(text if is_lit else pattern.sub(repl, text) for is_lit, text in segments(sql))


def aliases(sql: str) -> Set[str]:
    found: Set[str] = set()
    for is_lit, text in segments(sql or ""):
        if not is_lit:
            found.update(m.group(1).upper() for m in REF.finditer(text))
    return found


def count_star(sql: str) -> int:
    return sum(len(COUNT_STAR.findall(text)) for is_lit, text in segments(sql or "") if not is_lit)


def refs(sql: str) -> List[Tuple[str, str]]:
    """(ALIAS_UPPER, column) for every ALIAS.`column` in code (not in string literals)."""
    out: List[Tuple[str, str]] = []
    for is_lit, text in segments(sql or ""):
        if not is_lit:
            out += [(m.group(1).upper(), m.group(2).replace("``", "`")) for m in REF.finditer(text)]
    return out


# A dotted reference chain: `a`.`b`.`c` or source.`c` (each part a backtick
# identifier or a plain identifier; never a number such as 1.5).
_PART = r"(?:`(?:[^`]|``)+`|[A-Za-z_][A-Za-z0-9_]*)"
CHAIN = re.compile(rf"(?<![A-Za-z0-9_`.]){_PART}(?:\s*\.\s*{_PART})+")
_PARTS = re.compile(_PART)


def _unquote(part: str) -> str:
    return part[1:-1].replace("``", "`") if part.startswith("`") else part


def reference_paths(sql: str) -> List[Tuple[str, str]]:
    """(path, column) for every dotted reference in code (not in string literals):
    ``store`.`district`.`name`` -> ("store.district", "name"); ``source.`x``` ->
    ("source", "x"). Structured: the parts are split on identifier boundaries, so a
    period inside a backtick name stays part of that name."""
    out: List[Tuple[str, str]] = []
    for is_lit, text in segments(sql or ""):
        if is_lit:
            continue
        for m in CHAIN.finditer(text):
            parts = [_unquote(p) for p in _PARTS.findall(m.group(0))]
            out.append((".".join(parts[:-1]), parts[-1]))
    return out
