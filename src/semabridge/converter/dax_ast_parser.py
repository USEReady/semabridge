"""
DAX Abstract Syntax Tree (AST) Parser.

Implements a recursive-descent tokenizer and parser for DAX (Data Analysis Expressions),
producing a typed AST that can be traversed to generate Snowflake-compatible SQL.

Architecture
------------
Tier 1-2 (simple aggregations and arithmetic)  → fast-path regex in DAXTranslator (unchanged).
Tier 3   (time intelligence)                   → AST parse → SQL window function rendering.
Tier 4   (CALCULATE / FILTER / ALL / ALLEXCEPT) → AST parse → CASE-based scalar expression.

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
    ) -> None:
        # Keep alias as provided (caller passes the correct form)
        self.table_alias = table_alias
        self.date_alias = date_alias
        self.measure_sql_map = measure_sql_map or {}

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

    def _render_measure_ref(self, node: MeasureRefNode) -> str:
        if node.name in self.measure_sql_map:
            return f"({self.measure_sql_map[node.name]})"
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

        # CALCULATE
        if func == "CALCULATE":
            return self._render_calculate(node.args)

        # Time Intelligence
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

    # Matches a top-level simple aggregate so the inner column can be wrapped in CASE.
    # E.g.  SUM(fact."AMOUNT")  →  group(1)="SUM"  group(2)=`fact."AMOUNT"`
    _SIMPLE_AGG_RE = re.compile(
        r'^(SUM|AVG|MIN|MAX|COUNT)\((.+)\)$', re.IGNORECASE | re.DOTALL
    )

    def _render_calculate(self, args: List[DaxNode]) -> str:
        """
        Translate CALCULATE(agg_expr, filter1, filter2, ...) to a Snowflake-compatible
        scalar expression.

        Supported filter modifiers:
          - ALL(table)          → ignore all filters for that table → window OVER ()
          - ALLEXCEPT(t, col)   → OVER (PARTITION BY col)
          - FILTER(tbl, pred)   → rewrite as CASE-based filtered aggregate
          - simple comparisons  → rewrite as CASE-based filtered aggregate

        Note: full SELECT subqueries are **not** emitted because Snowflake's METRICS
        clause only accepts scalar expressions.  For simple aggregations the filter
        is pushed inside the aggregate via a CASE expression:
            CALCULATE(SUM([Amount]), [Year] = 2024)
            → SUM(CASE WHEN year_col = 2024 THEN amount_col END)
        When the aggregation is too complex to rewrite this way, a DaxRenderError is
        raised so the caller can skip the metric gracefully.
        """
        if not args:
            raise self.DaxRenderError("CALCULATE requires at least 1 argument")

        agg_expr = self._render_node(args[0])
        filter_clauses: List[str] = []
        partition_cols: List[str] = []
        is_window = False

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

        if is_window:
            if partition_cols:
                partition_clause = "PARTITION BY " + ", ".join(partition_cols)
                return f"{agg_expr} OVER ({partition_clause})"
            else:
                return f"{agg_expr} OVER ()"
        elif filter_clauses:
            where = " AND ".join(filter_clauses)
            # Snowflake METRICS clause does not allow SELECT subqueries.
            # Rewrite as a CASE-based filtered aggregate when the base aggregation
            # is a simple SUM/AVG/MIN/MAX/COUNT so the filter can be pushed inside:
            #   SUM(CASE WHEN <where> THEN <col> END)
            m = self._SIMPLE_AGG_RE.match(agg_expr.strip())
            if m:
                func, inner = m.group(1).upper(), m.group(2)
                return f"{func}(CASE WHEN {where} THEN {inner} END)"
            # The aggregation is too complex to safely rewrite (e.g. it already
            # contains a window function or a measure reference chain).  Raise so
            # the caller skips this metric and surfaces a clear sync_failure_reason.
            raise self.DaxRenderError(
                f"CALCULATE+FILTER: base aggregation '{agg_expr}' is too complex to "
                "rewrite as a Snowflake scalar expression.  Only simple SUM/AVG/MIN/"
                "MAX/COUNT aggregations are supported in the METRICS clause."
            )
        else:
            return agg_expr

    def _render_period_to_date(self, func: str, args: List[DaxNode]) -> str:
        """
        Translate TOTALYTD / TOTALMTD / TOTALQTD to Snowflake window functions.

        Pattern: TOTALYTD(aggregation, date_column[, filter_expression])
        """
        if not args:
            raise self.DaxRenderError(f"{func} requires at least 1 argument")

        agg_sql = self._render_node(args[0])
        d = self.date_alias

        if func == "TOTALYTD":
            return (
                f"{agg_sql} OVER (\n"
                f"    PARTITION BY {d}.\"YEAR\"\n"
                f"    ORDER BY {d}.\"DATE\"\n"
                f"    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW\n"
                f")"
            )
        if func == "TOTALMTD":
            return (
                f"{agg_sql} OVER (\n"
                f"    PARTITION BY {d}.\"YEAR\", {d}.\"MONTH\"\n"
                f"    ORDER BY {d}.\"DATE\"\n"
                f"    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW\n"
                f")"
            )
        if func == "TOTALQTD":
            return (
                f"{agg_sql} OVER (\n"
                f"    PARTITION BY {d}.\"YEAR\", {d}.\"QUARTER\"\n"
                f"    ORDER BY {d}.\"DATE\"\n"
                f"    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW\n"
                f")"
            )
        raise self.DaxRenderError(f"Unhandled period-to-date func: {func}")

    def _render_lag_period(
        self, args: List[DaxNode], interval: str, amount: int
    ) -> str:
        """
        Translate SAMEPERIODLASTYEAR / PREVIOUSxxx to a Snowflake LAG window function.

        Snowflake's METRICS clause does not allow SELECT subqueries, so we use a
        window-based LAG expression instead:

          SAMEPERIODLASTYEAR / PREVIOUSYEAR  (interval="year")
            →  LAG(agg) OVER (PARTITION BY <date>."MONTH" ORDER BY <date>."YEAR")
               Semantics: for each month, return the same aggregate from the
               previous calendar year.

          PREVIOUSMONTH  (interval="month")
            →  LAG(agg) OVER (ORDER BY <date>."DATE")

          PREVIOUSQUARTER  (interval="quarter")
            →  LAG(agg) OVER (PARTITION BY <date>."YEAR" ORDER BY <date>."QUARTER")

        If the interval is not recognised a DaxRenderError is raised so the caller
        can skip the metric with a clear sync_failure_reason.
        """
        if not args:
            raise self.DaxRenderError("Period function requires arguments")
        agg_sql = self._render_node(args[0])
        d = self.date_alias

        if interval == "year":
            return (
                f"LAG({agg_sql}) OVER (\n"
                f"    PARTITION BY {d}.\"MONTH\"\n"
                f"    ORDER BY {d}.\"YEAR\"\n"
                f")"
            )
        if interval == "month":
            return (
                f"LAG({agg_sql}) OVER (\n"
                f"    ORDER BY {d}.\"DATE\"\n"
                f")"
            )
        if interval == "quarter":
            return (
                f"LAG({agg_sql}) OVER (\n"
                f"    PARTITION BY {d}.\"YEAR\"\n"
                f"    ORDER BY {d}.\"QUARTER\"\n"
                f")"
            )
        raise self.DaxRenderError(
            f"Period shift interval '{interval}' cannot be expressed as a Snowflake "
            "scalar expression in the METRICS clause."
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
    date_alias: str = "calendar",
    measure_sql_map: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Attempt to translate a DAX expression to Snowflake SQL via AST parsing.

    This is the entry-point called by DAXTranslator for Tier 3/4 expressions
    that cannot be handled by the fast-path regex approach.

    Returns:
        SQL string on success, None on failure.
    """
    parser = DaxAstParser()
    ast = parser.parse(dax)
    if ast is None:
        return None

    renderer = DaxSqlRenderer(
        table_alias=table_alias,
        date_alias=date_alias,
        measure_sql_map=measure_sql_map or {},
    )
    return renderer.render(ast)
