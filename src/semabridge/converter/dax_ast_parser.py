"""
DAX Abstract Syntax Tree (AST) Parser.

Implements a recursive-descent tokenizer and parser for DAX (Data Analysis Expressions),
producing a typed AST that can be traversed to generate Snowflake-compatible SQL.

Architecture
------------
Tier 1-2 (simple aggregations and arithmetic)  → fast-path regex in DAXTranslator (unchanged).
Tier 3   (time intelligence)                   → AST parse → SQL window function rendering.
Tier 4   (CALCULATE / FILTER / ALL / ALLEXCEPT) → AST parse → SQL subquery / WHERE rendering.

The Snowflake SQL renderer is deterministic; no LLM calls occur at runtime.

Usage
-----
    from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer

    parser = DaxAstParser()
    ast = parser.parse("CALCULATE(SUM('Sales'[Amount]), FILTER(ALL('Date'), 'Date'[Year] = 2024))")
    renderer = DaxSqlRenderer(table_alias="salesfact", date_alias="l_date")
    sql = renderer.render(ast)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union

from semabridge.utils.logger import get_logger
from semabridge.converter.function_registry import FunctionRegistry
from semabridge.utils.relationship_graph import has_relationship_path

# Cached value is (sql, advisory_categories) rather than a bare sql string
# so a cache HIT still replays whatever advisory categories the render that
# first produced this SQL recorded (e.g.
# ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK) — a hit that returned
# just the sql string would silently drop that flag for every call after
# the first, since the renderer (and its _advisory_categories list) never
# runs again on a cache hit.
_AST_CACHE: dict[tuple, Tuple[Optional[str], Tuple[str, ...]]] = {}
_AST_CACHE_MAX = 1024


def _ast_cache_key(
    dax: str,
    table_alias: str,
    date_alias: str,
    measure_sql_map: Optional[Dict[str, str]],
    known_measure_names: Optional[Any] = None,
    anchor_flag_map: Optional[Dict[Tuple[str, ...], str]] = None,
    primary_table_name: Optional[str] = None,
    date_table_names: Optional[Any] = None,
    allow_unshifted_fallback: bool = False,
) -> tuple:
    items = tuple(sorted((measure_sql_map or {}).items()))
    names = tuple(sorted(known_measure_names or ()))
    flags = tuple(sorted((anchor_flag_map or {}).items()))
    date_tables = tuple(sorted(str(n).casefold() for n in (date_table_names or ())))
    return (
        dax or "",
        table_alias or "",
        date_alias or "",
        items,
        names,
        flags,
        primary_table_name or "",
        date_tables,
        allow_unshifted_fallback,
    )
from semabridge.utils.naming import sanitize_column

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Token Layer
# ---------------------------------------------------------------------------


class DaxTokenType(Enum):
    # Literals
    NUMBER = auto()
    STRING = auto()
    # Identifiers
    COLUMN_REF = auto()    # 'TableName'[ColumnName]
    MEASURE_REF = auto()   # [MeasureName]
    FUNC_NAME = auto()     # SUM, CALCULATE, FILTER, etc.
    IDENTIFIER = auto()    # bare word
    # Operators
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    SLASH = auto()
    EQ = auto()
    NEQ = auto()
    LT = auto()
    LTE = auto()
    GT = auto()
    GTE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    # Delimiters
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    # Special
    EOF = auto()
    UNKNOWN = auto()


@dataclass
class DaxToken:
    type: DaxTokenType
    value: str
    pos: int = 0


# Known DAX function names (lower-cased for matching)
_DAX_FUNCTIONS = {
    "sum", "average", "min", "max", "count", "distinctcount", "counta",
    "countrows", "sumx", "averagex", "minx", "maxx", "countx",
    "calculate", "calculatetable", "filter", "all", "allexcept", "allselected",
    "values", "distinct", "hasonevalue", "selectedvalue",
    "related", "relatedtable", "userelationship", "crossfilter",
    "if", "switch", "iferror", "isblank", "isfiltered", "hasonefilter",
    "divide", "round", "roundup", "rounddown", "int", "abs", "ceiling", "floor",
    "totalytd", "totalmtd", "totalqtd",
    "sameperiodlastyear", "previousyear", "previousmonth", "previousquarter",
    "dateadd", "datesytd", "datesmtd", "datesqtd",
    "parallelperiod", "openingbalanceyear", "closingbalanceyear",
    "rankx", "earlier", "earliest", "lookupvalue",
    "concatenate", "format", "left", "right", "mid", "len", "trim",
    "year", "month", "day", "hour", "minute", "second",
    "date", "now", "today", "eomonth",
    "and", "or", "not",
    "summarizecolumns", "summarize", "groupby", "addcolumns",
}


class DaxLexer:
    """Tokenises a DAX expression string into a list of DaxToken objects."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.tokens: List[DaxToken] = []

    def tokenize(self) -> List[DaxToken]:
        while self.pos < len(self.text):
            self._skip_whitespace()
            if self.pos >= len(self.text):
                break
            ch = self.text[self.pos]

            # Column reference: 'Table'[Column] or just [Column]
            if ch == "'":
                tok = self._read_column_ref()
            elif ch == "[":
                tok = self._read_measure_ref()
            elif ch == '"':
                tok = self._read_string('"')
            elif ch.isdigit() or (ch == "-" and self._peek_next_is_digit()):
                tok = self._read_number()
            elif ch.isalpha() or ch == "_":
                tok = self._read_identifier()
            else:
                tok = self._read_operator()

            self.tokens.append(tok)

        self.tokens.append(DaxToken(DaxTokenType.EOF, "", self.pos))
        return self.tokens

    # ------------------------------------------------------------------
    def _skip_whitespace(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek_next_is_digit(self) -> bool:
        nxt = self.pos + 1
        return nxt < len(self.text) and self.text[nxt].isdigit()

    def _read_column_ref(self) -> DaxToken:
        start = self.pos
        # Consume the table name in single quotes
        self.pos += 1  # skip opening '
        while self.pos < len(self.text) and self.text[self.pos] != "'":
            self.pos += 1
        self.pos += 1  # skip closing '
        # Now consume [ColumnName]
        col_name = ""
        if self.pos < len(self.text) and self.text[self.pos] == "[":
            self.pos += 1  # skip [
            while self.pos < len(self.text) and self.text[self.pos] != "]":
                col_name += self.text[self.pos]
                self.pos += 1
            self.pos += 1  # skip ]
        raw = self.text[start : self.pos]
        return DaxToken(DaxTokenType.COLUMN_REF, raw, start)

    def _read_measure_ref(self) -> DaxToken:
        start = self.pos
        self.pos += 1  # skip [
        val = ""
        while self.pos < len(self.text) and self.text[self.pos] != "]":
            val += self.text[self.pos]
            self.pos += 1
        self.pos += 1  # skip ]
        return DaxToken(DaxTokenType.MEASURE_REF, f"[{val}]", start)

    def _read_string(self, quote: str) -> DaxToken:
        start = self.pos
        self.pos += 1  # skip opening quote
        val = ""
        while self.pos < len(self.text) and self.text[self.pos] != quote:
            val += self.text[self.pos]
            self.pos += 1
        self.pos += 1  # skip closing quote
        return DaxToken(DaxTokenType.STRING, val, start)

    def _read_number(self) -> DaxToken:
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] in "."):
            self.pos += 1
        return DaxToken(DaxTokenType.NUMBER, self.text[start : self.pos], start)

    def _read_identifier(self) -> DaxToken:
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_"):
            self.pos += 1
        word = self.text[start : self.pos]
        word_lower = word.lower()
        if word_lower in _DAX_FUNCTIONS:
            return DaxToken(DaxTokenType.FUNC_NAME, word, start)
        # Boolean / logical keywords
        if word_lower == "and":
            return DaxToken(DaxTokenType.AND, word, start)
        if word_lower == "or":
            return DaxToken(DaxTokenType.OR, word, start)
        if word_lower == "not":
            return DaxToken(DaxTokenType.NOT, word, start)
        if word_lower in ("true", "false"):
            return DaxToken(DaxTokenType.NUMBER, word, start)
        return DaxToken(DaxTokenType.IDENTIFIER, word, start)

    def _read_operator(self) -> DaxToken:
        start = self.pos
        ch = self.text[self.pos]
        nxt = self.text[self.pos + 1] if self.pos + 1 < len(self.text) else ""

        two = ch + nxt
        one_map = {
            "+": DaxTokenType.PLUS,
            "-": DaxTokenType.MINUS,
            "*": DaxTokenType.STAR,
            "/": DaxTokenType.SLASH,
            "(": DaxTokenType.LPAREN,
            ")": DaxTokenType.RPAREN,
            ",": DaxTokenType.COMMA,
            "=": DaxTokenType.EQ,
            "<": DaxTokenType.LT,
            ">": DaxTokenType.GT,
        }
        two_map = {
            "<>": DaxTokenType.NEQ,
            "<=": DaxTokenType.LTE,
            ">=": DaxTokenType.GTE,
            "&&": DaxTokenType.AND,
            "||": DaxTokenType.OR,
        }

        if two in two_map:
            self.pos += 2
            return DaxToken(two_map[two], two, start)

        if ch in one_map:
            self.pos += 1
            return DaxToken(one_map[ch], ch, start)

        self.pos += 1
        return DaxToken(DaxTokenType.UNKNOWN, ch, start)


# ---------------------------------------------------------------------------
# AST Nodes
# ---------------------------------------------------------------------------


@dataclass
class DaxNode:
    """Base AST node."""
    pass


@dataclass
class LiteralNode(DaxNode):
    value: Union[str, float, int, bool]
    raw: str = ""


@dataclass
class ColumnRefNode(DaxNode):
    """'TableName'[ColumnName] reference."""
    table: str
    column: str
    raw: str = ""


@dataclass
class MeasureRefNode(DaxNode):
    """[MeasureName] reference."""
    name: str


@dataclass
class IdentifierNode(DaxNode):
    name: str


@dataclass
class FunctionCallNode(DaxNode):
    func: str  # upper-cased function name
    args: List[DaxNode] = field(default_factory=list)


@dataclass
class BinaryOpNode(DaxNode):
    op: str  # +, -, *, /, =, <>, <, <=, >, >=, AND, OR
    left: DaxNode = field(default_factory=lambda: LiteralNode(0))
    right: DaxNode = field(default_factory=lambda: LiteralNode(0))


@dataclass
class UnaryOpNode(DaxNode):
    op: str
    operand: DaxNode = field(default_factory=lambda: LiteralNode(0))


# ---------------------------------------------------------------------------
# Recursive-Descent Parser
# ---------------------------------------------------------------------------


class DaxParseError(Exception):
    pass


class DaxAstParser:
    """
    Recursive-descent parser for DAX expressions.

    Produces a typed AST from a DAX string.  The AST can then be
    passed to DaxSqlRenderer to generate Snowflake SQL.

    Supported patterns (Tier 3 & 4 focus):
    - TOTALYTD / TOTALMTD / TOTALQTD  → time-intelligence window functions
    - CALCULATE(agg, FILTER(...), ALL(...), ALLEXCEPT(...))
    - SAMEPERIODLASTYEAR / PREVIOUSYEAR / PREVIOUSMONTH / PREVIOUSQUARTER
    - IF / SWITCH / DIVIDE
    - Arithmetic / comparison expressions
    """

    def __init__(self) -> None:
        self.tokens: List[DaxToken] = []
        self.pos = 0

    def parse(self, dax: str) -> Optional[DaxNode]:
        """
        Parse a DAX string into an AST.

        Returns the root AST node, or None if the expression is empty
        or unparseable (errors are logged, not raised, for resilience).
        """
        if not dax or not dax.strip():
            return None
        try:
            lexer = DaxLexer(dax.strip())
            self.tokens = lexer.tokenize()
            self.pos = 0
            node = self._parse_expr()
            return node
        except DaxParseError as exc:
            logger.debug(f"DAX parse error (recoverable): {exc} | DAX: {dax[:80]}")
            return None
        except Exception as exc:
            logger.debug(f"DAX parse unexpected error: {exc} | DAX: {dax[:80]}")
            return None

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def _peek(self) -> DaxToken:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else DaxToken(DaxTokenType.EOF, "")

    def _advance(self) -> DaxToken:
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _expect(self, ttype: DaxTokenType) -> DaxToken:
        tok = self._peek()
        if tok.type != ttype:
            raise DaxParseError(f"Expected {ttype}, got {tok.type}({tok.value!r})")
        return self._advance()

    def _match(self, *types: DaxTokenType) -> bool:
        return self._peek().type in types

    def _match_func(self, *names: str) -> bool:
        tok = self._peek()
        return tok.type == DaxTokenType.FUNC_NAME and tok.value.upper() in {n.upper() for n in names}

    # ------------------------------------------------------------------
    # Expression grammar
    # ------------------------------------------------------------------

    def _parse_expr(self) -> DaxNode:
        return self._parse_or()

    def _parse_or(self) -> DaxNode:
        left = self._parse_and()
        while self._match(DaxTokenType.OR):
            op = self._advance().value
            right = self._parse_and()
            left = BinaryOpNode(op="OR", left=left, right=right)
        return left

    def _parse_and(self) -> DaxNode:
        left = self._parse_not()
        while self._match(DaxTokenType.AND):
            op = self._advance().value
            right = self._parse_not()
            left = BinaryOpNode(op="AND", left=left, right=right)
        return left

    def _parse_not(self) -> DaxNode:
        if self._match(DaxTokenType.NOT):
            self._advance()
            return UnaryOpNode(op="NOT", operand=self._parse_comparison())
        return self._parse_comparison()

    def _parse_comparison(self) -> DaxNode:
        left = self._parse_addition()
        cmp_ops = {
            DaxTokenType.EQ: "=",
            DaxTokenType.NEQ: "<>",
            DaxTokenType.LT: "<",
            DaxTokenType.LTE: "<=",
            DaxTokenType.GT: ">",
            DaxTokenType.GTE: ">=",
        }
        while self._peek().type in cmp_ops:
            op = cmp_ops[self._advance().type]
            right = self._parse_addition()
            left = BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_addition(self) -> DaxNode:
        left = self._parse_multiplication()
        while self._match(DaxTokenType.PLUS, DaxTokenType.MINUS):
            op = self._advance().value
            right = self._parse_multiplication()
            left = BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_multiplication(self) -> DaxNode:
        left = self._parse_unary()
        while self._match(DaxTokenType.STAR, DaxTokenType.SLASH):
            op = self._advance().value
            right = self._parse_unary()
            left = BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_unary(self) -> DaxNode:
        if self._match(DaxTokenType.MINUS):
            self._advance()
            return UnaryOpNode(op="-", operand=self._parse_primary())
        return self._parse_primary()

    def _parse_primary(self) -> DaxNode:
        tok = self._peek()

        if tok.type == DaxTokenType.COLUMN_REF:
            self._advance()
            # Parse 'Table'[Column]
            m = re.match(r"'([^']+)'\[([^\]]+)\]", tok.value)
            if m:
                return ColumnRefNode(table=m.group(1), column=m.group(2), raw=tok.value)
            return IdentifierNode(name=tok.value)

        if tok.type == DaxTokenType.MEASURE_REF:
            self._advance()
            name = tok.value.strip("[]")
            return MeasureRefNode(name=name)

        if tok.type == DaxTokenType.IDENTIFIER:
            # Unquoted table-qualified column reference: TableName[Column]
            # (DAX allows omitting the quotes around a table name that has
            # no spaces/special characters — the existing COLUMN_REF branch
            # above only recognizes the quoted 'Table'[Column] form). The
            # lexer tokenizes this as a bare IDENTIFIER immediately followed
            # by a MEASURE_REF, with no operator/comma between them — the
            # only DAX grammar production that produces that exact adjacent
            # pair, so this check is unambiguous.
            next_tok = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else None
            if next_tok is not None and next_tok.type == DaxTokenType.MEASURE_REF:
                self._advance()  # consume the table-name identifier
                bracket_tok = self._advance()  # consume [Column]
                column = bracket_tok.value.strip("[]")
                return ColumnRefNode(table=tok.value, column=column, raw=f"{tok.value}{bracket_tok.value}")

        if tok.type == DaxTokenType.NUMBER:
            self._advance()
            try:
                val: Union[int, float] = int(tok.value) if "." not in tok.value else float(tok.value)
            except ValueError:
                val = 0
            return LiteralNode(value=val, raw=tok.value)

        if tok.type == DaxTokenType.STRING:
            self._advance()
            return LiteralNode(value=tok.value, raw=f'"{tok.value}"')

        if tok.type in (DaxTokenType.FUNC_NAME, DaxTokenType.IDENTIFIER):
            return self._parse_function_call()

        if tok.type == DaxTokenType.LPAREN:
            self._advance()
            node = self._parse_expr()
            self._expect(DaxTokenType.RPAREN)
            return node

        if tok.type == DaxTokenType.EOF:
            raise DaxParseError("Unexpected end of expression")

        # Unknown token — consume and return identifier
        self._advance()
        return IdentifierNode(name=tok.value)

    def _parse_function_call(self) -> DaxNode:
        tok = self._advance()
        func_name = tok.value.upper()

        if self._peek().type != DaxTokenType.LPAREN:
            # Bare identifier without call
            return IdentifierNode(name=tok.value)

        self._advance()  # consume '('
        args: List[DaxNode] = []

        # Parse arguments separated by commas
        while not self._match(DaxTokenType.RPAREN, DaxTokenType.EOF):
            args.append(self._parse_expr())
            if self._match(DaxTokenType.COMMA):
                self._advance()

        if self._peek().type == DaxTokenType.RPAREN:
            self._advance()  # consume ')'

        return FunctionCallNode(func=func_name, args=args)


# ---------------------------------------------------------------------------
# SQL Renderer  (Snowflake dialect)
# ---------------------------------------------------------------------------


# Stable category code for the "unshifted fallback" a lag-period function
# (SAMEPERIODLASTYEAR/PREVIOUSYEAR/PREVIOUSMONTH/PREVIOUSQUARTER) falls back
# to when it wraps a measure reference whose own resolved SQL isn't a shape
# _inject_case_filter_into_rendered_aggregate can safely apply a date-range
# filter to (e.g. a nested aggregate) — see DaxSqlRenderer._render_lag_period.
# Opt-in only (allow_unshifted_fallback=True): the metric still ships real,
# valid SQL rather than being hard-blocked, but that SQL is the CURRENT
# period's value, not actually shifted to the requested prior period — a
# human must confirm this is acceptable before trusting it. Mirrored onto
# SMLMetric.advisory_categories by osi_to_sml.py, same convention as
# ADVISORY_CATEGORY_UNREACHABLE_DIMENSION below, so downstream consumers
# (project_mapping_engine.py's status computation) can key off this
# structural signal instead of matching a free-text reason string.
ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK = "lag_period_unshifted_fallback"

# Human-readable counterpart appended to SMLMetric.advisory_notes in lockstep
# with the category above (same-index parallel lists), matching the
# note/category pairing convention advisory_notes documents.
ADVISORY_NOTE_LAG_PERIOD_UNSHIFTED_FALLBACK = (
    "Time-intelligence function (e.g. SAMEPERIODLASTYEAR/PREVIOUSYEAR) could not be "
    "safely date-shifted for this measure, so it was translated as the base measure's "
    "current-period value instead. Needs review before trusting the shifted comparison."
)


class DaxSqlRenderer:
    """
    Walks a parsed DAX AST and emits Snowflake-compatible SQL.

    The renderer is deterministic: given the same AST it always emits
    the same SQL.  It raises ``DaxRenderError`` for expressions it cannot
    safely translate.

    Args:
        table_alias: SQL alias for the primary fact table (e.g. ``"SALES"``).
        date_alias: SQL alias for the Date/Calendar dimension table. Defaults
            to ``"COL_DATE"`` — the established Snowflake naming convention
            for this dimension (see snowflake_emitter.py's "Map common DAX
            name -> physical name (e.g. Date -> COL_DATE)" and
            dax_rule_translator.py's own _date_alias(), both of which already
            use this convention).
        measure_sql_map: Pre-resolved SQL for other metrics, keyed by measure name.
            Used to resolve [MeasureName] references in Tier 2/4 expressions.
    """

    class DaxRenderError(Exception):
        pass

    # Time-intelligence function mapping
    _TIME_INTEL_FUNCS = {
        "TOTALYTD": "YTD",
        "TOTALMTD": "MTD",
        "TOTALQTD": "QTD",
        "SAMEPERIODLASTYEAR": "SAMEPERIODLASTYEAR",
        "PREVIOUSYEAR": "PREVIOUSYEAR",
        "PREVIOUSMONTH": "PREVIOUSMONTH",
        "PREVIOUSQUARTER": "PREVIOUSQUARTER",
    }

    _AGG_MAP = {
        "SUM": "SUM",
        "AVERAGE": "AVG",
        "MIN": "MIN",
        "MAX": "MAX",
        "COUNT": "COUNT",
        "DISTINCTCOUNT": "COUNT(DISTINCT {col})",
        "COUNTROWS": "COUNT(*)",
        "COUNTA": "COUNT",
    }

    # DAX names conventionally used for the date/calendar dimension across
    # this codebase (see dax_translator.py's "'Calendar'[Date]" dimension
    # reference and dax_engine.py's date_alias="calendar" default) — a
    # ColumnRefNode naming one of these is resolved to date_alias rather
    # than the primary table.
    _DEFAULT_DATE_TABLE_NAMES = frozenset({"date", "calendar"})

    def __init__(
        self,
        table_alias: str = "T",
        date_alias: str = "COL_DATE",
        measure_sql_map: Optional[Dict[str, str]] = None,
        known_measure_names: Optional[Any] = None,
        anchor_flag_map: Optional[Dict[Tuple[str, ...], str]] = None,
        primary_table_name: Optional[str] = None,
        date_table_names: Optional[Any] = None,
        allow_unshifted_fallback: bool = False,
        advisory_categories: Optional[List[str]] = None,
    ) -> None:
        # Keep alias as provided (caller passes the correct form)
        self.table_alias = table_alias
        self.date_alias = date_alias
        self.measure_sql_map = measure_sql_map or {}
        # The DAX table name (e.g. "KPI") that table_alias physically
        # represents for this metric, if the caller knows it. None means
        # the caller hasn't opted into table-aware column resolution yet —
        # _resolve_table_alias then preserves the historical
        # single-table-assumed behavior exactly, so callers that don't pass
        # this are unaffected.
        self.primary_table_name = primary_table_name
        self._date_table_names_cf = {
            str(n).strip().casefold()
            for n in (date_table_names or self._DEFAULT_DATE_TABLE_NAMES)
        }
        # Shape tuple (see converter/time_intelligence_shapes.py, e.g.
        # ("YTD",), ("YTD", "SPLY_YEAR")) -> precomputed boolean flag column
        # name on the enriched view (e.g. "IS_YTD"). When a shape has an
        # entry here, _render_period_to_date/_render_lag_period reference
        # that column directly instead of building inline MAX_DATE
        # arithmetic — MAX_DATE itself is rejected by Snowflake's semantic-
        # view compiler even though the column physically exists; an
        # ordinary per-row boolean column is not. Empty (the default)
        # preserves today's inline-MAX_DATE behavior exactly — this is the
        # schema-blind dry-run/preview path's only mode, since it has no
        # live enriched view to reference flags on.
        self.anchor_flag_map: Dict[Tuple[str, ...], str] = anchor_flag_map or {}
        # Names of measures known to exist in the model but not yet resolved
        # to SQL (absent from measure_sql_map). Distinguishes "this bracket
        # reference names a real measure that just isn't translated yet"
        # (must fail closed — see _render_measure_ref) from "this bracket
        # reference names something the caller has no measure-registry
        # knowledge of at all" (the common SUM([Column]) shape, where a bare
        # bracket names a physical column — falls back to a column
        # reference, exactly as before).
        self.known_measure_names = set(known_measure_names or ())
        # DAX measure-name resolution is case-insensitive (Analysis Services
        # treats [Total Units] and [TOTAL UNITS] as the same measure) —
        # build casefolded lookups alongside the caller-provided ones so
        # _render_measure_ref matches regardless of casing, without
        # weakening the known-but-unresolved-vs-genuinely-unknown
        # distinction in _render_measure_ref below.
        self._measure_sql_map_by_casefold = {
            str(name).casefold(): sql for name, sql in self.measure_sql_map.items()
        }
        self._known_measure_names_casefold = {
            str(name).casefold() for name in self.known_measure_names
        }
        self._func_registry = FunctionRegistry()
        # Opt-in only -- False preserves the historical fail-closed
        # behavior exactly for every existing caller. Only a caller that
        # explicitly wants "ship an unshifted approximation, flagged for
        # review" instead of a hard translation failure sets this True
        # (see _render_lag_period's two DaxRenderError sites).
        self._allow_unshifted_fallback = allow_unshifted_fallback
        # Caller-provided list is mutated in place (same collector pattern
        # as DropLedger elsewhere in this codebase) so the caller can read
        # it back after render() returns; a fresh list is used internally
        # either way so this attribute is never None.
        self._advisory_categories: List[str] = (
            advisory_categories if advisory_categories is not None else []
        )

    def render(self, node: Optional[DaxNode]) -> Optional[str]:
        """
        Render a DAX AST node to Snowflake SQL.

        Returns None if the node cannot be safely translated.
        """
        if node is None:
            return None
        try:
            return self._render_node(node)
        except self.DaxRenderError as exc:
            logger.debug(f"AST render failed (recoverable): {exc}")
            return None

    def _render_node(self, node: DaxNode) -> str:
        if isinstance(node, LiteralNode):
            return self._render_literal(node)
        if isinstance(node, ColumnRefNode):
            return self._render_column_ref(node)
        if isinstance(node, MeasureRefNode):
            return self._render_measure_ref(node)
        if isinstance(node, BinaryOpNode):
            return self._render_binary(node)
        if isinstance(node, UnaryOpNode):
            return self._render_unary(node)
        if isinstance(node, FunctionCallNode):
            return self._render_function(node)
        if isinstance(node, IdentifierNode):
            return f'"{sanitize_column(node.name)}"'
        raise self.DaxRenderError(f"Unknown node type: {type(node).__name__}")

    # ------------------------------------------------------------------

    def _render_literal(self, node: LiteralNode) -> str:
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        if isinstance(node.value, str):
            escaped = node.value.replace("'", "''")
            return f"'{escaped}'"
        return str(node.value)

    def _render_column_ref(self, node: ColumnRefNode) -> str:
        col = sanitize_column(node.column)
        alias = self._resolve_table_alias(node.table)
        return f'{alias}."{col}"'

    def _resolve_table_alias(self, table_name: str) -> str:
        """Map a DAX 'TableName' to the SQL alias that owns its columns.

        Bug this closes: a bare 'TableName'[Column] reference used to
        always resolve to self.table_alias (the metric's own primary
        table) no matter what table it actually named — e.g. a CALCULATE
        filter on 'Date'[Running Year] inside a KPI-table measure rendered
        as kpi."RUNNING_YEAR" instead of the date table's column, silently
        filtering on the wrong table. Snowflake then either errors on the
        nonexistent column or (worse) returns wrong numbers if a
        same-named column happens to exist.
        """
        name_cf = str(table_name or "").strip().strip("'").casefold()
        if not self.primary_table_name:
            # Caller hasn't told us which DAX table this metric belongs to
            # (not yet opted into table-aware resolution, or passed an
            # empty/unknown name) — preserve the historical single-table
            # assumption rather than fail closed on missing context.
            return self.table_alias
        if name_cf == self.primary_table_name.strip().strip("'").casefold():
            return self.table_alias
        if name_cf in self._date_table_names_cf:
            return self.date_alias
        raise self.DaxRenderError(
            f"Column reference to table '{table_name}' does not match this "
            f"metric's own table ('{self.primary_table_name}') or the "
            "recognized date dimension — refusing to guess its SQL alias "
            "rather than emit a cross-table column reference that may "
            "silently compute the wrong thing"
        )

    def _render_measure_ref(self, node: MeasureRefNode) -> str:
        name_cf = node.name.casefold()
        if name_cf in self._measure_sql_map_by_casefold:
            return f"({self._measure_sql_map_by_casefold[name_cf]})"
        if name_cf in self._known_measure_names_casefold:
            # The caller's model tracks this as a real measure name, so
            # rendering it as a column would reference a physical column
            # that doesn't exist (Bug: measure-references-measure leaking
            # as a column reference). Fail closed so the caller can defer
            # to a later pass with fuller measure context, instead of
            # "succeeding" with wrong SQL.
            raise self.DaxRenderError(
                f"Unresolved measure reference [{node.name}]: known measure with no resolved SQL yet"
            )
        # The caller has no measure-registry knowledge of this name at all —
        # the common case is a bare [Column] used as an aggregation
        # argument (e.g. SUM([Amount])), where the parser cannot distinguish
        # a column from a measure at the token level. Falling back to a
        # column reference here preserves that long-standing, widely-relied
        # upon shape; it is only wrong for genuine unresolved measure
        # references, which are caught by the branch above instead.
        col = sanitize_column(node.name)
        return f'{self.table_alias}."{col}"'

    def _render_binary(self, node: BinaryOpNode) -> str:
        left = self._render_node(node.left)
        right = self._render_node(node.right)
        op_map = {
            "=": "=", "<>": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">=",
            "+": "+", "-": "-", "*": "*", "/": "/",
            "AND": "AND", "OR": "OR",
        }
        sql_op = op_map.get(node.op.upper(), node.op)
        # Wrap operands in parens for clarity on logical operators
        if sql_op in ("AND", "OR"):
            return f"({left}) {sql_op} ({right})"
        return f"{left} {sql_op} {right}"

    def _render_unary(self, node: UnaryOpNode) -> str:
        inner = self._render_node(node.operand)
        if node.op == "-":
            return f"(-{inner})"
        if node.op.upper() == "NOT":
            return f"NOT ({inner})"
        return inner

    def _render_function(self, node: FunctionCallNode) -> str:
        func = node.func.upper()

        # CALCULATE (lazy/CASE-based evaluation of filters)
        if func == "CALCULATE":
            return self._render_calculate(node.args)

        # Time Intelligence (lazy OVER/window/LAG function rendering)
        if func in {"TOTALYTD", "TOTALMTD", "TOTALQTD"}:
            return self._render_period_to_date(func, node.args)

        if func in {"SAMEPERIODLASTYEAR", "PREVIOUSYEAR"}:
            return self._render_lag_period(node.args, interval="year", amount=-1)

        if func == "PREVIOUSMONTH":
            return self._render_lag_period(node.args, interval="month", amount=-1)

        if func == "PREVIOUSQUARTER":
            return self._render_lag_period(node.args, interval="quarter", amount=-1)

        if func == "DATEADD":
            return self._render_dateadd(node.args)

        # Registry-driven templates (if configured)
        rendered_args = [self._render_node(a) for a in node.args]
        registry_sql = self._func_registry.render(
            func,
            rendered_args,
            table_alias=self.table_alias,
            date_alias=self.date_alias,
        )
        if registry_sql:
            return registry_sql

        # Direct aggregations
        if func in self._AGG_MAP and node.args:
            return self._render_aggregation(func, node.args[0])

        # DIVIDE → DIV0 (Snowflake null-safe division)
        if func == "DIVIDE":
            return self._render_divide(node.args)

        # IF
        if func == "IF":
            return self._render_if(node.args)

        # SWITCH
        if func == "SWITCH":
            return self._render_switch(node.args)

        # IFERROR
        if func == "IFERROR":
            return self._render_iferror(node.args)

        # ISBLANK
        if func == "ISBLANK":
            if len(node.args) != 1:
                raise self.DaxRenderError("ISBLANK requires 1 argument")
            arg_sql = self._render_node(node.args[0])
            return f"({arg_sql} IS NULL)"

        # BLANK() -> SQL NULL (matches ISBLANK's own "x IS NULL" mapping
        # just above, and the same BLANK()->NULL rule already documented
        # for Tier 5's prompt — just never implemented in this renderer).
        if func == "BLANK":
            return "NULL"

        # CONCATENATE
        if func == "CONCATENATE":
            if len(node.args) >= 2:
                left = self._render_node(node.args[0])
                right = self._render_node(node.args[1])
                return f"CONCAT({left}, {right})"

        # String functions with direct Snowflake equivalents
        _direct_sql = {
            "LEFT": "LEFT", "RIGHT": "RIGHT", "LEN": "LENGTH", "TRIM": "TRIM",
            "UPPER": "UPPER", "LOWER": "LOWER",
            "YEAR": "YEAR", "MONTH": "MONTH", "DAY": "DAY",
            "HOUR": "HOUR", "MINUTE": "MINUTE", "SECOND": "SECOND",
            "ABS": "ABS", "CEILING": "CEIL", "FLOOR": "FLOOR",
            "ROUND": "ROUND", "ROUNDUP": "CEIL", "ROUNDDOWN": "FLOOR",
            "INT": "FLOOR", "NOW": "CURRENT_TIMESTAMP()", "TODAY": "CURRENT_DATE()",
        }
        if func in _direct_sql:
            sql_func = _direct_sql[func]
            rendered_args = ", ".join(self._render_node(a) for a in node.args)
            if not node.args:
                return f"{sql_func}"
            return f"{sql_func}({rendered_args})"

        # Unsupported — raise so caller can fall back
        raise self.DaxRenderError(f"Unsupported DAX function: {func}")

    # ------------------------------------------------------------------
    # Helpers for specific patterns
    # ------------------------------------------------------------------

    def _render_aggregation(self, func: str, arg: DaxNode) -> str:
        col_sql = self._render_node(arg)
        template = self._AGG_MAP[func]
        
        # Snowflake: cast to FLOAT for SUM/AVG to handle BOOLEAN columns safely
        cast = "::FLOAT" if func in ("SUM", "AVERAGE", "AVERAGEX", "AVG") else ""
        
        if "{col}" in template:
            return template.format(col=f"{col_sql}{cast}")
        return f"{template}({col_sql}{cast})"

    def _render_divide(self, args: List[DaxNode]) -> str:
        if len(args) < 2:
            raise self.DaxRenderError("DIVIDE requires at least 2 arguments")
        num = self._render_node(args[0])
        den = self._render_node(args[1])
        # DIV0 returns 0 (not NULL) on zero division; matches DAX DIVIDE default
        return f"DIV0({num}, {den})"

    def _render_if(self, args: List[DaxNode]) -> str:
        if len(args) < 2:
            raise self.DaxRenderError("IF requires at least 2 arguments")
        cond = self._render_node(args[0])
        true_val = self._render_node(args[1])
        false_val = self._render_node(args[2]) if len(args) > 2 else "NULL"
        return f"CASE WHEN {cond} THEN {true_val} ELSE {false_val} END"

    def _render_iferror(self, args: List[DaxNode]) -> str:
        if len(args) < 2:
            raise self.DaxRenderError("IFERROR requires 2 arguments")
        expr = self._render_node(args[0])
        fallback = self._render_node(args[1])
        return f"IFF(TRY_TO_NUMBER(TO_VARCHAR({expr})) IS NULL, {fallback}, {expr})"

    def _render_switch(self, args: List[DaxNode]) -> str:
        if len(args) < 3:
            raise self.DaxRenderError("SWITCH requires at least 3 arguments")
        expr = self._render_node(args[0])
        cases = []
        i = 1
        while i + 1 < len(args):
            match_val = self._render_node(args[i])
            result = self._render_node(args[i + 1])
            cases.append(f"WHEN {expr} = {match_val} THEN {result}")
            i += 2
        else_clause = f"ELSE {self._render_node(args[-1])}" if len(args) % 2 == 0 else "ELSE NULL"
        return "CASE " + " ".join(cases) + f" {else_clause} END"

    def _rewrite_filtered_aggregate(self, agg_node: FunctionCallNode, condition_sql: str) -> str:
        func = agg_node.func.upper()
        if func not in self._AGG_MAP:
            raise self.DaxRenderError(f"Function {func} is not a recognized direct aggregation")
        
        if func == "COUNTROWS":
            case_expr = f"CASE WHEN {condition_sql} THEN 1 END"
            return f"COUNT({case_expr})"
            
        if not agg_node.args:
            raise self.DaxRenderError(f"Aggregation {func} requires at least 1 argument")
            
        col_sql = self._render_node(agg_node.args[0])
        case_expr = f"CASE WHEN {condition_sql} THEN {col_sql} END"
        
        if func == "DISTINCTCOUNT":
            return f"COUNT(DISTINCT {case_expr})"
            
        sql_func = "AVG" if func == "AVERAGE" else func
        if sql_func == "COUNTA":
            sql_func = "COUNT"

        cast = "::FLOAT" if func in ("SUM", "AVERAGE") else ""
        return f"{sql_func}({case_expr}{cast})"

    _AGG_CALL_START = re.compile(r"\b(SUM|AVG|MIN|MAX|COUNT)\s*\(", re.IGNORECASE)

    @staticmethod
    def _find_balanced_close_paren(expr: str, open_paren_index: int) -> Optional[int]:
        """Return the index of the ')' matching the '(' at
        open_paren_index, tracking nesting depth and skipping single-
        quoted string literals. None if the parens never balance."""
        depth = 0
        in_string = False
        for i in range(open_paren_index, len(expr)):
            ch = expr[i]
            if ch == "'":
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
        return None

    def _inject_case_filter_into_rendered_aggregate(self, rendered_sql: str, condition_sql: str) -> Optional[str]:
        """Rewrite an already-rendered SQL expression (e.g. from inlining a
        referenced measure's own SQL) so every leaf SUM/AVG/MIN/MAX/COUNT
        call in it is filtered by `condition_sql`, instead of wrapping the
        whole already-aggregated expression in a second outer aggregate.

        Finds and rewrites EVERY bare aggregate call wherever it appears —
        not just at the top level — so this handles a measure defined as
        [A] - [B] (each side its own aggregate), a ratio like
        DIVIDE([A], [B]), an IF(...)-guarded ratio, or any other
        combination of aggregates, uniformly: applying a row-level date
        filter to a formula built purely from same-grain aggregates is
        mathematically equivalent whether applied to the whole formula or
        to each of its aggregate leaves individually, regardless of what
        arithmetic/conditional structure combines them. Only the
        aggregates' own arguments are rewritten; everything else (IF/CASE
        structure, division, comparisons) is left untouched.

        Returns None (fail closed) if `rendered_sql` contains a NESTED
        aggregate (a call whose own argument contains another aggregate
        call) — that shape means this isn't simple leaf-level aggregation,
        and rewriting it here would risk producing SUM(...SUM(...)...),
        which Snowflake (and this codebase's own metric-SQL rules)
        disallow — or if no aggregate call is found at all (nothing to
        filter).
        """
        clean = (rendered_sql or "").strip()
        if clean.startswith("(") and clean.endswith(")"):
            clean = clean[1:-1].strip()

        calls: List[Tuple[int, int, str, str]] = []
        for m in self._AGG_CALL_START.finditer(clean):
            func = m.group(1).upper()
            open_idx = m.end() - 1
            close_idx = self._find_balanced_close_paren(clean, open_idx)
            if close_idx is None:
                return None  # unbalanced parens — malformed, fail closed
            arg = clean[open_idx + 1 : close_idx].strip()
            calls.append((m.start(), close_idx, func, arg))

        if not calls:
            return None

        # A call nested inside another call's argument span means this
        # isn't a flat leaf-level formula (e.g. SUM(AVG(x))) — fail closed
        # rather than guess which one should actually be filtered.
        for i, (s1, e1, _f1, _a1) in enumerate(calls):
            for j, (s2, e2, _f2, _a2) in enumerate(calls):
                if i != j and s2 > s1 and e2 < e1:
                    return None

        rewritten = clean
        for start, end, func, arg in sorted(calls, key=lambda c: c[0], reverse=True):
            replacement = f"{func}(CASE WHEN {condition_sql} THEN {arg} ELSE NULL END)"
            rewritten = rewritten[:start] + replacement + rewritten[end + 1 :]

        return rewritten

    def _render_calculate(self, args: List[DaxNode]) -> str:
        """
        Translate CALCULATE(agg_expr, filter1, filter2, ...) to a subquery or CASE expression.

        Supported filter modifiers:
          - ALL(table)          → ignore all filters for that table
          - ALLEXCEPT(t, col)   → OVER (PARTITION BY col)
          - FILTER(tbl, pred)   → WHERE pred
          - simple comparisons  → WHERE comparison
        """
        if not args:
            raise self.DaxRenderError("CALCULATE requires at least 1 argument")

        agg_expr = self._render_node(args[0])
        filter_clauses: List[str] = []
        partition_cols: List[str] = []
        is_window = False

        # Support lag-period time-intelligence functions inside CALCULATE —
        # DAX allows this shape (CALCULATE(agg, SAMEPERIODLASTYEAR(Date[Date])))
        # as an alternative to the direct SAMEPERIODLASTYEAR(agg, Date[Date])
        # form _render_lag_period already handles. These are the same DAX
        # operation expressed two different ways — delegate to that single
        # shared implementation rather than keeping a second, independent
        # copy of the same CASE WHEN/anchor-shift logic here. (This
        # duplication is exactly how a quarter/month measure-reference
        # fallback existed in _render_lag_period but not here, and how an
        # anchor-shift bug got fixed in one copy but not the other.)
        lag_filter_arg = None
        lag_interval = "year"
        for filter_arg in args[1:]:
            if isinstance(filter_arg, FunctionCallNode):
                fname = filter_arg.func.upper()
                if fname in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
                    lag_interval = "year" if fname in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR") else ("quarter" if fname == "PREVIOUSQUARTER" else "month")
                    lag_filter_arg = filter_arg
                    break

        if lag_filter_arg is not None:
            date_arg = lag_filter_arg.args[0] if lag_filter_arg.args else None
            if date_arg is None:
                raise self.DaxRenderError(f"{lag_filter_arg.func} requires a date-column argument")
            return self._render_lag_period([args[0], date_arg], interval=lag_interval, amount=-1)

        for filter_arg in args[1:]:
            if isinstance(filter_arg, FunctionCallNode):
                fname = filter_arg.func.upper()

                if fname == "ALL":
                    # ALL(table) strips all filters → no WHERE, use window over full table
                    is_window = True

                elif fname == "ALLEXCEPT":
                    # ALLEXCEPT(table, col1, col2,...) → PARTITION BY col1, col2
                    is_window = True
                    for col_arg in filter_arg.args[1:]:
                        if isinstance(col_arg, ColumnRefNode):
                            partition_cols.append(f'{self.table_alias}."{sanitize_column(col_arg.column)}"')
                        elif isinstance(col_arg, MeasureRefNode):
                            partition_cols.append(f'"{sanitize_column(col_arg.name)}"')

                elif fname == "FILTER":
                    # FILTER(table, predicate) → extract predicate as WHERE clause
                    if len(filter_arg.args) >= 2:
                        pred = self._render_node(filter_arg.args[1])
                        filter_clauses.append(pred)
                    else:
                        raise self.DaxRenderError("FILTER requires table and predicate arguments")

                else:
                    # Unrecognized modifier
                    raise self.DaxRenderError(f"CALCULATE filter modifier not supported: {fname}")

            elif isinstance(filter_arg, BinaryOpNode):
                # Direct comparison inside CALCULATE, e.g. CALCULATE(SUM(...), 'Date'[Year] = 2024)
                filter_clauses.append(self._render_node(filter_arg))

            else:
                # Anything else (a bare measure/column reference, literal, etc.)
                # is not a recognized filter shape. Raise rather than silently
                # dropping it — a dropped filter argument changes what the
                # expression computes without any indication that happened.
                raise self.DaxRenderError(
                    f"CALCULATE filter argument of type {type(filter_arg).__name__} "
                    "is not a recognized filter modifier or comparison predicate"
                )

        if is_window:
            # ALL(...)/ALLEXCEPT(...) mean "re-partition this aggregate
            # independently of the query's own grouping" — inherently a SQL
            # window function. Snowflake's semantic-view METRICS clause
            # forbids window functions (see metrics_clause_builder.py's and
            # snowflake_metric_sql.py's own OVER/PARTITION BY guards), and
            # this codebase has no derived-metric/precomputed-column
            # mechanism to materialize the windowed value another way.
            # Emitting OVER(...) here used to look like a successful Tier 4
            # translation while actually being invalid Snowflake METRICS
            # SQL (silently NULL at query time) — fail closed instead, so
            # the metric correctly escalates to Tier 5 like any other
            # genuinely unresolved pattern.
            raise self.DaxRenderError(
                "ALLEXCEPT/ALL context-transition requires a SQL window "
                "function, which Snowflake's semantic-view METRICS clause "
                "does not support"
            )
        elif filter_clauses:
            where = " AND ".join(filter_clauses)
            if isinstance(args[0], FunctionCallNode) and args[0].func.upper() in self._AGG_MAP:
                return self._rewrite_filtered_aggregate(args[0], where)
            # Base isn't a direct aggregate call — the common real-world
            # shape here is a measure reference, e.g. CALCULATE([Measure],
            # <filter>). Inject the filter into ITS aggregate argument
            # rather than wrapping the already-aggregated expression in a
            # second outer aggregate — the same fallback already used for
            # TOTALYTD/lag-period measure-reference bases
            # (_render_period_to_date/_render_lag_period), just never
            # applied to this branch until now.
            rewritten = self._inject_case_filter_into_rendered_aggregate(agg_expr, where)
            if rewritten:
                return rewritten
            raise self.DaxRenderError(
                f"Base aggregation {args[0]} is too complex to rewrite as CASE-based filtered aggregate"
            )
        else:
            return agg_expr

    # Must stay in sync with converter/time_intelligence_shapes.py's
    # PERIOD_TO_DATE_FUNCS / LAG_FUNCS — duplicated locally (not imported)
    # to avoid a circular import (that module imports node classes from
    # this one).
    _PERIOD_TO_DATE_COMPONENT = {"TOTALYTD": "YTD", "TOTALMTD": "MTD", "TOTALQTD": "QTD"}
    _PERIOD_TO_DATE_UNIT = {"TOTALYTD": "YEAR", "TOTALMTD": "MONTH", "TOTALQTD": "QUARTER"}

    def _render_period_to_date(self, func: str, args: List[DaxNode]) -> str:
        """
        Translate TOTALYTD / TOTALMTD / TOTALQTD to Snowflake CASE WHEN scalar aggregates.

        Snowflake METRICS clause does NOT allow window functions (OVER).
        Pattern: TOTALYTD(aggregation, date_column[, filter_expression])
        """
        if not args:
            raise self.DaxRenderError(f"{func} requires at least 1 argument")

        d = self.date_alias
        date_col = f'{d}."COL_DATE"'

        # Shape-flag mode (see converter/time_intelligence_shapes.py): if the
        # caller precomputed a boolean flag column for this exact shape on
        # the enriched view, reference it directly — Snowflake's semantic-
        # view compiler rejects a direct MAX_DATE reference even though the
        # column physically exists, but accepts an ordinary per-row boolean
        # column fine (confirmed against a live deploy). Falls back to
        # today's inline MAX_DATE arithmetic when no flag map was supplied —
        # the schema-blind dry-run/preview path's only mode, since it has no
        # live enriched view to reference flags on.
        component = self._PERIOD_TO_DATE_COMPONENT.get(func)
        flag_name = self.anchor_flag_map.get((component,)) if component else None
        if flag_name:
            condition_sql = f'{self.table_alias}."{flag_name}"'
        else:
            # No flag column provisioned for this shape (schema-blind
            # dry-run/preview, or a real deploy where enrichment couldn't
            # establish this fact table's anchor) — anchor to CURRENT_DATE(),
            # a native Snowflake function, never a synthetic enriched-view
            # column. MAX_DATE itself only exists on the enriched view (see
            # _create_enriched_view), so referencing it here unconditionally
            # is exactly as enrichment-dependent as the flag-column path,
            # just via an uglier name — this branch must degrade to
            # something that works with NO enriched view at all. Matches the
            # fallback already established (and tested) in
            # dax_rule_translator.py's translate_time_intelligence_with_anchors
            # and connectors/translator.py's parallel implementation (commit
            # e4c8322) — known, accepted tradeoff: CURRENT_DATE() is today's
            # real wall-clock date, not the last-synced date; see those
            # implementations' comments for the year-rollover staleness
            # tradeoff this carries.
            max_date = "CURRENT_DATE()"
            unit = self._PERIOD_TO_DATE_UNIT.get(func)
            condition_sql = (
                f"{date_col} >= DATE_TRUNC('{unit}', {max_date}) AND {date_col} <= {max_date}"
                if unit else None
            )

        if condition_sql is None:
            raise self.DaxRenderError(f"Unhandled period-to-date func: {func}")

        # Unwrap the inner aggregation to build a CASE WHEN expression
        agg_node = args[0]
        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else f'{self.table_alias}."AMOUNT"'
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            cast = "::FLOAT" if inner_func in ("SUM", "AVERAGE") else ""
            return f"{sql_func}(CASE WHEN {condition_sql} THEN {col_sql}{cast} END)"

        # Base argument isn't a direct aggregation call — the common
        # real-world shape here is a measure reference, e.g.
        # TOTALYTD([Total Sales], 'Date'[Date]). Render it (inlining the
        # referenced measure's own SQL via measure_sql_map, or failing
        # closed per _render_measure_ref) and inject the date-range
        # filter into ITS aggregate argument, rather than wrapping the
        # already-aggregated expression in a second outer aggregate —
        # Snowflake disallows nested aggregate functions.
        agg_sql = self._render_node(agg_node)
        rewritten = self._inject_case_filter_into_rendered_aggregate(agg_sql, condition_sql)
        if rewritten:
            return rewritten
        raise self.DaxRenderError(
            f"{func}: base expression '{agg_sql}' is not a simple aggregate this "
            "renderer can safely apply a date-range filter to without risking a "
            "nested aggregate"
        )

    # Must stay in sync with converter/time_intelligence_shapes.py's LAG_FUNCS.
    _LAG_COMPONENT = {"year": "SPLY_YEAR", "quarter": "SPLY_QUARTER", "month": "SPLY_MONTH"}

    def _lag_period_unshifted_fallback_or_raise(self, agg_sql: str, error_message: str) -> str:
        """Called at a lag-period DaxRenderError site instead of raising
        directly. Default (allow_unshifted_fallback=False): raises exactly
        as before -- no behavior change for any existing caller. Opted in:
        ships `agg_sql` completely unmodified (the referenced measure's
        real, valid SQL, just not shifted to the requested prior period)
        and records ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK instead
        of failing the whole metric closed. The caller (dax_translator.py)
        is responsible for surfacing that category so a human reviews this
        specific metric before trusting it -- this method only decides
        whether to ship the approximation, never hides that it did.
        """
        if not self._allow_unshifted_fallback:
            raise self.DaxRenderError(error_message)
        if ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK not in self._advisory_categories:
            self._advisory_categories.append(ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK)
        return agg_sql

    def _render_lag_period(
        self, args: List[DaxNode], interval: str, amount: int
    ) -> str:
        """
        Translate SAMEPERIODLASTYEAR / PREVIOUSxxx to scalar CASE WHEN bounded aggregates.

        Snowflake METRICS clause does NOT allow window functions (OVER / LAG).
        """
        if not args:
            raise self.DaxRenderError("Period function requires arguments")
        if len(args) != 2:
            raise self.DaxRenderError("Period function expects (aggregate, date_column) arguments")

        d = self.date_alias
        date_col = f'{d}."COL_DATE"'
        # No flag column provisioned for this shape — anchor to CURRENT_DATE()
        # rather than the enriched-view-only MAX_DATE column, matching
        # _render_period_to_date's fallback above and the already-established
        # dax_rule_translator.py/connectors/translator.py pattern (commit
        # e4c8322). Every use of `max_date` below (direct-aggregate fallback,
        # nested-composition detection/shift, and the "inject a new
        # condition" fallback) stays correct with this substitution: it's
        # the same anchor expression used consistently both for rendering
        # and for detecting an inner call's own anchor to shift.
        max_date = "CURRENT_DATE()"
        agg_node = args[0]
        lag_component = self._LAG_COMPONENT[interval]
        base_flag = self.anchor_flag_map.get((lag_component,))

        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else f'{self.table_alias}."UNITS"'
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            cast = "::FLOAT" if inner_func in ("SUM", "AVERAGE") else ""

            if base_flag:
                condition_sql = f'{self.table_alias}."{base_flag}"'
            elif interval == "year":
                condition_sql = (
                    f"YEAR({date_col}) = YEAR({max_date}) - 1 "
                    f"AND {date_col} BETWEEN DATEADD(YEAR, -1, DATE_TRUNC('YEAR', {max_date})) "
                    f"AND DATEADD(YEAR, -1, {max_date})"
                )
            elif interval == "quarter":
                condition_sql = (
                    f"YEAR({date_col}) = YEAR(DATEADD(QUARTER, -1, {max_date})) "
                    f"AND QUARTER({date_col}) = QUARTER(DATEADD(QUARTER, -1, {max_date}))"
                )
            else:  # month
                condition_sql = (
                    f"YEAR({date_col}) = YEAR(DATEADD(MONTH, -1, {max_date})) "
                    f"AND MONTH({date_col}) = MONTH(DATEADD(MONTH, -1, {max_date}))"
                )
            return f"{sql_func}(CASE WHEN {condition_sql} THEN {col_sql}{cast} END)"

        # Base argument isn't a direct aggregation call — e.g. a measure
        # reference such as SAMEPERIODLASTYEAR([Total Sales], 'Date'[Date]).
        agg_sql = self._render_node(agg_node)
        shift_expr = {
            "year": f"DATEADD(YEAR, -1, {max_date})",
            "quarter": f"DATEADD(QUARTER, -1, {max_date})",
            "month": f"DATEADD(MONTH, -1, {max_date})",
        }[interval]

        if self.anchor_flag_map:
            # Shape-flag mode: a boolean flag column can't be shifted with
            # DATEADD the way a literal date value can (see the MAX_DATE
            # text-substitution branch below, which this replaces) — so
            # nested composition is resolved by finding which OTHER shape's
            # flag reference the already-rendered inner SQL contains, and
            # swapping it for the combined shape's own flag reference
            # instead. inner_shape + (lag_component,) is exactly the same
            # canonical shape converter/time_intelligence_shapes.py's
            # discovery pass would have derived for this composition, so
            # the enriched view must already carry that column whenever
            # this substitution is reachable.
            for inner_shape, inner_flag in self.anchor_flag_map.items():
                inner_ref = f'{self.table_alias}."{inner_flag}"'
                if inner_ref not in agg_sql:
                    continue
                combined_flag = self.anchor_flag_map.get(inner_shape + (lag_component,))
                if combined_flag:
                    return agg_sql.replace(inner_ref, f'{self.table_alias}."{combined_flag}"')
                # The inner expression already has its own date-window flag,
                # but no flag was provisioned for the combined (nested)
                # shape. Unlike the MAX_DATE case, this must not silently
                # fall through to the "no existing anchor" branch below —
                # that branch assumes agg_sql has NO date window of its own,
                # which is false here (it's opaque, but real), and ANDing an
                # unrelated new condition around an opaque flag reference
                # risks the exact always-empty-CASE contradiction this whole
                # mechanism exists to avoid. Fail closed instead.
                raise self.DaxRenderError(
                    f"Period function needs a flag column for the nested shape "
                    f"{inner_shape + (lag_component,)!r}, but none was provisioned — "
                    "the shape-discovery pass and the enriched view must agree on "
                    "every nested time-intelligence composition actually used"
                )

            if base_flag:
                rewritten = self._inject_case_filter_into_rendered_aggregate(
                    agg_sql, f'{self.table_alias}."{base_flag}"'
                )
                if rewritten:
                    return rewritten
                return self._lag_period_unshifted_fallback_or_raise(
                    agg_sql,
                    f"Period function base expression '{agg_sql}' is not a simple aggregate this "
                    "renderer can safely apply a date-range filter to without risking a nested aggregate",
                )
            # No flag provisioned for this exact shape (discovery pass and
            # enrichment disagree, or this shape wasn't anticipated) — fall
            # through to the inline MAX_DATE logic below rather than emit
            # something silently wrong.

        if max_date in agg_sql:
            # The referenced measure's own SQL already anchors a date
            # window to CURRENT_DATE() (e.g. TOTALYTD's own "date <=
            # CURRENT_DATE()" upper bound) — shifting every reference to
            # that anchor by the same lag period moves the WHOLE window
            # back together, which is what SAMEPERIODLASTYEAR/PREVIOUSxxx
            # of an already-date-bounded measure actually means. ANDing a
            # second, unshifted "is this row from last year" condition
            # around an inner window still bounded to *this* year's anchor
            # would be self-contradictory — the two conditions can never
            # both be true (confirmed for TOTALYTD-wrapped-in-
            # SAMEPERIODLASTYEAR, which is exactly how this bug manifested:
            # an always-empty CASE WHEN, never a crash, so it never
            # surfaced as a translation failure).
            return agg_sql.replace(max_date, shift_expr)

        legacy_max_date = f'{self.table_alias}."MAX_DATE"'
        if legacy_max_date in agg_sql:
            # The inner expression was rendered by an earlier translation of
            # this same fallback, from before CURRENT_DATE() replaced the
            # enriched-view-only MAX_DATE column as the no-flag default (see
            # _render_period_to_date's comment above) — e.g. a
            # measure_sql_map entry sourced from a persisted sql_expression
            # translated before this fix. Detect and shift that legacy
            # anchor too, exactly like the CURRENT_DATE() case above,
            # rather than treating the inner expression as anchor-less and
            # ANDing a second, unshifted condition around it — the same
            # self-contradictory, always-empty-CASE bug the branch above
            # exists to avoid, just reachable here via a generation
            # mismatch (mixed old/new sql_expression values) instead of a
            # missing flag.
            legacy_shift_expr = {
                "year": f"DATEADD(YEAR, -1, {legacy_max_date})",
                "quarter": f"DATEADD(QUARTER, -1, {legacy_max_date})",
                "month": f"DATEADD(MONTH, -1, {legacy_max_date})",
            }[interval]
            return agg_sql.replace(legacy_max_date, legacy_shift_expr)

        # No existing anchor to shift — the referenced measure has no date
        # window of its own (e.g. a plain SUM with no date filter at all),
        # so there is nothing to conflict with. Inject a new period
        # condition instead (same pattern as _render_period_to_date's
        # measure-reference fallback for TOTALYTD/MTD/QTD).
        if interval == "year":
            condition_sql = (
                f"YEAR({date_col}) = YEAR({max_date}) - 1 "
                f"AND {date_col} BETWEEN DATEADD(YEAR, -1, DATE_TRUNC('YEAR', {max_date})) "
                f"AND {shift_expr}"
            )
        elif interval == "quarter":
            condition_sql = (
                f"YEAR({date_col}) = YEAR({shift_expr}) "
                f"AND QUARTER({date_col}) = QUARTER({shift_expr})"
            )
        else:
            condition_sql = (
                f"YEAR({date_col}) = YEAR({shift_expr}) "
                f"AND MONTH({date_col}) = MONTH({shift_expr})"
            )
        rewritten = self._inject_case_filter_into_rendered_aggregate(agg_sql, condition_sql)
        if rewritten:
            return rewritten
        return self._lag_period_unshifted_fallback_or_raise(
            agg_sql,
            f"Period function base expression '{agg_sql}' is not a simple aggregate this "
            "renderer can safely apply a date-range filter to without risking a nested aggregate",
        )

    def _render_dateadd(self, args: List[DaxNode]) -> str:
        """Translate DATEADD(dates, n_intervals, interval) to Snowflake DATEADD."""
        if len(args) < 3:
            raise self.DaxRenderError("DATEADD requires 3 arguments")
        # DAX signature: DATEADD(<dates>, <number>, <interval>)
        date_col = self._render_node(args[0])
        amount = self._render_node(args[1])
        interval_node = args[2]
        interval_str = (
            interval_node.name.lower()
            if isinstance(interval_node, IdentifierNode)
            else str(interval_node)
        )
        return f"DATEADD({interval_str}, {amount}, {date_col})"


# ---------------------------------------------------------------------------
# Convenience function used by DAXTranslator
# ---------------------------------------------------------------------------


def try_ast_translate(
    dax: str,
    table_alias: str,
    date_alias: str = "COL_DATE",
    measure_sql_map: Optional[Dict[str, str]] = None,
    known_measure_names: Optional[Any] = None,
    anchor_flag_map: Optional[Dict[Tuple[str, ...], str]] = None,
    primary_table_name: Optional[str] = None,
    date_table_names: Optional[Any] = None,
    allow_unshifted_fallback: bool = False,
    advisory_categories: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Attempt to translate a DAX expression to Snowflake SQL via AST parsing.

    This is the entry-point called by DAXTranslator for Tier 3/4 expressions
    that cannot be handled by the fast-path regex approach.

    Args:
        known_measure_names: names of measures the caller's model tracks
            (whether or not they have resolved SQL yet). Lets the renderer
            fail closed on a genuinely unresolved measure reference while
            still falling back to a column reference for bracket names it
            has no measure-registry knowledge of at all (the common
            SUM([Column]) shape). Omit (or pass an empty collection) to
            preserve the historical "unknown bracket name -> column"
            fallback for every bracket reference, e.g. for callers with no
            measure registry to consult.
        anchor_flag_map: shape tuple -> precomputed flag column name (see
            converter/time_intelligence_shapes.py). Omit (or pass empty) to
            preserve the historical inline-MAX_DATE rendering exactly —
            required for the schema-blind dry-run/preview path, which has
            no live enriched view to reference flags on.
        primary_table_name: the DAX table name (e.g. "KPI") that
            table_alias represents for this metric. Enables table-aware
            resolution of 'TableName'[Column] references inside CALCULATE/
            FILTER predicates: a column named on a table that is neither
            this one nor date_table_names now fails closed (DaxRenderError,
            caught by renderer.render -> None) instead of silently
            qualifying it with table_alias — the bug where a filter like
            'Date'[Running Year]=1 on a KPI-table measure rendered as
            kpi."RUNNING_YEAR" (wrong table) rather than the date table's
            own column. Omit to preserve the historical behavior for
            callers not yet passing it.
        date_table_names: DAX names recognized as the date/calendar
            dimension for alias resolution (default: {"date", "calendar"}).
            Only consulted when primary_table_name is provided.
        allow_unshifted_fallback: opt-in only (default False preserves
            historical fail-closed behavior for every existing caller). A
            lag-period function (SAMEPERIODLASTYEAR/PREVIOUSYEAR/PREVIOUSMONTH/
            PREVIOUSQUARTER) wrapping a measure reference whose own SQL
            isn't a shape the renderer can safely apply a date-range filter
            to (e.g. a nested aggregate) normally fails closed (returns
            None). Set True to instead ship that measure's own unmodified
            SQL — real, valid, but the CURRENT period's value, not shifted
            to the requested prior period — and record
            ADVISORY_CATEGORY_LAG_PERIOD_UNSHIFTED_FALLBACK in
            `advisory_categories` so the caller can flag it for human
            review instead of silently trusting it.
        advisory_categories: caller-provided list, appended to in place
            (same pattern as DropLedger elsewhere in this codebase) with
            any advisory category codes produced by this translation
            attempt. Ignored unless allow_unshifted_fallback is True.

    Returns:
        SQL string on success, None on failure.
    """
    cache_key = _ast_cache_key(
        dax,
        table_alias,
        date_alias,
        measure_sql_map,
        known_measure_names,
        anchor_flag_map,
        primary_table_name,
        date_table_names,
        allow_unshifted_fallback,
    )
    if cache_key in _AST_CACHE:
        cached_sql, cached_categories = _AST_CACHE[cache_key]
        if advisory_categories is not None:
            for category in cached_categories:
                if category not in advisory_categories:
                    advisory_categories.append(category)
        return cached_sql

    parser = DaxAstParser()
    ast = parser.parse(dax)
    if ast is None:
        _AST_CACHE[cache_key] = (None, ())
        return None

    renderer = DaxSqlRenderer(
        table_alias=table_alias,
        date_alias=date_alias,
        measure_sql_map=measure_sql_map or {},
        known_measure_names=known_measure_names,
        anchor_flag_map=anchor_flag_map,
        primary_table_name=primary_table_name,
        date_table_names=date_table_names,
        allow_unshifted_fallback=allow_unshifted_fallback,
    )
    sql = renderer.render(ast)
    if len(_AST_CACHE) >= _AST_CACHE_MAX:
        _AST_CACHE.clear()
    produced_categories = tuple(renderer._advisory_categories)
    _AST_CACHE[cache_key] = (sql, produced_categories)
    if advisory_categories is not None:
        for category in produced_categories:
            if category not in advisory_categories:
                advisory_categories.append(category)
    return sql


# ---------------------------------------------------------------------------
# Root-shape classifiers — used to distinguish "cannot translate this DAX"
# from "this DAX isn't a valid aggregate metric to begin with" (a modeling
# classification, not a translation gap). Both are pure AST-shape checks:
# no field/model/dataset names are ever inspected.
# ---------------------------------------------------------------------------

# Shared marker prefix for sync_failure_reason on by-design-excluded metrics.
# Any later pass that might otherwise re-attempt translating an unresolved
# metric (Tier-5 batching, cross-metric dependency resolution, etc.) must
# check for this prefix and skip — these metrics were never meant to be
# translated, so re-attempting could silently un-exclude them.
BY_DESIGN_EXCLUDED_PREFIX = "BY_DESIGN_EXCLUDED"


def is_by_design_excluded(sync_failure_reason: Optional[str]) -> bool:
    """True if `sync_failure_reason` marks a metric as by-design excluded
    (constant expression / string-producing root) rather than a genuine,
    still-open DAX translation failure."""
    return str(sync_failure_reason or "").startswith(BY_DESIGN_EXCLUDED_PREFIX)


_STRING_PRODUCING_FUNCS = {
    "CONCATENATE", "LEFT", "RIGHT", "MID", "FORMAT", "SUBSTITUTE", "REPT",
    "TRIM", "UPPER", "LOWER",
}


def dax_root_is_string_producing(dax: str) -> bool:
    """
    True if the DAX expression's root/outermost operation produces a string
    value — a bare string literal, or a call to a string-producing function
    — rather than a numeric aggregate.

    Such an expression cannot be represented in a Snowflake semantic-view
    METRICS clause (which requires an aggregate expression); it should be
    classified as a dimension/attribute instead, not attempted as a metric
    translation.
    """
    node = DaxAstParser().parse(dax)
    if isinstance(node, LiteralNode):
        return isinstance(node.value, str)
    if isinstance(node, FunctionCallNode):
        return node.func.upper() in _STRING_PRODUCING_FUNCS
    return False


def dax_has_zero_data_dependencies(dax: str) -> bool:
    """
    True if the DAX expression references no column, measure, or table
    anywhere in its parse tree — a compile-time constant (e.g. a bare
    string/numeric literal, or arithmetic over literals only).

    Such an expression has nothing to aggregate and should be classified as
    by-design excluded, not attempted as a translation or reported as a
    DAX-translation failure.
    """
    node = DaxAstParser().parse(dax)
    if node is None:
        return False

    def _refs_data(n: DaxNode) -> bool:
        if isinstance(n, (ColumnRefNode, MeasureRefNode)):
            return True
        if isinstance(n, BinaryOpNode):
            return _refs_data(n.left) or _refs_data(n.right)
        if isinstance(n, UnaryOpNode):
            return _refs_data(n.operand)
        if isinstance(n, FunctionCallNode):
            return any(_refs_data(a) for a in n.args)
        return False

    return not _refs_data(node)


_CONTEXT_TRANSITION_REASON = (
    "DAX ALLEXCEPT/ALL requires re-partitioning the aggregate "
    "independently of the query's own grouping (a SQL window "
    "function) — Snowflake's semantic-view METRICS clause does not "
    "support window functions, and no derived-metric/precomputed-"
    "column materialization exists yet for this pattern."
)


def _calculate_has_direct_all_or_allexcept_modifier(node: DaxNode) -> bool:
    """True if any CALCULATE(...) anywhere in the tree has ALL(...) or
    ALLEXCEPT(...) as one of its own direct filter-modifier arguments —
    the exact shape DaxSqlRenderer._render_calculate treats as a
    context-transition/window-function case (sets is_window=True).

    Deliberately NOT a text/regex search for "ALL(" or "ALLEXCEPT("
    anywhere in the expression: ALL(...) also appears legitimately nested
    inside FILTER(...)'s own first argument (e.g. CALCULATE(measure,
    FILTER(ALL(table), predicate))) — DAX's idiom for "filter the whole
    table regardless of context," a normal, different, sometimes-
    resolvable filtered-aggregate pattern, not a window-function one. Only
    an ALL/ALLEXCEPT that CALCULATE sees as its own direct argument
    triggers the window-function path; walking the same structure
    _render_calculate itself dispatches on keeps this classifier exactly
    consistent with the renderer's actual behavior.
    """
    if isinstance(node, FunctionCallNode):
        if node.func == "CALCULATE":
            for filter_arg in node.args[1:]:
                if isinstance(filter_arg, FunctionCallNode) and filter_arg.func in ("ALL", "ALLEXCEPT"):
                    return True
        return any(_calculate_has_direct_all_or_allexcept_modifier(a) for a in node.args)
    if isinstance(node, BinaryOpNode):
        return (
            _calculate_has_direct_all_or_allexcept_modifier(node.left)
            or _calculate_has_direct_all_or_allexcept_modifier(node.right)
        )
    if isinstance(node, UnaryOpNode):
        return _calculate_has_direct_all_or_allexcept_modifier(node.operand)
    return False


def dax_context_transition_failure_reason(dax: str) -> Optional[str]:
    """
    Returns a specific, accurate failure reason if `dax` contains
    CALCULATE(agg, ALL(...)) or CALCULATE(agg, ALLEXCEPT(...)) — a genuine
    DAX context-transition pattern with no SQL equivalent inside
    Snowflake's semantic-view METRICS clause. ALLEXCEPT/ALL mean
    "recompute this aggregate re-partitioned independently of whatever
    dimensions the query itself groups by" — inherently a SQL window
    function, which Snowflake's METRICS clause forbids (see
    DaxSqlRenderer._render_calculate, which fails closed on this shape
    rather than emitting one). This codebase has no derived-metric/
    precomputed-column mechanism to materialize the windowed value some
    other way, so the pattern is genuinely unsupported today, not just
    untranslated.

    Returns None for any other DAX shape (including ALL(...) nested inside
    FILTER(...), a different and sometimes-resolvable idiom — see
    _calculate_has_direct_all_or_allexcept_modifier), so callers fall back
    to their own generic translation-failure message.
    """
    if not dax:
        return None
    node = DaxAstParser().parse(dax)
    if node is None:
        return None
    if _calculate_has_direct_all_or_allexcept_modifier(node):
        return _CONTEXT_TRANSITION_REASON
    return None


_LAG_PERIOD_FUNCS = ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER")


def _calculate_lag_period_of_measure_reference(node: DaxNode) -> Optional[str]:
    """Returns the referenced measure's name if any CALCULATE(...)
    anywhere in the tree wraps a bare measure reference (not a direct
    aggregate call) with a SAMEPERIODLASTYEAR/PREVIOUSxxx filter modifier
    as its own direct argument — the shape DaxSqlRenderer._render_lag_period's
    measure-reference fallback handles by inlining that measure's own
    resolved SQL (shifting its date anchor, or injecting a new period
    filter if it has none). Mirrors
    _calculate_has_direct_all_or_allexcept_modifier's AST-walk approach —
    a text/regex search would risk matching the function name appearing
    nested somewhere unrelated.
    """
    if isinstance(node, FunctionCallNode):
        if node.func == "CALCULATE" and node.args:
            base = node.args[0]
            if isinstance(base, MeasureRefNode):
                for filter_arg in node.args[1:]:
                    if isinstance(filter_arg, FunctionCallNode) and filter_arg.func in _LAG_PERIOD_FUNCS:
                        return base.name
        for a in node.args:
            found = _calculate_lag_period_of_measure_reference(a)
            if found:
                return found
        return None
    if isinstance(node, BinaryOpNode):
        return (
            _calculate_lag_period_of_measure_reference(node.left)
            or _calculate_lag_period_of_measure_reference(node.right)
        )
    if isinstance(node, UnaryOpNode):
        return _calculate_lag_period_of_measure_reference(node.operand)
    return None


def dax_lag_period_of_measure_reference_failure_reason(dax: str) -> Optional[str]:
    """
    Returns a specific, accurate failure reason if `dax` contains
    CALCULATE([SomeMeasure], SAMEPERIODLASTYEAR(...)/PREVIOUSYEAR(...)/
    PREVIOUSMONTH(...)/PREVIOUSQUARTER(...)) — a measure reference (not a
    direct aggregate) wrapped in a prior-period filter.

    Translating this requires inlining the referenced measure's own
    resolved SQL and shifting its date anchor (or, if it has none,
    injecting a new period filter) — see
    DaxSqlRenderer._render_lag_period. If this still failed to translate,
    the referenced measure either hasn't been resolved yet (a normal,
    later-pass-clears-it deferral) or its SQL shape was too complex to
    safely rewrite — either way, "DAX translation failed" alone doesn't
    say why, and this used to fail differently and worse: emitting a bare
    reference to the other metric by name (rejected by Snowflake with "a
    metric must directly refer to another aggregate-level expression") or
    a self-contradictory always-empty filter, neither of which is this
    function's concern to restate — it only describes the current,
    corrected failure mode.

    Returns None for any other DAX shape, so callers fall back to their
    own generic translation-failure message.
    """
    if not dax:
        return None
    node = DaxAstParser().parse(dax)
    if node is None:
        return None
    measure_name = _calculate_lag_period_of_measure_reference(node)
    if measure_name is None:
        return None
    return (
        f"DAX SAMEPERIODLASTYEAR/PREVIOUSYEAR/PREVIOUSMONTH/PREVIOUSQUARTER wraps "
        f"measure [{measure_name}] rather than a direct aggregate — requires that "
        f"measure's own resolved SQL to inline and shift its date window. Not yet "
        f"resolvable: either [{measure_name}] hasn't been translated yet, or its "
        f"SQL shape is too complex to safely rewrite."
    )


# ---------------------------------------------------------------------------
# CALCULATE-through-a-disconnected-dimension detection
# ---------------------------------------------------------------------------
#
# The DAX idiom this section detects: CALCULATE(measure_or_agg, <filter on
# TableX>) where the metric declaring this expression is itself based on a
# table with no relationship path to TableX. In DAX this is completely
# normal — CALCULATE's filter arguments apply through the model's whole
# evaluation context, not through the calling measure's own relationship
# path — but Snowflake's semantic-view compiler binds each METRICS-clause
# entry to ONE base-table alias and validates every column reference inside
# its expression against THAT table's relationship graph, independent of
# how the SQL is shaped (rejected with "A metric cannot refer to another
# dimension from an unrelated entity", error 010211). No amount of
# expression-level rewriting fixes this — DaxSqlRenderer._render_calculate
# already rewrites CALCULATE([Measure], filter) into a CASE-based filtered
# aggregate (see _inject_case_filter_into_rendered_aggregate), and the
# result still gets rejected, because the problem is which table the
# metric is declared under, not the shape of its SQL text. This is
# therefore a DETECTOR only, producing a precise, well-explained reason —
# not a rewriter (see Docs/decisions or the KPI01/KPI02 investigation this
# was written for).


# Stable category code for this failure class, carried on
# UnreachableDimensionFilter.category and mirrored onto
# SMLMetric.advisory_categories by osi_to_sml.py so downstream consumers
# (project_mapping_engine.py's predicted-failure status computation) can
# key off this structural signal instead of matching the free-text reason
# string. Not specific to any one model's metric names (e.g. KPI01/KPI02) —
# it fires for any metric on any model whose CALCULATE(...) filters an
# unreachable table.
ADVISORY_CATEGORY_UNREACHABLE_DIMENSION = "unreachable_dimension"


@dataclass
class UnreachableDimensionFilter:
    """A CALCULATE(...) filter that references a dimension with no
    relationship path from the calling metric's own base table — the
    shape that will fail Snowflake's semantic-view compiler regardless of
    how the expression is translated."""

    filtered_table: str
    referenced_measure: Optional[str]
    referenced_measure_dataset: Optional[str]
    # None when referenced_measure is unset/unresolvable; otherwise True if
    # THAT measure's own base table DOES have a path to filtered_table
    # (the common, fixable shape: the metric is just declared under the
    # wrong table), False if nothing in the model reaches filtered_table
    # from either table (the KPI01/KPI02 shape: no fix short of adding a
    # relationship that may not legitimately exist in the source data).
    referenced_measure_has_path: Optional[bool]
    reason: str
    category: str = ADVISORY_CATEGORY_UNREACHABLE_DIMENSION


def _find_all_calculate_nodes(node: DaxNode) -> List[FunctionCallNode]:
    """Every CALCULATE(...) FunctionCallNode anywhere in the tree, however
    deeply nested — a metric can contain more than one (e.g. inside
    separate branches of an IF/SWITCH selector, exactly the KPI01/KPI02
    shape: IF(SUM('KPI'[KPI])=N, ..., CALCULATE(...), ...))."""
    found: List[FunctionCallNode] = []

    def _walk(n: DaxNode) -> None:
        if isinstance(n, FunctionCallNode):
            if n.func == "CALCULATE":
                found.append(n)
            for a in n.args:
                _walk(a)
        elif isinstance(n, BinaryOpNode):
            _walk(n.left)
            _walk(n.right)
        elif isinstance(n, UnaryOpNode):
            _walk(n.operand)

    _walk(node)
    return found


def _collect_column_ref_tables(node: DaxNode) -> Set[str]:
    """Every distinct table name referenced by a ColumnRefNode anywhere in
    this subtree — deliberately walks the WHOLE filter-argument subtree
    (not just a single top-level comparison), so compound filters
    (FILTER(table, predicate), AND/OR-composed comparisons) are covered,
    not just the single direct-comparison shape KPI01/KPI02 happens to
    use."""
    tables: Set[str] = set()

    def _walk(n: DaxNode) -> None:
        if isinstance(n, ColumnRefNode):
            if n.table:
                tables.add(n.table)
        elif isinstance(n, BinaryOpNode):
            _walk(n.left)
            _walk(n.right)
        elif isinstance(n, UnaryOpNode):
            _walk(n.operand)
        elif isinstance(n, FunctionCallNode):
            for a in n.args:
                _walk(a)

    _walk(node)
    return tables


def dax_calculate_filters_unreachable_dimension(
    dax: str,
    calling_dataset: str,
    relationships: Iterable[Any],
    metric_datasets: Optional[Dict[str, str]] = None,
) -> Optional["UnreachableDimensionFilter"]:
    """Detect a CALCULATE(...) filter that references a table unreachable
    from `calling_dataset` via `relationships` — the shape that will fail
    Snowflake's semantic-view compiler as "a metric cannot refer to
    another dimension from an unrelated entity," no matter how the
    expression itself is translated (see module-level comment above).

    Args:
        dax: the metric's raw DAX expression.
        calling_dataset: the dataset/table this metric is (or would be)
            declared under in the target semantic view.
        relationships: the model's relationship list (anything
            utils.relationship_graph.find_relationship_path accepts —
            e.g. SMLModel.relationships).
        metric_datasets: optional {metric_unique_name: dataset_name} map,
            used only to enrich the result with whether the measure
            CALCULATE's first argument references (when it's a bare
            measure reference) has its OWN path to the filtered table —
            distinguishes "this metric is just declared under the wrong
            table" (fixable by re-anchoring or relating those two tables)
            from "nothing in this model reaches that table at all" (not
            fixable without a relationship that may not legitimately
            exist in the source model). Omit if unavailable — detection
            of the core problem doesn't depend on it.

    Returns the FIRST such filter found (walking CALCULATE nodes in
    document order), or None if no CALCULATE in this expression filters by
    an unreachable table — including the ordinary case where every
    CALCULATE's filters stay within calling_dataset's own reachable graph.
    """
    if not dax or not calling_dataset:
        return None
    node = DaxAstParser().parse(dax)
    if node is None:
        return None

    metric_datasets_ci = {str(k).casefold(): v for k, v in (metric_datasets or {}).items()}

    for calc_node in _find_all_calculate_nodes(node):
        if not calc_node.args:
            continue
        filtered_tables: Set[str] = set()
        for filter_arg in calc_node.args[1:]:
            filtered_tables |= _collect_column_ref_tables(filter_arg)

        for filtered_table in sorted(filtered_tables):
            if has_relationship_path(relationships, calling_dataset, filtered_table):
                continue  # reachable -- not the failure shape this detects

            referenced_measure: Optional[str] = None
            referenced_measure_dataset: Optional[str] = None
            referenced_measure_has_path: Optional[bool] = None
            base = calc_node.args[0]
            if isinstance(base, MeasureRefNode):
                referenced_measure = base.name
                referenced_measure_dataset = metric_datasets_ci.get(str(base.name).casefold())
                if referenced_measure_dataset:
                    referenced_measure_has_path = has_relationship_path(
                        relationships, referenced_measure_dataset, filtered_table
                    )

            reason = (
                f"This metric's CALCULATE(...) filters by table '{filtered_table}', but "
                f"'{calling_dataset}' (this metric's own base table) has no relationship "
                f"path to '{filtered_table}'. Snowflake's semantic-view compiler will "
                f"reject this as \"a metric cannot refer to another dimension from an "
                f"unrelated entity\" (error 010211) regardless of how the DAX is "
                f"translated — the SQL expression's shape isn't the problem, the missing "
                f"relationship path is."
            )
            if referenced_measure:
                if referenced_measure_has_path:
                    reason += (
                        f" '{referenced_measure}' (the measure this CALCULATE wraps, "
                        f"declared on '{referenced_measure_dataset}') DOES have a path to "
                        f"'{filtered_table}' — this metric may be declared under the wrong "
                        f"table; re-anchoring it to '{referenced_measure_dataset}', or "
                        f"adding a relationship between '{calling_dataset}' and "
                        f"'{referenced_measure_dataset}', would resolve this."
                    )
                elif referenced_measure_dataset:
                    reason += (
                        f" '{referenced_measure}' (declared on "
                        f"'{referenced_measure_dataset}') doesn't reach '{filtered_table}' "
                        f"either — no relationship anywhere in this model connects any of "
                        f"these tables."
                    )

            return UnreachableDimensionFilter(
                filtered_table=filtered_table,
                referenced_measure=referenced_measure,
                referenced_measure_dataset=referenced_measure_dataset,
                referenced_measure_has_path=referenced_measure_has_path,
                reason=reason,
            )

    return None


# Distinct from ADVISORY_CATEGORY_UNREACHABLE_DIMENSION: that one fires when
# NO relationship path exists (a real, unfixable-without-a-new-relationship
# 010211 failure). This one fires for the opposite outcome -- a relationship
# path DOES exist -- for a metric whose translation nonetheless failed today,
# because dry-run's translators can't yet exploit that path (see
# dax_calculate_filters_reachable_dimension_pending_enrichment's docstring).
# Mirrored onto SMLMetric.advisory_categories by osi_to_sml.py and mapped to
# STATUS_NEEDS_REVIEW (not STATUS_PREDICTED_FAILURE) by
# project_mapping_engine.py, since a real deploy has a genuine chance of
# resolving this where dry-run could not.
ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT = "pending_live_schema_enrichment"


@dataclass
class ReachableDimensionFilterPendingEnrichment:
    """A CALCULATE(...) filter that references a dimension WITH a
    relationship path from the calling metric's own base table, whose
    translation still failed -- see module docstring above
    ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT."""

    filtered_table: str
    reason: str
    category: str = ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT


def dax_calculate_filters_reachable_dimension_pending_enrichment(
    dax: str,
    calling_dataset: str,
    relationships: Iterable[Any],
) -> Optional["ReachableDimensionFilterPendingEnrichment"]:
    """Detect a CALCULATE(...) filter that references a table OTHER than
    `calling_dataset`, but one THAT IS reachable from it via
    `relationships` -- the shape dry-run's deterministic AST renderer
    declines (DaxSqlRenderer._resolve_table_alias refuses to guess a
    cross-table SQL alias) and Tier-5's LLM fallback also declines (its
    prompt is never given relationship/alias information for any table
    but the metric's own, so it correctly treats reachability as
    "unclear" and emits a NULL-cast placeholder per its own verification
    checklist).

    Unlike that failure, this one has a real fix path at real-deploy time:
    connectors/metrics_clause_builder.py's precomputed-cross-dataset-
    column rewrite (_rewrite_cross_dataset_dax_refs_to_precomputed) runs
    against the live-schema-derived enriched view -- built only when a
    real Snowflake connection is available -- and can resolve exactly
    this reference by substituting a same-table precomputed column before
    handing the DAX back to the same deterministic renderer for a second,
    easier attempt. Dry-run has no live connection, so it can never
    exercise that rewrite; a metric hitting this shape may well succeed
    at real deploy even though dry-run could not translate it.

    Only called (see osi_to_sml.py's
    _flag_reachable_dimension_filters_pending_enrichment) for a metric
    whose translation already failed -- if some tier already produced
    valid SQL, this note would be misleading noise, not a signal.

    Returns the FIRST such filter found (walking CALCULATE nodes in
    document order), or None if no CALCULATE in this expression filters
    by a reachable-but-different table — including the ordinary
    same-table case and the unreachable case
    (dax_calculate_filters_unreachable_dimension's shape, not this one).
    """
    if not dax or not calling_dataset:
        return None
    node = DaxAstParser().parse(dax)
    if node is None:
        return None

    calling_dataset_cf = str(calling_dataset).strip().strip("'").casefold()

    for calc_node in _find_all_calculate_nodes(node):
        if not calc_node.args:
            continue
        filtered_tables: Set[str] = set()
        for filter_arg in calc_node.args[1:]:
            filtered_tables |= _collect_column_ref_tables(filter_arg)

        for filtered_table in sorted(filtered_tables):
            if str(filtered_table or "").strip().strip("'").casefold() == calling_dataset_cf:
                continue  # same table -- not a cross-table reference at all
            if not has_relationship_path(relationships, calling_dataset, filtered_table):
                continue  # unreachable -- dax_calculate_filters_unreachable_dimension's shape, not this one

            reason = (
                f"This metric's CALCULATE(...) filters by table '{filtered_table}', "
                f"which IS reachable from '{calling_dataset}' via a declared "
                f"relationship, but dry-run's translators cannot resolve a "
                f"cross-table reference without a live connection's schema "
                f"enrichment. This metric may succeed at real deploy time even "
                f"though it could not be translated here — review before "
                f"assuming it will fail."
            )
            return ReachableDimensionFilterPendingEnrichment(
                filtered_table=filtered_table,
                reason=reason,
            )

    return None


# ---------------------------------------------------------------------------
# Disconnected-selector-table decomposition
# ---------------------------------------------------------------------------
# A distinct, THIRD shape from the two detectors above: a metric declared on
# a small selector/parameter table (e.g. a Power BI "field parameter"/
# "what-if" table with no real business key) whose DAX is an IF-chain that
# switches between OTHER tables' real aggregates based on the selector's own
# value -- e.g. IF(SUM('KPI'[KPI])=1, [MeasureA], IF(SUM('KPI'[KPI])=2, " ",
# CALCULATE([MeasureB], 'Date'[Year]=1))). DAX can do this with no
# relationship at all, because each branch evaluates independently in its
# own filter context and the results are just combined afterward -- but a
# single Snowflake METRICS-clause expression cannot: it must be one SQL
# aggregate validated against ONE base table's relationship graph, and
# there is no relationship to declare here (the selector table is
# deliberately disconnected, not merely mis-anchored).
#
# Unlike ADVISORY_CATEGORY_UNREACHABLE_DIMENSION (a dead end -- no fix
# short of a relationship that may not legitimately exist) this shape is
# actually resolvable: every non-trivial branch, with its selector
# condition stripped away, is a self-contained expression that doesn't
# reference the selector table at all -- e.g. `[MeasureB]` filtered by
# 'Date'[Year]=1 only needs SalesFact/Date, never KPI. Decomposing each
# branch into its OWN standalone metric lets those real values deploy
# normally; the original metric still can't be one metric (recorded as an
# advisory, not silently dropped), and a human recreates the selector
# switch itself one layer up, in the reporting tool, using the new
# per-branch metrics.
ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED = "disconnected_selector_decomposed"


@dataclass
class DecomposedSelectorBranch:
    """One branch extracted from a disconnected-selector IF-chain, ready to
    become its own standalone metric."""

    condition_dax: str  # human-readable, e.g. "SUM('KPI'[KPI])=1" or "otherwise" for a terminal else
    branch_dax: str      # best-effort re-serialized DAX text for the branch's own value expression
    branch_dataset: str  # the single, unambiguous dataset this branch's expression is anchored on


@dataclass
class DisconnectedSelectorDecomposition:
    selector_table: str
    branches: List[DecomposedSelectorBranch]
    skipped_trivial_count: int  # e.g. a bare " " literal branch -- nothing to decompose there


def _walk_if_chain(node: DaxNode) -> Tuple[List[Tuple[DaxNode, DaxNode]], Optional[DaxNode]]:
    """Follow a right-linear chain of IF(cond, true_val, next_if_or_else)
    calls. Returns (list of (cond, true_val) pairs in order, terminal else
    node or None if the chain ends without one / stops at a non-IF node)."""
    pairs: List[Tuple[DaxNode, DaxNode]] = []
    current: Optional[DaxNode] = node
    while isinstance(current, FunctionCallNode) and current.func == "IF" and len(current.args) >= 2:
        pairs.append((current.args[0], current.args[1]))
        current = current.args[2] if len(current.args) >= 3 else None
    return pairs, current


def _node_contains_measure_ref(node: DaxNode) -> bool:
    if isinstance(node, MeasureRefNode):
        return True
    if isinstance(node, BinaryOpNode):
        return _node_contains_measure_ref(node.left) or _node_contains_measure_ref(node.right)
    if isinstance(node, UnaryOpNode):
        return _node_contains_measure_ref(node.operand)
    if isinstance(node, FunctionCallNode):
        return any(_node_contains_measure_ref(a) for a in node.args)
    return False


def _node_is_pure_literal(node: DaxNode) -> bool:
    """True if `node` has no column/measure/function reference anywhere --
    a compile-time constant like " " or 42, nothing worth its own metric."""
    if isinstance(node, LiteralNode):
        return True
    if isinstance(node, BinaryOpNode):
        return _node_is_pure_literal(node.left) and _node_is_pure_literal(node.right)
    if isinstance(node, UnaryOpNode):
        return _node_is_pure_literal(node.operand)
    return False


def _collect_measure_ref_names(node: DaxNode) -> Set[str]:
    """Every distinct measure name referenced by a MeasureRefNode anywhere
    in this subtree -- same walk shape as _collect_column_ref_tables."""
    names: Set[str] = set()

    def _walk(n: DaxNode) -> None:
        if isinstance(n, MeasureRefNode):
            names.add(n.name)
        elif isinstance(n, BinaryOpNode):
            _walk(n.left)
            _walk(n.right)
        elif isinstance(n, UnaryOpNode):
            _walk(n.operand)
        elif isinstance(n, FunctionCallNode):
            for a in n.args:
                _walk(a)

    _walk(node)
    return names


def _resolve_single_branch_dataset(
    node: DaxNode, calling_dataset: str, metric_datasets_ci: Dict[str, str]
) -> Optional[str]:
    """The one, unambiguous dataset the new standalone metric should be
    anchored on. Prefers the dataset of a referenced MEASURE (e.g.
    CALCULATE([Total Category Volume], 'Date'[Running Year]=1) anchors on
    'Total Category Volume''s own dataset, SalesFact) over any bare column
    reference in the same branch (here, 'Date') -- a branch commonly
    references its own anchor's related tables too (a filter on the date
    dimension, say), and that's fine: whether THOSE are actually reachable
    from the chosen anchor is verified naturally when the new metric goes
    through the normal translation pipeline afterward, the same as any
    other metric -- this function only decides which table OWNS the new
    metric, not whether every reference in it is valid.

    None if measure references resolve to more than one distinct dataset
    (ambiguous), or if there's no measure reference at all and bare column
    references don't collapse to exactly one table either -- the caller
    must decline decomposing that branch rather than guess.
    """
    measure_datasets = set()
    for measure_name in _collect_measure_ref_names(node):
        resolved = metric_datasets_ci.get(str(measure_name).casefold())
        if resolved:
            measure_datasets.add(resolved)
    measure_datasets.discard(calling_dataset)
    if len(measure_datasets) == 1:
        return next(iter(measure_datasets))
    if measure_datasets:
        return None

    tables = set(_collect_column_ref_tables(node))
    tables.discard(calling_dataset)
    if len(tables) != 1:
        return None
    return next(iter(tables))


def _dax_text(node: DaxNode) -> str:
    """Best-effort DAX pretty-printer -- the inverse of DaxAstParser, used
    only to give an auto-decomposed branch a readable, round-trippable DAX
    expression (fed back through the normal translation pipeline exactly
    like any hand-authored metric). Not a byte-exact reproduction of the
    original source text (whitespace/quote-style may differ), only
    semantically equivalent DAX."""
    if isinstance(node, LiteralNode):
        if isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        if isinstance(node.value, str):
            escaped = node.value.replace('"', '""')
            return f'"{escaped}"'
        return str(node.value)
    if isinstance(node, ColumnRefNode):
        return f"'{node.table}'[{node.column}]"
    if isinstance(node, MeasureRefNode):
        return f"[{node.name}]"
    if isinstance(node, IdentifierNode):
        return node.name
    if isinstance(node, FunctionCallNode):
        return f"{node.func}({', '.join(_dax_text(a) for a in node.args)})"
    if isinstance(node, BinaryOpNode):
        return f"{_dax_text(node.left)}{node.op}{_dax_text(node.right)}"
    if isinstance(node, UnaryOpNode):
        return f"{node.op}{_dax_text(node.operand)}"
    return "BLANK()"


def try_decompose_disconnected_selector_metric(
    dax: str,
    calling_dataset: str,
    relationships: Iterable[Any],
    metric_datasets: Optional[Dict[str, str]] = None,
) -> Optional[DisconnectedSelectorDecomposition]:
    """Detect the disconnected-selector-table IF-chain shape (see module
    comment above ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED) and,
    if it matches, extract each non-trivial branch as a standalone,
    self-contained DAX expression ready to become its own metric.

    IMPORTANT: this codebase's own AST renderer does NOT validate
    cross-table reachability for an INLINED MEASURE reference (only for a
    bare ColumnRefNode, via _resolve_table_alias) -- it just splices in
    the referenced measure's already-resolved SQL verbatim. So a metric
    matching this exact shape typically translates "successfully"
    (sql_expression populated, sync_enabled=True) by this codebase's own
    check, even though the emitted SQL mixes columns from a table
    unrelated to the metric's own base table -- something Snowflake's
    real semantic-view compiler WILL reject at actual deploy time (error
    010211). That's why this function takes `relationships` and requires
    calling_dataset to have NO path to each non-trivial branch's own
    dataset: without that check, this would also "decompose" an ordinary,
    already-valid multi-table metric (one Snowflake would happily accept
    because a real relationship exists) that merely happens to match the
    IF-chain shape syntactically -- unnecessary and wasteful.

    Deliberately conservative -- bails (returns None) entirely rather than
    partially decomposing, on ANY sign this isn't cleanly the shape
    understood here:
      - the metric's WHOLE expression must be a top-level IF(...) call
        (not IF nested inside some other operation);
      - every condition in the chain must reference ONLY calling_dataset
        (and never a measure reference, which could hide another table)
        -- and at least one condition must actually reference it, or this
        isn't a "selector on this table" shape at all;
      - every non-trivial branch value must NOT reference calling_dataset
        anywhere, must resolve to exactly one other, unambiguous dataset
        (via _resolve_single_branch_dataset), AND that dataset must be
        genuinely UNREACHABLE from calling_dataset (has_relationship_path
        returns False) -- any branch that's ambiguous, still depends on
        calling_dataset, or IS actually reachable means the whole metric
        declines, not just that branch, since guessing here risks a
        silently wrong (or unnecessary) decomposition.

    A trivial branch (a bare literal like " ", per _node_is_pure_literal)
    is simply skipped (counted in skipped_trivial_count), never given its
    own metric -- there's nothing to translate.

    Returns None if the chain has fewer than 2 conditioned branches, or if
    every branch turned out trivial/skippable (nothing usable to
    decompose) -- the caller's existing advisory-only handling applies
    unchanged in either case.
    """
    if not dax or not calling_dataset:
        return None
    node = DaxAstParser().parse(dax)
    if node is None:
        return None
    if not (isinstance(node, FunctionCallNode) and node.func == "IF"):
        return None

    pairs, terminal = _walk_if_chain(node)
    if len(pairs) < 2:
        return None

    metric_datasets_ci = {str(k).casefold(): v for k, v in (metric_datasets or {}).items()}

    saw_calling_dataset_condition = False
    for cond, _true_val in pairs:
        if _node_contains_measure_ref(cond):
            return None
        cond_tables = _collect_column_ref_tables(cond)
        if cond_tables - {calling_dataset}:
            return None
        if calling_dataset in cond_tables:
            saw_calling_dataset_condition = True
    if not saw_calling_dataset_condition:
        return None

    values_to_check: List[Tuple[Optional[DaxNode], DaxNode]] = list(pairs)
    if terminal is not None:
        values_to_check.append((None, terminal))

    branches: List[DecomposedSelectorBranch] = []
    skipped_trivial = 0
    for cond, value_node in values_to_check:
        if _node_is_pure_literal(value_node):
            skipped_trivial += 1
            continue
        if calling_dataset in _collect_column_ref_tables(value_node):
            return None
        branch_dataset = _resolve_single_branch_dataset(value_node, calling_dataset, metric_datasets_ci)
        if not branch_dataset:
            return None
        if has_relationship_path(relationships, calling_dataset, branch_dataset):
            # Genuinely reachable -- this branch's table isn't actually
            # disconnected from calling_dataset, so the original metric
            # may well be valid Snowflake SQL as written. Decline the
            # whole decomposition rather than second-guess a metric that
            # might already deploy correctly.
            return None
        branches.append(DecomposedSelectorBranch(
            condition_dax=_dax_text(cond) if cond is not None else "otherwise",
            branch_dax=_dax_text(value_node),
            branch_dataset=branch_dataset,
        ))

    if not branches:
        return None

    return DisconnectedSelectorDecomposition(
        selector_table=calling_dataset,
        branches=branches,
        skipped_trivial_count=skipped_trivial,
    )
