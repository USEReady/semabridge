"""Reference normalisation.

Removes what cannot change meaning (spacing, comments, identifier case and
redundant quoting, optional keywords) and keeps what can (literals, cast types,
functions, columns, operand order). Dialect comes from the caller, never from a
platform check inside this module.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

PLACEHOLDER = "__PLACEHOLDER_NULL__"
MAX_EXPRESSION_LENGTH = 50_000  # caller may override; configuration in the product

PARSED = "parsed"
TEXT_FALLBACK = "text_fallback"

_COMMENTS = re.compile(r"/\*.*?\*/|--[^\r\n]*", re.S)
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:NULL|CAST\s*\(\s*NULL\s+AS\s+[A-Za-z0-9_]+(?:\s*\([^)]*\))?\s*\)"
    r"|NULL::[A-Za-z0-9_]+(?:\s*\([^)]*\))?)\s*$",
    re.I,
)


@dataclass(frozen=True)
class NormalizedExpr:
    text: str
    method: str

    def __eq__(self, other) -> bool:  # compares by text, so tests read naturally
        if isinstance(other, NormalizedExpr):
            return self.text == other.text
        return self.text == other

    def __hash__(self) -> int:
        return hash(self.text)


def _text_fallback(raw: str) -> str:
    cleaned = _COMMENTS.sub(" ", raw)
    return re.sub(r"\s+", " ", cleaned).strip()


def is_placeholder(raw: Optional[str]) -> bool:
    return bool(raw) and bool(_PLACEHOLDER_RE.match(_COMMENTS.sub(" ", str(raw)).strip()))


def normalize_expression(raw: Optional[str], dialect: Optional[str],
                         max_length: int = MAX_EXPRESSION_LENGTH) -> NormalizedExpr:
    """Canonical form of one expression. Never raises."""
    if raw is None or not str(raw).strip():
        return NormalizedExpr("", PARSED)
    text = str(raw)
    if is_placeholder(text):
        return NormalizedExpr(PLACEHOLDER, PARSED)
    if len(text) > max_length:
        logger.info("Expression above the size limit; using the text fallback")
        return NormalizedExpr(_text_fallback(text), TEXT_FALLBACK)
    if not dialect:
        logger.info("No dialect given; using the text fallback")
        return NormalizedExpr(_text_fallback(text), TEXT_FALLBACK)
    try:
        sqlglot.Dialect.get_or_raise(dialect)
    except Exception as exc:  # noqa: BLE001 - unknown dialect: fall back, never fail
        logger.info("Unknown dialect %s (%s); using the text fallback", dialect, exc)
        return NormalizedExpr(_text_fallback(text), TEXT_FALLBACK)
    try:
        tree = sqlglot.parse_one(_COMMENTS.sub(" ", text), read=dialect)
        if tree is None:
            raise ValueError("no expression parsed")
        tree = _unwrap_redundant_parens(tree)
        tree = _unquote_plain_identifiers(tree, dialect)
        rendered = tree.sql(dialect=dialect, normalize=True, identify=False, comments=False)
        return NormalizedExpr(re.sub(r"\s+", " ", rendered).strip(), PARSED)
    except Exception as exc:  # noqa: BLE001 - unparseable: fall back, never fail
        logger.info("Expression not parseable (%s); using the text fallback", exc)
        return NormalizedExpr(_text_fallback(text), TEXT_FALLBACK)


_PLAIN_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _unquote_plain_identifiers(node: exp.Expression, dialect: str) -> exp.Expression:
    """Drop quotes the dialect does not need.

    An identifier quoted only to be safe is the same object as the unquoted one,
    but an identifier whose quoting preserves its case is a different object, so
    that one keeps its quotes.
    """
    # Where quoting preserves case, a mixed case quoted name is its own object;
    # where identifiers are case insensitive, quoting is only punctuation.
    case_sensitive_when_quoted = dialect in ("snowflake",)
    for ident in node.find_all(exp.Identifier):
        if not ident.quoted:
            continue
        name = ident.name
        if not _PLAIN_IDENT.match(name):
            continue
        if case_sensitive_when_quoted and name != name.upper():
            continue  # quoting is what keeps this name mixed case
        ident.set("quoted", False)
    return node


def _unwrap_redundant_parens(node: exp.Expression) -> exp.Expression:
    """Drop brackets that wrap another bracket or a single term."""
    def walk(n: exp.Expression) -> exp.Expression:
        if isinstance(n, exp.Paren):
            inner = walk(n.this)
            if isinstance(inner, (exp.Paren, exp.Column, exp.Literal, exp.Func, exp.Cast)):
                return inner
            if n.parent is None or isinstance(n.parent, (exp.Paren, exp.Alias)):
                return inner  # a bracket around the whole expression changes nothing
            n.set("this", inner)
            return n
        for key, value in list(n.args.items()):
            if isinstance(value, exp.Expression):
                n.set(key, walk(value))
            elif isinstance(value, list):
                n.set(key, [walk(v) if isinstance(v, exp.Expression) else v for v in value])
        return n

    return walk(node)


def normalize_description(raw: Optional[str]) -> str:
    """Trim, collapse whitespace, unify line endings and unicode form. Case kept."""
    if raw is None:
        return ""
    text = unicodedata.normalize("NFC", str(raw)).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def comparable(a: Optional[str], b: Optional[str], dialect: Optional[str]):
    """Both sides normalised the same way.

    When one side parses and the other does not, both drop to the text fallback,
    so a parsing difference can never show up as drift.
    """
    left = normalize_expression(a, dialect)
    right = normalize_expression(b, dialect)
    if left.method != right.method:
        return (NormalizedExpr(_text_fallback(str(a or "")), TEXT_FALLBACK),
                NormalizedExpr(_text_fallback(str(b or "")), TEXT_FALLBACK))
    return left, right
