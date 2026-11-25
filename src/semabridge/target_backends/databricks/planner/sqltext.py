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
