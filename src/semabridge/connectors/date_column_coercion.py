"""Make semantic-view DDL work when an existing table stores a date as text.

Semabridge never alters tables it binds to (they may hold customer data), so
the generated SQL has to cope with the column types that are actually there.
A Power BI date column can live in Snowflake as TEXT/VARCHAR (for example a
table created earlier, or loaded from CSV). Snowflake implicitly casts text in
comparisons, but date functions such as YEAR()/MONTH()/EXTRACT/DATE_TRUNC
reject it outright -- "Function EXTRACT does not support VARCHAR(500) argument
type" -- and that one error fails the whole semantic view.

`coerce_text_date_columns` rewrites the final DDL: inside the arguments of any
date/time function, a reference ``ALIAS."COL"`` whose live type is text becomes
``TRY_TO_TIMESTAMP(ALIAS."COL")``. TRY_TO_TIMESTAMP reads both date and
timestamp strings and yields NULL (not an error) for values that are not dates.
It is generic: table aliases come from the DDL's own TABLES clause and column
types from information_schema, so no model, table or column name is assumed.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Set, Tuple

# Snowflake date/time functions that reject text arguments.
DATE_FUNCTIONS = (
    "YEAR", "YEAROFWEEK", "YEAROFWEEKISO", "QUARTER", "MONTH", "WEEK", "WEEKOFYEAR",
    "WEEKISO", "DAY", "DAYOFMONTH", "DAYOFWEEK", "DAYOFWEEKISO", "DAYOFYEAR",
    "HOUR", "MINUTE", "SECOND", "LAST_DAY", "NEXT_DAY", "PREVIOUS_DAY",
    "MONTHNAME", "DAYNAME", "ADD_MONTHS", "MONTHS_BETWEEN",
    "DATE_TRUNC", "TIME_SLICE", "DATE_PART", "DATEADD", "TIMEADD", "TIMESTAMPADD",
    "DATEDIFF", "TIMEDIFF", "TIMESTAMPDIFF", "EXTRACT",
)
_FUNC_RE = re.compile(r"\b(" + "|".join(DATE_FUNCTIONS) + r")\s*\(", re.IGNORECASE)

TEXT_TYPES = ("TEXT", "VARCHAR", "STRING", "CHAR", "CHARACTER", "NVARCHAR", "NCHAR")

# One TABLES-clause entry: ALIAS AS "DB"."SCHEMA"."TABLE" (identifiers may be unquoted).
_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][A-Za-z0-9_$]*)'
_TABLE_ENTRY_RE = re.compile(
    rf"^\s*({_IDENT})\s+AS\s+((?:{_IDENT}\s*\.\s*){{0,2}}{_IDENT})",
    re.IGNORECASE | re.MULTILINE,
)

_WRAP = "TRY_TO_TIMESTAMP"


def _unquote(ident: str) -> str:
    ident = ident.strip()
    if ident.startswith('"') and ident.endswith('"'):
        return ident[1:-1].replace('""', '"')
    return ident.upper()


def is_text_type(data_type: str) -> bool:
    t = str(data_type or "").upper()
    return any(t == x or t.startswith(x + "(") for x in TEXT_TYPES)


def _tables_clause(ddl: str) -> str:
    """Body of the TABLES ( ... ) clause, or '' when absent."""
    m = re.search(r"\bTABLES\s*\(", ddl, re.IGNORECASE)
    if not m:
        return ""
    end = _matching_paren(ddl, m.end() - 1)
    return ddl[m.end(): end] if end > 0 else ""


def _matching_paren(text: str, open_idx: int) -> int:
    """Index of the ')' closing the '(' at open_idx; -1 if unbalanced. Skips string literals."""
    depth = 0
    i = open_idx
    in_str = False
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _literal_spans(text: str) -> List[Tuple[int, int]]:
    """[start, end) of every '...' string literal ('' is an escaped quote)."""
    spans: List[Tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            spans.append((i, j + 1))
            i = j + 1
        else:
            i += 1
    return spans


def alias_tables(ddl: str) -> Dict[str, str]:
    """Logical table alias -> physical table name (unquoted, as in information_schema)."""
    out: Dict[str, str] = {}
    for m in _TABLE_ENTRY_RE.finditer(_tables_clause(ddl)):
        alias = _unquote(m.group(1))
        parts = re.findall(_IDENT, m.group(2))
        if parts:
            out[alias.upper()] = _unquote(parts[-1]).upper()
    return out


def text_columns_by_alias(
    ddl: str, column_types: Mapping[str, Mapping[str, str]]
) -> Dict[str, Set[str]]:
    """Alias -> {COLUMN} whose live type is text, for the tables this DDL binds to."""
    types = {str(t).upper(): {str(c).upper(): v for c, v in cols.items()} for t, cols in (column_types or {}).items()}
    out: Dict[str, Set[str]] = {}
    for alias, table in alias_tables(ddl).items():
        cols = {c for c, t in types.get(table, {}).items() if is_text_type(t)}
        if cols:
            out[alias] = cols
    return out


def coerce_text_date_columns(
    ddl: str, column_types: Mapping[str, Mapping[str, str]]
) -> Tuple[str, List[Tuple[str, str]]]:
    """Wrap text columns used inside date functions in TRY_TO_TIMESTAMP().

    Returns (ddl, [(alias, column), ...] that were wrapped). The DDL is
    returned unchanged when no table has a text column used as a date.
    """
    text_cols = text_columns_by_alias(ddl, column_types)
    if not ddl or not text_cols:
        return ddl, []

    alias_pattern = "|".join(re.escape(a) for a in sorted(text_cols, key=len, reverse=True))
    ref_re = re.compile(rf'(?<![\w."])("?)({alias_pattern})\1\s*\.\s*"([^"]+)"', re.IGNORECASE)

    literals = _literal_spans(ddl)

    def in_literal(pos: int) -> bool:
        return any(s <= pos < e for s, e in literals)

    spans: List[Tuple[int, int]] = []
    for m in _FUNC_RE.finditer(ddl):
        if in_literal(m.start()):
            continue
        close = _matching_paren(ddl, m.end() - 1)
        if close > 0:
            spans.append((m.end(), close))
    if not spans:
        return ddl, []

    def in_span(pos: int) -> bool:
        return any(s <= pos < e for s, e in spans)

    wrapped: List[Tuple[str, str]] = []
    pieces: List[str] = []
    last = 0
    for m in ref_re.finditer(ddl):
        alias, col = m.group(2).upper(), m.group(3)
        if col.upper() not in text_cols.get(alias, set()) or not in_span(m.start()) or in_literal(m.start()):
            continue
        before = ddl[max(0, m.start() - len(_WRAP) - 2): m.start()].upper().replace(" ", "")
        if before.endswith(_WRAP + "("):
            continue  # already coerced
        pieces.append(ddl[last: m.start()])
        pieces.append(f"{_WRAP}({m.group(0)})")
        last = m.end()
        if (alias, col) not in wrapped:
            wrapped.append((alias, col))
    if not wrapped:
        return ddl, []
    pieces.append(ddl[last:])
    return "".join(pieces), wrapped


def coerce_ddls(
    ddls: Iterable[str], column_types: Mapping[str, Mapping[str, str]]
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """coerce_text_date_columns over several statements."""
    out: List[str] = []
    all_wrapped: List[Tuple[str, str]] = []
    for ddl in ddls:
        new, wrapped = coerce_text_date_columns(ddl, column_types)
        out.append(new)
        all_wrapped.extend(w for w in wrapped if w not in all_wrapped)
    return out, all_wrapped
