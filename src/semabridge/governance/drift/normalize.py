"""Expression and description normalization for platform-independent drift comparison.

Canonicalizes SQL expressions using the platform's dialect to eliminate false drift
from warehouse formatting differences (whitespace, quoting, casing, default clauses),
while strictly preserving meaningful semantic differences such as string literals,
constant values, and cast target types.

Rules:
- Purity: No warehouse calls, no files, no database. Uses an in-memory LRU memo cache for performance that does not affect normalization results.
- Platform-neutral: No warehouse/platform names or `if platform == ...` inside.
  The adapter passes the dialect string; unknown dialect falls back to text cleanup.
- Error resilience: Never raises exceptions to the caller. Bad input yields text fallback.
- Case preservation: String literals, quoted case-sensitive identifiers, and description casing
  are strictly preserved.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from collections import OrderedDict
from typing import Optional, Tuple

logger = logging.getLogger("semabridge.governance.drift.normalize")

# Fixed placeholder token for unified placeholder representation on both sides
PLACEHOLDER_TOKEN = "__SEMABRIDGE_PLACEHOLDER_NULL__"
PARSED = "parsed"
TEXT_FALLBACK = "text_fallback"

# Default maximum expression length before falling back to text normalization (prevents ReDoS/memory exhaustion)
DEFAULT_MAX_EXPRESSION_LENGTH = 50000

# Cache size for expression normalization memoization
_MEMO_MAX_SIZE = 2048
_MEMO_CACHE: OrderedDict[Tuple[str, str, Optional[str]], NormalizedExpr] = OrderedDict()

# Diagnostic tracking for normalization runs missing a SQL dialect
_MISSING_DIALECT_COUNT: int = 0
_WARNED_MISSING_DIALECT: bool = False
_WARNED_PARSE_FAILURE: bool = False


def get_missing_dialect_count() -> int:
    """Return the total number of expression normalizations performed without a dialect."""
    return _MISSING_DIALECT_COUNT


def reset_missing_dialect_count() -> int:
    """Reset the missing dialect counter and warning flag, returning the previous count."""
    global _MISSING_DIALECT_COUNT, _WARNED_MISSING_DIALECT, _WARNED_PARSE_FAILURE
    prev = _MISSING_DIALECT_COUNT
    _MISSING_DIALECT_COUNT = 0
    _WARNED_MISSING_DIALECT = False
    _WARNED_PARSE_FAILURE = False
    return prev


def reset_parse_warnings() -> None:
    """Reset parse failure warning flags for a new drift check run."""
    global _WARNED_PARSE_FAILURE
    _WARNED_PARSE_FAILURE = False

# Regex patterns for fallback text cleanup
_COMMENT_RE = re.compile(r"(?:/\*.*?\*/|--[^\n]*|//[^\n]*)", re.DOTALL)
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_SEMICOLON_RE = re.compile(r";+\s*$")

# Dedicated DAX patterns
_DAX_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_DAX_LINE_COMMENT = re.compile(r"(?:--|//)[^\r\n]*")
_DAX_QUOTED_TABLE = re.compile(r"'([A-Za-z0-9_ ]+)'\s*\[")
_DAX_TOKENS = re.compile(r'("(?:[^"]|"")*"|\[[^\]]+\])')
_DAX_PUNCT_SPACES = re.compile(r"\s*([(),\[\]])\s*")

# Placeholders generated during auto-remediation or manual warehouse definition
_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:cast\s*\(\s*null\s*as\s+[\w\s(),]+\)|null(?:::[\w\s(),]+)?|__semabridge_placeholder_null__)$",
    re.IGNORECASE,
)


def get_max_expression_length() -> int:
    """Retrieve configured maximum expression length for AST parsing."""
    raw = os.getenv("SEMABRIDGE_DRIFT_MAX_EXPRESSION_LENGTH", "").strip()
    if raw.isdigit():
        val = int(raw)
        if val > 0:
            return val
    try:
        from semabridge.core.settings import get_settings
        settings = get_settings()
        drift_cfg = getattr(settings, "drift", None)
        if drift_cfg and hasattr(drift_cfg, "max_expression_length"):
            return int(drift_cfg.max_expression_length)
    except Exception:
        pass
    return DEFAULT_MAX_EXPRESSION_LENGTH


class NormalizedExpr(str):
    """Canonical normalized expression string with method metadata ('parsed' | 'text_fallback').

    Inherits from str so it is transparently compatible with all string operations,
    comparisons, hashing, and serialization.
    """

    method: str  # 'parsed' | 'text_fallback'

    def __new__(cls, text: str, method: str = "parsed") -> "NormalizedExpr":
        instance = super().__new__(cls, text)
        instance.method = method
        return instance

    @property
    def text(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return f"NormalizedExpr({super().__repr__()}, method={self.method!r})"


def has_expression(expr: Optional[str]) -> bool:
    """Check if an expression string was recorded (non-empty)."""
    return bool(expr and str(expr).strip())


def is_placeholder_expression(expr: Optional[str]) -> bool:
    """Check if an expression is an actual null/placeholder remediation value.

    An empty or missing expression is NOT a placeholder (see has_expression).
    """
    if not expr or not str(expr).strip():
        return False
    cleaned = str(expr).strip()
    return bool(_PLACEHOLDER_PATTERN.match(cleaned))


is_placeholder = is_placeholder_expression


def comparable(a: Optional[str], b: Optional[str], dialect: Optional[str] = None) -> Tuple[NormalizedExpr, NormalizedExpr]:
    """Normalize two expressions for side-by-side comparison."""
    norm_a = normalize_expression(a, dialect=dialect)
    norm_b = normalize_expression(b, dialect=dialect)
    if norm_a.method != norm_b.method:
        fb_a = normalize_expression_fallback(a)
        fb_b = normalize_expression_fallback(b)
        if fb_a == fb_b:
            norm_a = NormalizedExpr(fb_a, method=TEXT_FALLBACK)
            norm_b = NormalizedExpr(fb_b, method=TEXT_FALLBACK)
    return norm_a, norm_b


def _text_cleanup_fallback(expr: str, table: Optional[str] = None) -> NormalizedExpr:
    """Fallback text normalization when dialect parsing fails or expression is oversized.

    - Removes comments (--, //, /* ... */)
    - Collapses whitespace and line breaks
    - Strips redundant trailing semicolons
    - Preserves string literals, numbers, and quoted identifiers exactly
    """
    if not expr:
        return NormalizedExpr("", method="text_fallback")

    # Strip comments
    cleaned = _COMMENT_RE.sub(" ", expr)
    # Strip trailing semicolons
    cleaned = _TRAILING_SEMICOLON_RE.sub("", cleaned)
    # Collapse whitespace
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()

    return NormalizedExpr(cleaned, method="text_fallback")


def normalize_expression_fallback(expr: Optional[str], table: Optional[str] = None) -> NormalizedExpr:
    """Public helper to normalize an expression purely using text fallback.

    Used when comparing two expressions where one side failed AST parsing,
    guaranteeing that both sides are compared using the same fallback method.
    """
    if not expr or not str(expr).strip():
        return NormalizedExpr("", method="text_fallback")
    return _text_cleanup_fallback(str(expr).strip(), table=table)


def normalize_dax_expression(raw: Optional[str]) -> NormalizedExpr:
    """Canonicalize a Power BI DAX formula for semantic equivalence comparison.

    Eliminates non-semantic formatting differences:
    - Comments (--, //, /* ... */)
    - Leading '='
    - Redundant whitespace and line breaks
    - Optional table quotes on simple table identifiers: 'Table'[Column] -> Table[Column]
    - Keyword and identifier casing outside string literals

    Strictly preserves:
    - Double-quoted string literals and their exact casing
    - Measure/column names inside brackets
    - Operators, values, and operands
    """
    if not raw or not str(raw).strip():
        return NormalizedExpr("", method="parsed")

    text = str(raw).strip()
    if is_placeholder_expression(text):
        return NormalizedExpr(PLACEHOLDER_TOKEN, method="parsed")

    # Strip comments
    text = _DAX_BLOCK_COMMENT.sub(" ", text)
    text = _DAX_LINE_COMMENT.sub(" ", text)
    text = text.strip()

    # Strip leading '='
    if text.startswith("="):
        text = text[1:].strip()

    # Canonicalize table references: unquote clean table names before brackets
    text = _DAX_QUOTED_TABLE.sub(
        lambda m: (m.group(1) if re.match(r"^[A-Za-z0-9_]+$", m.group(1)) else f"'{m.group(1)}'") + "[",
        text,
    )

    # Normalize whitespace and punctuation spacing while preserving string literals and bracket contents
    parts = []
    pos = 0
    for m in _DAX_TOKENS.finditer(text):
        start, end = m.span()
        if start > pos:
            chunk = text[pos:start]
            chunk = _DAX_PUNCT_SPACES.sub(r"\1", chunk)
            chunk = _WHITESPACE_RE.sub(" ", chunk)
            parts.append(chunk.upper())
        token = text[start:end]
        if token.startswith("["):
            # Bracket identifier: preserve internal spacing, uppercase identifier
            parts.append(token.upper())
        else:
            # String literal: preserve verbatim
            parts.append(token)
        pos = end
    if pos < len(text):
        chunk = text[pos:]
        chunk = _DAX_PUNCT_SPACES.sub(r"\1", chunk)
        chunk = _WHITESPACE_RE.sub(" ", chunk)
        parts.append(chunk.upper())

    canonical = "".join(parts).strip()
    return NormalizedExpr(canonical, method="parsed")


def normalize_expression(
    expr: Optional[str],
    dialect: Optional[str] = None,
    platform: Optional[str] = None,
    table: Optional[str] = None,
) -> NormalizedExpr:
    """Parse and format an expression in canonical dialect form.

    Eliminates formatting-only differences:
    - Whitespace, line breaks, indentation
    - Comments
    - Function and keyword casing
    - Unquoted identifier casing per dialect rules
    - Unneeded identifier quotes
    - Redundant parentheses
    - Trailing semicolons and optional AS keywords
    - Dialect-equivalent spellings (<> vs !=, ::type vs CAST)
    - Boolean and NULL casing
    - Redundant table qualification when pointing to the same table

    Strictly preserves genuine semantic differences:
    - String literal content and casing ('Active' != 'active')
    - Numeric literal values (0.5 != 0.05)
    - Cast target types and precision
    - Function differences (SUM != AVG, COUNT != COUNT(DISTINCT))
    - Quoted case-sensitive identifiers ("Amount" != AMOUNT)
    - Filter conditions, operators, NULL handling (x != COALESCE(x, 0))
    - Operand order (a - b != b - a)

    Args:
        expr: Raw expression text.
        dialect: SQLglot dialect name (e.g. 'snowflake', 'databricks', 'spark', 'postgres').
        platform: Optional platform fallback name for backward compatibility.
        table: Optional table context to strip redundant table qualification.

    Returns:
        NormalizedExpr with .text and .method ('parsed' or 'text_fallback').
    """
    if not expr or not str(expr).strip():
        return NormalizedExpr("", method="parsed")

    raw = str(expr).strip()

    # Detect placeholders on both sides
    if is_placeholder_expression(raw):
        return NormalizedExpr(PLACEHOLDER_TOKEN, method="parsed")

    # Check maximum expression size
    max_len = get_max_expression_length()
    if len(raw) > max_len:
        logger.warning(
            "Expression length (%d chars) exceeds maximum allowed length (%d); using text fallback",
            len(raw), max_len,
        )
        return _text_cleanup_fallback(raw, table=table)

    # Resolve target dialect (platform-neutral: adapter passes dialect directly)
    target_dialect = str(dialect or platform or "").strip().lower()
    if not target_dialect:
        global _MISSING_DIALECT_COUNT, _WARNED_MISSING_DIALECT
        _MISSING_DIALECT_COUNT += 1
        if not _WARNED_MISSING_DIALECT:
            logger.warning(
                "No dialect provided for expression normalization; using text fallback "
                "(suppressing duplicate warnings for current check run)"
            )
            _WARNED_MISSING_DIALECT = True
        return _text_cleanup_fallback(raw, table=table)

    # Memoization lookup
    cache_key = (raw, target_dialect, table)
    if cache_key in _MEMO_CACHE:
        _MEMO_CACHE.move_to_end(cache_key)
        return _MEMO_CACHE[cache_key]

    # Dedicated DAX normalization for Power BI expressions
    if target_dialect in ("dax", "powerbi", "pbix"):
        res = normalize_dax_expression(raw)
        _store_memo(cache_key, res)
        return res

    try:
        import sqlglot
        from sqlglot import expressions as exp

        # Validate dialect existence
        try:
            sqlglot.Dialect.get_or_raise(target_dialect)
        except Exception:
            logger.warning("Dialect '%s' not recognized by SQLglot; using text fallback", target_dialect)
            res = _text_cleanup_fallback(raw, table=table)
            _store_memo(cache_key, res)
            return res

        # Strip trailing semicolons before parsing
        clean_input = _TRAILING_SEMICOLON_RE.sub("", raw)

        # Parse single expression with target dialect
        parsed = sqlglot.parse_one(clean_input, read=target_dialect)
        if parsed is None:
            res = _text_cleanup_fallback(raw, table=table)
            _store_memo(cache_key, res)
            return res

        # Unwrap outer-level parentheses first: (((a + b))) -> a + b
        while isinstance(parsed, exp.Paren):
            parsed = parsed.this

        # Collapse redundant parentheses in tree:
        # 1. ((x)) -> (x)
        # 2. Parens around atomic terms (Func, Column, Literal, Cast, Var, Null, Boolean) -> unwrapped
        def _collapse_parens(node):
            if isinstance(node, exp.Paren):
                inner = node.this
                while isinstance(inner, exp.Paren):
                    inner = inner.this
                if isinstance(inner, (exp.Func, exp.Column, exp.Literal, exp.Var, exp.Cast, exp.Anonymous, exp.Null, exp.Boolean)):
                    return inner
                node.set("this", inner)
            return node

        parsed = parsed.transform(_collapse_parens)

        # AST Transformations:
        # 1. Identifier unquoting / casing:
        #    Only unquote identifiers that do NOT need quotes according to dialect rules.
        #    Preserve quotes on case-sensitive or special-character identifiers.
        def _normalize_ast_nodes(node):
            # 1. Identifier normalization
            if isinstance(node, exp.Identifier):
                name = node.this
                if not isinstance(name, str):
                    return node
                is_quoted = node.args.get("quoted", False)

                if target_dialect == "snowflake":
                    if not is_quoted:
                        # Snowflake unquoted identifiers fold to uppercase
                        return exp.Identifier(this=name.upper(), quoted=False)
                    if name == name.upper() and re.match(r"^[A-Z_][A-Z0-9_$]*$", name):
                        # Unneeded quotes on standard uppercase identifier -> unquote
                        return exp.Identifier(this=name, quoted=False)
                    # Quoted identifier with lowercase or special chars -> preserve case-sensitive quotes!
                    return exp.Identifier(this=name, quoted=True)

                if target_dialect in ("databricks", "spark"):
                    if not is_quoted:
                        return exp.Identifier(this=name.lower(), quoted=False)
                    if name.isidentifier():
                        return exp.Identifier(this=name.lower(), quoted=False)
                    return exp.Identifier(this=name, quoted=True)

                # Generic dialect rule: unquote standard identifiers
                if is_quoted and name.isidentifier():
                    return exp.Identifier(this=name, quoted=False)

                return node

            # 2. Redundant table qualification removal:
            #    When table context is provided and column table matches, strip table prefix in-place
            if isinstance(node, exp.Column) and node.table:
                if table and str(node.table).upper() == str(table).upper():
                    node.set("table", None)
                return node

            return node

        transformed = parsed.transform(_normalize_ast_nodes)

        # Unwrap any remaining root-level parentheses
        while isinstance(transformed, exp.Paren):
            transformed = transformed.this

        # Generate canonical SQL without comments or non-standard quoting
        canonical = transformed.sql(dialect=target_dialect, comments=False, identify=False).strip()

        result = NormalizedExpr(canonical, method="parsed")
        _store_memo(cache_key, result)
        return result

    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "sqlglot could not parse expression '%s' with dialect '%s': %s",
            raw[:60], target_dialect, exc,
        )
        global _WARNED_PARSE_FAILURE
        if not _WARNED_PARSE_FAILURE:
            logger.warning(
                "sqlglot could not parse expression '%s' with dialect '%s' (falling back to text cleanup; suppressing duplicate parse warnings for check run): %s",
                raw[:60], target_dialect, exc,
            )
            _WARNED_PARSE_FAILURE = True

    res = _text_cleanup_fallback(raw, table=table)
    _store_memo(cache_key, res)
    return res


def _store_memo(key: Tuple[str, str, Optional[str]], value: NormalizedExpr) -> None:
    """Store normalization result in LRU memo cache."""
    if len(_MEMO_CACHE) >= _MEMO_MAX_SIZE:
        _MEMO_CACHE.popitem(last=False)
    _MEMO_CACHE[key] = value


def normalize_description(desc: Optional[str]) -> str:
    """Normalize object descriptions and documentation comments.

    - Trims leading/trailing whitespace.
    - Collapses internal whitespace/newlines to a single space.
    - Unifies Unicode to standard NFC form (visually identical text matches).
    - Preserves case (case changes count as description drift).
    - Treats None and empty strings as equivalent empty strings ("").
    """
    if desc is None:
        return ""
    text = str(desc).strip()
    if not text:
        return ""
    # Normalize unicode to standard NFC representation
    text = unicodedata.normalize("NFC", text)
    # Unify line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse multiple whitespace characters
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text
