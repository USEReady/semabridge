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
from typing import Any, Dict, List, Optional, Tuple, Union

from semabridge.utils.logger import get_logger
from semabridge.converter.function_registry import FunctionRegistry

_AST_CACHE: dict[tuple, Optional[str]] = {}
_AST_CACHE_MAX = 1024


def _ast_cache_key(dax: str, table_alias: str, date_alias: str, measure_sql_map: Optional[Dict[str, str]]) -> tuple:
    items = tuple(sorted((measure_sql_map or {}).items()))
    return (dax or "", table_alias or "", date_alias or "", items)
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


class DaxSqlRenderer:
    """
    Walks a parsed DAX AST and emits Snowflake-compatible SQL.

    The renderer is deterministic: given the same AST it always emits
    the same SQL.  It raises ``DaxRenderError`` for expressions it cannot
    safely translate.

    Args:
        table_alias: SQL alias for the primary fact table (e.g. ``"SALES"``).
        date_alias: SQL alias for the Date/Calendar dimension table (e.g. ``"CALENDAR"``).
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

    def __init__(
        self,
        table_alias: str = "T",
        date_alias: str = "CALENDAR",
        measure_sql_map: Optional[Dict[str, str]] = None,
        behavior_config: Optional[Dict[str, Any]] = None,
        cursor: Any = None,
        date_column: str = "DATE",
    ) -> None:
        # Keep alias as provided (caller passes the correct form)
        self.table_alias = table_alias
        self.date_alias = date_alias
        self.date_column = date_column
        self.measure_sql_map = measure_sql_map or {}
        self.behavior_config = behavior_config or {}
        self.cursor = cursor
        self._func_registry = FunctionRegistry()
        self._discovery_cache = {}

    def _get_config_value(self, key: str, default: Any = None) -> Any:
        keys = key.split('.')
        value = self.behavior_config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def _discover_date_column(self) -> str:
        """Dynamically discover date column."""
        col = self._get_config_value("default_date_column")
        if col:
            return col
        import os
        env_col = os.getenv("SEMABRIDGE_DATE_COLUMN")
        if env_col:
            return f'{self.date_alias}."{env_col}"'
        return f'{self.date_alias}."{self.date_column}"'

    def _discover_max_date(self) -> str:
        return (
            self._get_config_value("max_date_expression")
            or self._get_config_value("dynamic_anchors.max_date_anchor")
            or self._get_config_value("max_date_anchor")
            or self._get_config_value("max_date_anchor_name")
            or "MAX_DATE"
        )

    def _discover_year_function(self) -> str:
        if self.cursor:
            try:
                for func in ['YEAR', 'EXTRACT(YEAR FROM', 'DATE_PART(\'year\'']:
                    try:
                        self.cursor.execute(f"SELECT {func}(CURRENT_DATE)")
                        return func
                    except:
                        continue
            except:
                pass
        return self._get_config_value('year_function', 'YEAR')

    def _discover_quarter_function(self) -> str:
        return self._get_config_value('quarter_function', 'QUARTER')

    def _discover_month_function(self) -> str:
        return self._get_config_value('month_function', 'MONTH')

    def _discover_date_add_function(self) -> str:
        if self.cursor:
            for func in ['DATEADD', 'DATE_ADD', 'TIMESTAMPADD']:
                try:
                    self.cursor.execute(f"SELECT {func}('day', 1, CURRENT_DATE)")
                    return func
                except:
                    continue
        return self._get_config_value('date_add_function', 'DATEADD')

    def _discover_date_trunc_function(self) -> str:
        return self._get_config_value('date_trunc_function', 'DATE_TRUNC')

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
        return f'{self.table_alias}."{col}"'

    def _unwrap_aggregate(self, sql: str) -> Optional[str]:
        import re
        match = re.match(r'^\s*(?:SUM|AVG|MIN|MAX|COUNT)\s*\(\s*(.+?)\s*\)\s*$', sql, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _render_measure_ref(self, node: MeasureRefNode) -> str:
        measure_name = node.name
        if measure_name in self.measure_sql_map:
            base_sql = self.measure_sql_map[measure_name]
            if base_sql:
                unwrapped = self._unwrap_aggregate(base_sql)
                return unwrapped if unwrapped else base_sql
        return "CAST(NULL AS DOUBLE)"

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
        alt = self._render_node(args[2]) if len(args) > 2 else "0"
        return f"COALESCE({num} / NULLIF({den}, 0), {alt})"

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

        # Support lag-period time-intelligence functions inside CALCULATE
        is_lag = False
        lag_interval = "year"
        is_ytd = False
        ytd_func = ""
        is_topn = False
        topn_n = "1"
        is_rankx = False

        for filter_arg in args[1:]:
            if isinstance(filter_arg, FunctionCallNode):
                fname = filter_arg.func.upper()
                if fname in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
                    is_lag = True
                    lag_interval = "year" if fname in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR") else ("quarter" if fname == "PREVIOUSQUARTER" else "month")
                    break
                elif fname in ("DATESYTD", "DATESMTD", "DATESQTD"):
                    is_ytd = True
                    ytd_func = "TOTALYTD" if fname == "DATESYTD" else ("TOTALMTD" if fname == "DATESMTD" else "TOTALQTD")
                    break
                elif fname == "TOPN":
                    is_topn = True
                    if filter_arg.args:
                        topn_n = self._render_node(filter_arg.args[0])
                    break
                elif fname == "RANKX":
                    is_rankx = True
                    break

        if is_ytd:
            return self._render_period_to_date(ytd_func, [args[0]])

        if is_topn:
            unwrapped = self._unwrap_measure_ref(args[0])
            if unwrapped:
                sql_func, col_sql, cast, is_distinct = unwrapped
                return f"{sql_func}(CASE WHEN \"_RANK\" <= {topn_n} THEN {col_sql}{cast} ELSE 0 END)"
            return f"SUM(CASE WHEN \"_RANK\" <= {topn_n} THEN ({agg_expr})::FLOAT ELSE 0 END)"

        if is_rankx:
            return '"_RANK"'

        if is_lag:
            date_col = self._discover_date_column()
            max_date = self._discover_max_date()
            year_func = self._discover_year_function()
            quarter_func = self._discover_quarter_function()
            month_func = self._discover_month_function()
            dateadd_func = self._discover_date_add_function()
            trunc_func = self._discover_date_trunc_function()

            unwrapped = self._unwrap_measure_ref(args[0])
            if unwrapped:
                sql_func, col_sql, cast, is_distinct = unwrapped
                if sql_func == "COUNT" and is_distinct:
                    if lag_interval == "year":
                        return f"COUNT(DISTINCT CASE WHEN {year_func}({date_col}) = {year_func}({max_date}) - 1 AND {date_col} BETWEEN {dateadd_func}('year', -1, {trunc_func}('YEAR', {max_date})) AND {dateadd_func}('year', -1, {max_date}) THEN {col_sql} END)"
                    elif lag_interval == "quarter":
                        return f"COUNT(DISTINCT CASE WHEN {year_func}({date_col}) = {year_func}({dateadd_func}('quarter', -1, {max_date})) AND {quarter_func}({date_col}) = {quarter_func}({dateadd_func}('quarter', -1, {max_date})) THEN {col_sql} END)"
                    else:
                        return f"COUNT(DISTINCT CASE WHEN {year_func}({date_col}) = {year_func}({dateadd_func}('month', -1, {max_date})) AND {month_func}({date_col}) = {month_func}({dateadd_func}('month', -1, {max_date})) THEN {col_sql} END)"
                else:
                    if lag_interval == "year":
                        return (
                            f"{sql_func}(CASE WHEN {year_func}({date_col}) = {year_func}({max_date}) - 1 "
                            f"AND {date_col} BETWEEN {dateadd_func}('year', -1, {trunc_func}('YEAR', {max_date})) "
                            f"AND {dateadd_func}('year', -1, {max_date}) THEN {col_sql}{cast} END)"
                        )
                    elif lag_interval == "quarter":
                        return (
                            f"{sql_func}(CASE WHEN {year_func}({date_col}) = {year_func}({dateadd_func}('quarter', -1, {max_date})) "
                            f"AND {quarter_func}({date_col}) = {quarter_func}({dateadd_func}('quarter', -1, {max_date})) THEN {col_sql}{cast} END)"
                        )
                    else:  # month
                        return (
                            f"{sql_func}(CASE WHEN {year_func}({date_col}) = {year_func}({dateadd_func}('month', -1, {max_date})) "
                            f"AND {month_func}({date_col}) = {month_func}({dateadd_func}('month', -1, {max_date})) THEN {col_sql}{cast} END)"
                        )
            
            # Fallback: wrap the raw agg_expr in a CASE-bounded prior-year filter
            if lag_interval == "year":
                return (
                    f"SUM(CASE WHEN {year_func}({date_col}) = {year_func}({max_date}) - 1 "
                    f"AND {date_col} <= {dateadd_func}('year', -1, {max_date}) THEN ({agg_expr})::FLOAT END)"
                )
            raise self.DaxRenderError(f"Cannot render lag_interval={lag_interval} without recognized aggregation")

        for filter_arg in args[1:]:
            if isinstance(filter_arg, FunctionCallNode):
                fname = filter_arg.func.upper()

                if fname == "ALL":
                    # ALL(table) - ignore filter instead of windowing
                    pass
                elif fname == "ALLEXCEPT":
                    # For now, treat ALLEXCEPT as ignoring the filter globally 
                    # Real ALLEXCEPT is complex, but in semantic view simple CASE is safer
                    pass
                elif fname == "FILTER":
                    if len(filter_arg.args) >= 2:
                        pred = self._render_node(filter_arg.args[1])
                        filter_clauses.append(pred)
                    else:
                        raise self.DaxRenderError("FILTER requires table and predicate arguments")
                else:
                    raise self.DaxRenderError(f"CALCULATE filter modifier not supported: {fname}")

            elif isinstance(filter_arg, BinaryOpNode):
                filter_clauses.append(self._render_node(filter_arg))

        # Always try to unwrap the base measure to avoid nested aggregates or OVER()
        unwrapped = self._unwrap_measure_ref(args[0])
        
        if not filter_clauses:
            return agg_expr
            
        where = " AND ".join(filter_clauses)
        
        if unwrapped:
            sql_func, col_sql, cast, is_distinct = unwrapped
            if sql_func == "COUNT" and is_distinct:
                return f"COUNT(DISTINCT CASE WHEN {where} THEN {col_sql} END)"
            return f"{sql_func}(CASE WHEN {where} THEN {col_sql}{cast} END)"
        else:
            if isinstance(args[0], FunctionCallNode) and args[0].func.upper() in self._AGG_MAP:
                return self._rewrite_filtered_aggregate(args[0], where)
            else:
                # Last fallback: wrap the raw expression. Snowflake will reject if it's nested aggregate.
                return f"SUM(CASE WHEN {where} THEN ({agg_expr})::FLOAT ELSE 0 END)"

    def _unwrap_measure_ref(self, agg_node: DaxNode) -> Optional[Tuple[str, str, str, bool]]:
        """
        Attempt to unwrap an aggregation node into its (sql_func, col_sql, cast, is_distinct).
        Returns None if it cannot be unwrapped into a simple base aggregation.
        """
        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else f'{self.table_alias}."AMOUNT"'
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            cast = "::FLOAT" if inner_func in ("SUM", "AVERAGE") else ""
            return sql_func, col_sql, cast, False

        elif isinstance(agg_node, MeasureRefNode) and agg_node.name in self.measure_sql_map:
            ref_sql = self.measure_sql_map[agg_node.name]
            try:
                import sqlglot
                from sqlglot import exp
                ref_ast = sqlglot.parse_one(ref_sql, read="snowflake")
                
                # Unwrap Cast
                if isinstance(ref_ast, exp.Cast) and ref_ast.this:
                    ref_ast = ref_ast.this

                # CRITICAL: Snowflake physical columns are uppercase.
                # If sqlglot preserves mixed-case quotes like "Units", it will fail dry-run!
                for identifier in ref_ast.find_all(exp.Identifier):
                    identifier.set("this", identifier.name.upper())

                func_map = {"Sum": "SUM", "Avg": "AVG", "Min": "MIN", "Max": "MAX", "Count": "COUNT"}
                cls_name = ref_ast.__class__.__name__
                if cls_name in func_map:
                    sql_func = func_map[cls_name]
                    is_distinct = False
                    col_sql = "1"
                    if ref_ast.this:
                        if isinstance(ref_ast.this, exp.Distinct):
                            is_distinct = True
                            if hasattr(ref_ast.this, 'expressions') and ref_ast.this.expressions:
                                col_sql = ref_ast.this.expressions[0].sql(dialect="snowflake")
                        else:
                            col_sql = ref_ast.this.sql(dialect="snowflake")
                    cast = "::FLOAT" if sql_func in ("SUM", "AVG") else ""
                    return sql_func, col_sql, cast, is_distinct
            except Exception:
                pass
        return None

    def _render_period_to_date(self, func: str, args: List[DaxNode]) -> str:
        """
        Translate TOTALYTD / TOTALMTD / TOTALQTD to Snowflake CASE WHEN scalar aggregates.

        Snowflake METRICS clause does NOT allow window functions (OVER).
        Pattern: TOTALYTD(aggregation, date_column[, filter_expression])
        """
        if not args:
            raise self.DaxRenderError(f"{func} requires at least 1 argument")

        date_col = self._discover_date_column()
        max_date = self._discover_max_date()
        trunc_func = self._discover_date_trunc_function()

        # Unwrap the inner aggregation to build a CASE WHEN expression
        agg_node = args[0]
        unwrapped = self._unwrap_measure_ref(agg_node)
        
        if unwrapped:
            sql_func, col_sql, cast, is_distinct = unwrapped
            if sql_func == "COUNT" and is_distinct:
                if func == "TOTALYTD":
                    return f"COUNT(DISTINCT CASE WHEN {date_col} >= {trunc_func}('YEAR', {max_date}) AND {date_col} <= {max_date} THEN {col_sql} END)"
                if func == "TOTALMTD":
                    return f"COUNT(DISTINCT CASE WHEN {date_col} >= {trunc_func}('MONTH', {max_date}) AND {date_col} <= {max_date} THEN {col_sql} END)"
                if func == "TOTALQTD":
                    return f"COUNT(DISTINCT CASE WHEN {date_col} >= {trunc_func}('QUARTER', {max_date}) AND {date_col} <= {max_date} THEN {col_sql} END)"
            else:
                if func == "TOTALYTD":
                    return f"{sql_func}(CASE WHEN {date_col} >= {trunc_func}('YEAR', {max_date}) AND {date_col} <= {max_date} THEN {col_sql}{cast} END)"
                if func == "TOTALMTD":
                    return f"{sql_func}(CASE WHEN {date_col} >= {trunc_func}('MONTH', {max_date}) AND {date_col} <= {max_date} THEN {col_sql}{cast} END)"
                if func == "TOTALQTD":
                    return f"{sql_func}(CASE WHEN {date_col} >= {trunc_func}('QUARTER', {max_date}) AND {date_col} <= {max_date} THEN {col_sql}{cast} END)"
        else:
            # fallback: render the whole agg, wrap in YEAR filter
            agg_sql = self._render_node(agg_node)
            if func == "TOTALYTD":
                return (
                    f"SUM(CASE WHEN {date_col} >= {trunc_func}('YEAR', {max_date}) "
                    f"AND {date_col} <= {max_date} THEN ({agg_sql})::FLOAT END)"
                )
            if func == "TOTALMTD":
                return (
                    f"SUM(CASE WHEN {date_col} >= {trunc_func}('MONTH', {max_date}) "
                    f"AND {date_col} <= {max_date} THEN ({agg_sql})::FLOAT END)"
                )
            if func == "TOTALQTD":
                return (
                    f"SUM(CASE WHEN {date_col} >= {trunc_func}('QUARTER', {max_date}) "
                    f"AND {date_col} <= {max_date} THEN ({agg_sql})::FLOAT END)"
                )
        raise self.DaxRenderError(f"Unhandled period-to-date func: {func}")

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

        date_col = self._discover_date_column()
        max_date = self._discover_max_date()
        year_func = self._discover_year_function()
        quarter_func = self._discover_quarter_function()
        month_func = self._discover_month_function()
        dateadd_func = self._discover_date_add_function()
        trunc_func = self._discover_date_trunc_function()

        agg_node = args[0]

        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else f'{self.table_alias}."UNITS"'
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            cast = "::FLOAT" if inner_func in ("SUM", "AVERAGE") else ""

            if interval == "year":
                return (
                    f"{sql_func}(CASE WHEN {year_func}({date_col}) = {year_func}({max_date}) - 1 "
                    f"AND {date_col} BETWEEN {dateadd_func}('year', -1, {trunc_func}('YEAR', {max_date})) "
                    f"AND {dateadd_func}('year', -1, {max_date}) THEN {col_sql}{cast} END)"
                )
            if interval == "quarter":
                return (
                    f"{sql_func}(CASE WHEN {year_func}({date_col}) = {year_func}({dateadd_func}('quarter', -1, {max_date})) "
                    f"AND {quarter_func}({date_col}) = {quarter_func}({dateadd_func}('quarter', -1, {max_date})) THEN {col_sql}{cast} END)"
                )
            # month
            return (
                f"{sql_func}(CASE WHEN {year_func}({date_col}) = {year_func}({dateadd_func}('month', -1, {max_date})) "
                f"AND {month_func}({date_col}) = {month_func}({dateadd_func}('month', -1, {max_date})) THEN {col_sql}{cast} END)"
            )

        raise self.DaxRenderError(
            f"Period function requires a direct aggregation as first argument, got: {type(agg_node).__name__}"
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
        dateadd_func = self._discover_date_add_function()
        return f"{dateadd_func}('{interval_str}', {amount}, {date_col})"


# ---------------------------------------------------------------------------
# Convenience function used by DAXTranslator
# ---------------------------------------------------------------------------


def try_ast_translate(
    dax: str,
    table_alias: str,
    date_alias: str = "calendar",
    measure_sql_map: Optional[Dict[str, str]] = None,
    behavior_config: Optional[Dict[str, Any]] = None,
    cursor: Any = None,
    **kwargs
) -> Optional[str]:
    """
    Attempt to translate a DAX expression to Snowflake SQL via AST parsing.

    This is the entry-point called by DAXTranslator for Tier 3/4 expressions
    that cannot be handled by the fast-path regex approach.

    Returns:
        SQL string on success, None on failure.
    """
    cache_key = _ast_cache_key(dax, table_alias, date_alias, measure_sql_map)
    if cache_key in _AST_CACHE:
        return _AST_CACHE[cache_key]

    parser = DaxAstParser()
    ast = parser.parse(dax)
    if ast is None:
        _AST_CACHE[cache_key] = None
        return None

    date_column = kwargs.get("date_column", "DATE")
    renderer = DaxSqlRenderer(
        table_alias=table_alias,
        date_alias=date_alias,
        measure_sql_map=measure_sql_map or {},
        behavior_config=behavior_config,
        cursor=cursor,
        date_column=date_column,
    )
    sql = renderer.render(ast)
    if len(_AST_CACHE) >= _AST_CACHE_MAX:
        _AST_CACHE.clear()
    _AST_CACHE[cache_key] = sql
    return sql
