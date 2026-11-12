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
from semabridge.utils.naming import to_alias

import contextlib
import os
import re

from dataclasses import dataclass, field

from enum import Enum, auto

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Union



from semabridge.utils.logger import get_logger

from semabridge.converter.function_registry import FunctionRegistry

from semabridge.utils.relationship_graph import has_relationship_path



def _wrap_parens_for_op(expr: str) -> str:
    s = expr.strip()
    if not s:
        return s
    if s.startswith("(") and s.endswith(")"):
        depth = 0
        balanced = True
        for ch in s[:-1]:
            if ch == "(": depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    balanced = False
                    break
        if balanced:
            return s
    depth = 0
    in_quote = None
    for ch in s:
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ("'", '"'):
            in_quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in ("+", "-"):
            return f"({s})"
    return s


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

    table_aliases: Optional[Dict[str, str]] = None,

    relationships: Optional[Iterable[Any]] = None,

    measure_non_additive_map: Optional[Dict[str, Tuple[Optional[str], str]]] = None,

    table_key_map: Optional[Dict[str, str]] = None,

    allow_window_enrichment: bool = False,

) -> tuple:

    items = tuple(sorted((measure_sql_map or {}).items()))

    names = tuple(sorted(known_measure_names or ()))

    flags = tuple(sorted((anchor_flag_map or {}).items()))

    date_tables = tuple(sorted(str(n).casefold() for n in (date_table_names or ())))

    tbl_aliases = tuple(sorted((table_aliases or {}).items()))

    non_add_items = tuple(sorted((measure_non_additive_map or {}).items()))

    key_items = tuple(sorted((table_key_map or {}).items()))

    rel_tuple = tuple(

        sorted(

            (

                str(getattr(r, "from_dataset", "")).casefold(),

                tuple(str(c).casefold() for c in (getattr(r, "from_columns", None) or ())),

                str(getattr(r, "to_dataset", "")).casefold(),

                tuple(str(c).casefold() for c in (getattr(r, "to_columns", None) or ())),

                bool(getattr(r, "is_active", True)),

            )

            for r in (relationships or ())

        )

    )

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

        tbl_aliases,

        rel_tuple,

        non_add_items,

        key_items,

        allow_window_enrichment,

    )

from semabridge.utils.naming import sanitize_column, to_alias



logger = get_logger(__name__)



# ---------------------------------------------------------------------------

# Token Layer

# ---------------------------------------------------------------------------





class DaxTokenType(Enum):

    # Literals

    NUMBER = auto()

    STRING = auto()

    BOOLEAN = auto()

    # Identifiers

    COLUMN_REF = auto()    # 'TableName'[ColumnName]

    MEASURE_REF = auto()   # [MeasureName]

    FUNC_NAME = auto()     # SUM, CALCULATE, FILTER, etc.

    IDENTIFIER = auto()    # bare word

    VAR = auto()           # VAR keyword (variable-definition block)

    RETURN = auto()        # RETURN keyword (variable-definition block)

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
    LBRACE = auto()      # { -- table constructor open
    RBRACE = auto()      # } -- table constructor close
    CONCAT = auto()      # & -- text concatenation

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

    "calculate", "calculatetable", "filter", "all", "allexcept", "allselected", "removefilters",

    "values", "distinct", "hasonevalue", "selectedvalue",

    "related", "relatedtable", "userelationship", "crossfilter",

    "if", "switch", "iferror", "isblank", "isfiltered", "hasonefilter",

    "divide", "round", "roundup", "rounddown", "mround", "int", "abs", "ceiling", "floor",

    "value",

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

            elif ch.isdigit() or (ch == "." and self._peek_next_is_digit()):

                tok = self._read_number()

            elif ch == "-" and self._peek_next_is_digit():

                if self._is_unary_minus_context():

                    tok = self._read_number()

                else:

                    tok = self._read_operator()

            elif ch.isalpha() or ch == "_":

                tok = self._read_identifier()

            else:

                tok = self._read_operator()



            self.tokens.append(tok)



        self.tokens.append(DaxToken(DaxTokenType.EOF, "", self.pos))

        return self.tokens



    # ------------------------------------------------------------------

    def _skip_whitespace(self) -> None:

        while self.pos < len(self.text):

            ch = self.text[self.pos]

            if ch in " \t\r\n":

                self.pos += 1

                continue

            two = self.text[self.pos : self.pos + 2]

            if two in ("--", "//"):

                self.pos += 2

                while self.pos < len(self.text) and self.text[self.pos] not in "\r\n":

                    self.pos += 1

                continue

            if two == "/*":

                self.pos += 2

                while self.pos < len(self.text) and self.text[self.pos : self.pos + 2] != "*/":

                    self.pos += 1

                if self.pos < len(self.text):

                    self.pos += 2  # skip */

                continue

            break



    def _is_unary_minus_context(self) -> bool:

        if not self.tokens:

            return True

        last_type = self.tokens[-1].type

        return last_type in (

            DaxTokenType.PLUS,

            DaxTokenType.MINUS,

            DaxTokenType.STAR,

            DaxTokenType.SLASH,

            DaxTokenType.EQ,

            DaxTokenType.NEQ,

            DaxTokenType.LT,

            DaxTokenType.LTE,

            DaxTokenType.GT,

            DaxTokenType.GTE,

            DaxTokenType.AND,

            DaxTokenType.OR,

            DaxTokenType.NOT,

            DaxTokenType.LPAREN,

            DaxTokenType.COMMA,

            DaxTokenType.VAR,

            DaxTokenType.RETURN,

        )



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

        # Check for Power BI hierarchy: 'Table'[Column].[Level]
        while (
            self.pos < len(self.text)
            and self.text[self.pos] == "."
            and self.pos + 1 < len(self.text)
            and self.text[self.pos + 1] == "["
        ):
            self.pos += 2  # skip .[
            while self.pos < len(self.text) and self.text[self.pos] != "]":
                self.pos += 1
            if self.pos < len(self.text) and self.text[self.pos] == "]":
                self.pos += 1

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

        # VAR/RETURN are structural keywords, not functions — they mark a

        # variable-definition block's boundaries, so the parser needs to

        # recognize them distinctly from an ordinary FUNC_NAME/IDENTIFIER

        # to know when a VAR block starts/ends (see _parse_var_block).

        if word_lower == "var":

            return DaxToken(DaxTokenType.VAR, word, start)

        if word_lower == "return":

            return DaxToken(DaxTokenType.RETURN, word, start)

        if word_lower == "not":
            peek_pos = self.pos
            while peek_pos < len(self.text) and self.text[peek_pos] in " \t\r\n":
                peek_pos += 1
            if peek_pos < len(self.text) and self.text[peek_pos] == "(":
                return DaxToken(DaxTokenType.FUNC_NAME, word.upper(), start)
            return DaxToken(DaxTokenType.NOT, word, start)

        if word_lower in _DAX_FUNCTIONS:

            return DaxToken(DaxTokenType.FUNC_NAME, word, start)

        # Boolean / logical keywords

        if word_lower == "and":

            return DaxToken(DaxTokenType.AND, word, start)

        if word_lower == "or":

            return DaxToken(DaxTokenType.OR, word, start)

        if word_lower in ("true", "false"):

            peek_pos = self.pos

            while peek_pos < len(self.text) and self.text[peek_pos] in " \t\r\n":

                peek_pos += 1

            if peek_pos < len(self.text) and self.text[peek_pos] == "(":

                return DaxToken(DaxTokenType.FUNC_NAME, word.upper(), start)

            return DaxToken(DaxTokenType.BOOLEAN, word.upper(), start)

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
            "{": DaxTokenType.LBRACE,
            "}": DaxTokenType.RBRACE,
            "&": DaxTokenType.CONCAT,

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





# Pseudo function name for a DAX table constructor `{ v1, v2, ... }`.
TABLE_CONSTRUCTOR_FUNC = "__TABLE_CONSTRUCTOR__"


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





@dataclass

class VarNode(DaxNode):

    """VAR <name> = <expr> [VAR <name2> = <expr2> ...] RETURN <body>.



    DAX's variable-definition block. `variables` holds each VAR's (name,

    definition-expression) pair in source order; `body` is the RETURN

    expression. Any column/measure/table reference inside EITHER a

    variable's own definition OR the RETURN body counts as a real data

    dependency for the whole block — a variable is just a named

    sub-expression, not a boundary that hides what it references. Every

    AST walker that needs to see "everything this expression touches"

    (dax_has_zero_data_dependencies, the CALCULATE/ALLEXCEPT detector, the

    unreachable-dimension detector, etc.) must descend into both parts;

    walkers that only care about "the outermost/root operation" (e.g.

    dax_root_is_string_producing) should unwrap to `body` instead, since

    that's what the expression ultimately evaluates to.

    """

    variables: List[Tuple[str, DaxNode]] = field(default_factory=list)

    body: DaxNode = field(default_factory=lambda: LiteralNode(0))





def unwrap_var_body(node: Optional[DaxNode]) -> Optional[DaxNode]:

    """Follow a (possibly nested) VarNode chain down to its innermost

    RETURN body — the node an expression actually evaluates to once its

    VAR definitions are resolved. A no-op for any non-VarNode root."""

    while isinstance(node, VarNode):

        node = node.body

    return node





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

            if self._peek().type != DaxTokenType.EOF:

                raise DaxParseError(

                    f"Unexpected trailing token {self._peek().type.name}('{self._peek().value}') at pos {self._peek().pos}"

                )

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

        # A VAR/RETURN block can appear anywhere a DAX expression is valid

        # — as the whole measure body, nested inside a function argument

        # (e.g. CALCULATE(VAR x = ... RETURN ..., filter)), or as a VAR's

        # own definition — since _parse_expr is the entry point every one

        # of those call sites funnels through, checking here (rather than

        # only at the top-level parse() call) makes VAR/RETURN parse

        # correctly at any nesting depth, for any variables/body it holds.

        if self._match(DaxTokenType.VAR):

            return self._parse_var_block()

        return self._parse_or()



    def _parse_var_block(self) -> DaxNode:

        """VAR <name> = <expr> [VAR <name2> = <expr2> ...] RETURN <body>.



        Structural parse only — no assumption about what any variable is

        named or what its definition/RETURN body computes, so this covers

        any VAR/RETURN shape DAX allows, not just single-variable ones.

        """

        variables: List[Tuple[str, DaxNode]] = []

        while self._match(DaxTokenType.VAR):

            self._advance()  # consume VAR

            name_tok = self._peek()

            if name_tok.type not in (DaxTokenType.IDENTIFIER, DaxTokenType.FUNC_NAME):

                raise DaxParseError(

                    f"Expected variable name after VAR, got {name_tok.type}({name_tok.value!r})"

                )

            self._advance()  # consume the variable name

            self._expect(DaxTokenType.EQ)

            value_expr = self._parse_expr()

            variables.append((name_tok.value, value_expr))

        self._expect(DaxTokenType.RETURN)

        body = self._parse_expr()

        return VarNode(variables=variables, body=body)



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
        left = self._parse_concat()

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
            right = self._parse_concat()
            left = BinaryOpNode(op=op, left=left, right=right)
        # `<expr> IN { v1, v2, ... }` -- DAX membership test.
        tok = self._peek()
        if tok.type in (DaxTokenType.IDENTIFIER, DaxTokenType.FUNC_NAME) and str(tok.value).upper() == "IN":
            self._advance()  # consume IN
            right = self._parse_concat()
            left = BinaryOpNode(op="IN", left=left, right=right)
        return left

    def _parse_concat(self) -> DaxNode:
        # DAX `&` (text concatenation): binds looser than +/- and tighter
        # than comparisons.
        left = self._parse_addition()
        while self._match(DaxTokenType.CONCAT):
            self._advance()
            right = self._parse_addition()
            left = BinaryOpNode(op="&", left=left, right=right)
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

            # Parse 'Table'[Column] or 'Table'[Column].[Level] or [Column].[Level]
            m = re.match(r"(?:'([^']+)')?\[([^\]]+)\](?:\.\[([^\]]+)\])?", tok.value)
            if m:
                target_col = m.group(3) or m.group(2)
                tbl = m.group(1) or ""
                return ColumnRefNode(table=tbl, column=target_col, raw=tok.value)

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



        if tok.type == DaxTokenType.BOOLEAN:

            self._advance()

            val = (tok.value.upper() == "TRUE")

            return LiteralNode(value=val, raw=tok.value.upper())



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
        if tok.type == DaxTokenType.LBRACE:
            # Table constructor { v1, v2, ... } -- a pseudo function call so
            # every walker recursing through FunctionCallNode.args sees it.
            self._advance()
            items: List[DaxNode] = []
            while not self._match(DaxTokenType.RBRACE, DaxTokenType.EOF):
                items.append(self._parse_expr())
                if self._match(DaxTokenType.COMMA):
                    self._advance()
            self._expect(DaxTokenType.RBRACE)
            return FunctionCallNode(func=TABLE_CONSTRUCTOR_FUNC, args=items)



        if tok.type == DaxTokenType.EOF:

            raise DaxParseError("Unexpected end of expression")



        # Unknown token — consume and return identifier

        self._advance()

        return IdentifierNode(name=tok.value)



    def _parse_function_call(self) -> DaxNode:

        tok = self._advance()

        func_name = tok.value.upper()



        if self._peek().type != DaxTokenType.LPAREN:
            if func_name == "NOT":
                return UnaryOpNode(op="NOT", operand=self._parse_comparison())
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



ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS = "calculate_filter_semantics_difference"

# Time intelligence rendered against CURRENT_DATE() (no enriched-view anchor
# flag available). DAX anchors period functions to the dates visible in the
# filter context (typically the last date with data), so results differ
# whenever the data's last date is not today.
ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE = "time_intelligence_anchored_to_current_date"
ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE = (
    "Time-intelligence window is anchored to CURRENT_DATE() because no data-anchored "
    "period flag was available; DAX anchors to the last date in the filter context, "
    "so values differ when the data does not extend to today."
)


USERELATIONSHIP_REASON = (
    "not applicable: USERELATIONSHIP requires an inactive-relationship path not representable "
    "in the semantic view (the view always joins through the active relationship)"
)
HASONEVALUE_GUARD_REASON = (
    "not applicable: IF(HASONEVALUE/ISINSCOPE/ISFILTERED(...)) returns a value only at a single-value "
    "granularity and BLANK at totals; a semantic-view metric cannot detect its grouping level, "
    "so totals would differ"
)


def parse_fiscal_year_end(text: Any) -> Optional[int]:
    """Parse a DAX year_end_date literal (e.g. "6/30", "3-31", "2024-06-30")
    into the fiscal year-end MONTH. Returns None for a calendar year end
    (12/31). Raises ValueError when the year end is not the last day of a
    month (a shift this translator cannot express exactly)."""
    import calendar as _cal
    raw = str(text or "").strip()
    parts = [p for p in re.split(r"[/\-\.\s]+", raw) if p]
    if len(parts) < 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"unrecognized fiscal year-end '{raw}'")
    nums = [int(p) for p in parts]
    if len(parts) >= 3 and len(parts[0]) == 4:
        month, day = nums[1], nums[2]
    else:
        month, day = nums[0], nums[1]
        if month > 12 and day <= 12:
            month, day = day, month
    if not (1 <= month <= 12):
        raise ValueError(f"unrecognized fiscal year-end '{raw}'")
    last = _cal.monthrange(2001, month)[1]
    if not (day == last or (month == 2 and day == 29)):
        raise ValueError(f"fiscal year-end '{raw}' is not the last day of a month")
    return None if month == 12 else month


def period_to_date_year_end_node(fn_node: Any) -> Optional[Any]:
    """The year_end_date argument of TOTALYTD/DATESYTD, if one was written."""
    func = str(getattr(fn_node, "func", "")).upper()
    args = list(getattr(fn_node, "args", []) or [])
    if func == "DATESYTD":
        return args[1] if len(args) > 1 else None
    if func == "TOTALYTD":
        for a in args[2:]:
            if isinstance(a, LiteralNode) and isinstance(a.value, str):
                return a
    return None


def fiscal_ytd_component(month: int) -> str:
    """Canonical shape component for a fiscal YTD ending at month `month`."""
    return f"FYTD{int(month):02d}"

ADVISORY_NOTE_CALCULATE_FILTER_SEMANTICS = (

    "DAX CALCULATE filters override existing filters on that column, "

    "while the SUM(CASE WHEN ...) translation doesn't, so results differ when grouping by the filtered column."

)



_DAX_SYNTAX_IN_SQL_PATTERN = re.compile(

    r"\b(BLANK|CALCULATE|CALCULATETABLE|FILTER|ALL|ALLEXCEPT|ALLSELECTED|REMOVEFILTERS|SAMEPERIODLASTYEAR|"

    r"PREVIOUSYEAR|PREVIOUSMONTH|PREVIOUSQUARTER|DATESYTD|DATESMTD|DATESQTD|"

    r"TOTALYTD|TOTALMTD|TOTALQTD|DIVIDE|USERELATIONSHIP|CROSSFILTER|RELATED|RELATEDTABLE|EARLIER|EARLIEST|LOOKUPVALUE)\s*\(",

    re.IGNORECASE,

)





def validate_emitted_sql(sql: Optional[str]) -> bool:

    """Validation gate to reject any emitted SQL containing un-translated DAX functions or bracket references."""

    if not sql or not str(sql).strip():

        return False

    # Check for bracket references [..]

    if re.search(r"\[[^\]]+\]", sql):

        return False

    # Check for un-translated DAX function calls

    if _DAX_SYNTAX_IN_SQL_PATTERN.search(sql):

        return False

    return True





def extract_measure_references_from_ast(dax: str, known_measure_names: Optional[Iterable[str]] = None) -> Set[str]:

    """Extract all bare [MeasureName] references from DAX using AST parsing."""

    if not dax or not dax.strip():

        return set()

    parser = DaxAstParser()

    ast = parser.parse(dax)

    if ast is None:

        return set()

    refs: Set[str] = set()
    known_cf = {m.strip().casefold() for m in known_measure_names if m} if known_measure_names else set()

    def walk(node: Optional[DaxNode]) -> None:

        if node is None:

            return

        if isinstance(node, MeasureRefNode):

            if node.name and node.name.strip():

                refs.add(node.name.strip())

        elif isinstance(node, ColumnRefNode):

            if known_cf and node.column and node.column.strip().casefold() in known_cf:

                refs.add(node.column.strip())

        elif isinstance(node, FunctionCallNode):

            for arg in node.args:

                walk(arg)

        elif isinstance(node, BinaryOpNode):

            walk(node.left)

            walk(node.right)

        elif isinstance(node, UnaryOpNode):

            walk(node.operand)

        elif isinstance(node, VarNode):

            for _, v_expr in node.variables:

                walk(v_expr)

            walk(node.body)



    walk(ast)

    return refs





_TI_DATE_ARG_FUNCS = {
    "TOTALYTD": 1, "TOTALMTD": 1, "TOTALQTD": 1,                 # arg index of the dates column
    "SAMEPERIODLASTYEAR": 0, "PREVIOUSYEAR": 0, "PREVIOUSMONTH": 0, "PREVIOUSQUARTER": 0,
    "DATEADD": 0, "DATESYTD": 0, "DATESMTD": 0, "DATESQTD": 0, "PARALLELPERIOD": 0,
    "DATESINPERIOD": 0, "DATESBETWEEN": 0, "LASTDATE": 0, "FIRSTDATE": 0,
}


def _ti_date_tables_in_ast(node: Optional[DaxNode], out: Set[str]) -> None:
    """Tables used as the dates argument of a time-intelligence function."""
    if node is None:
        return
    if isinstance(node, FunctionCallNode):
        idx = _TI_DATE_ARG_FUNCS.get(node.func.upper())
        if idx is not None and len(node.args) > idx and isinstance(node.args[idx], ColumnRefNode) and node.args[idx].table:
            out.add(node.args[idx].table.strip("'").casefold())
        for a in node.args:
            _ti_date_tables_in_ast(a, out)
    elif isinstance(node, BinaryOpNode):
        _ti_date_tables_in_ast(node.left, out)
        _ti_date_tables_in_ast(node.right, out)
    elif isinstance(node, UnaryOpNode):
        _ti_date_tables_in_ast(node.operand, out)
    elif isinstance(node, VarNode):
        for _, v in node.variables:
            _ti_date_tables_in_ast(v, out)
        _ti_date_tables_in_ast(node.body, out)


def detect_date_tables(
    datasets: Iterable[Any],
    relationships: Iterable[Any],
    measure_dax: Iterable[str],
    config_date_table_names: Optional[Iterable[str]] = None,
) -> Set[str]:
    """Date tables from metadata only - never from table names.
    Optional config_date_table_names provides an explicit override list if specified.
    """
    found: Set[str] = set()
    if config_date_table_names:
        for n in config_date_table_names:
            if n:
                found.add(str(n).strip().casefold())
    col_type: Dict[Tuple[str, str], str] = {}
    keys: Dict[str, Set[str]] = {}
    for ds in datasets or ():
        name = str(getattr(ds, "unique_name", None) or getattr(ds, "name", "")).strip().casefold()
        if not name:
            continue
        # 1. marked as date table in Power BI
        if getattr(ds, "is_date_table", False) or str(getattr(ds, "data_category", "") or "").casefold() == "time":
            found.add(name)
        pk = getattr(ds, "primary_key", None) or []
        keys[name] = {str(k).casefold() for k in ([pk] if isinstance(pk, str) else pk)}
        for col in getattr(ds, "columns", []) or []:
            c = str(getattr(col, "unique_name", None) or getattr(col, "name", "")).casefold()
            dt = getattr(col, "data_type", None) or getattr(col, "type", None)
            col_type[(name, c)] = str(getattr(dt, "value", dt) or "").upper()
            if getattr(col, "is_key", False) or getattr(col, "is_unique", False):
                keys[name].add(c)
    # 2. used as the dates argument of a time-intelligence function
    parser = DaxAstParser()
    for dax in measure_dax or ():
        if not dax:
            continue
        try:
            ast = parser.parse(dax)
            _ti_date_tables_in_ast(ast, found)
        except Exception:
            pass
    # 3. 'one' side of an active relationship on a unique, date-typed key
    for rel in relationships or ():
        if not getattr(rel, "is_active", getattr(rel, "isActive", True)):
            continue
        to_ds = str(getattr(rel, "to_dataset", "") or getattr(rel, "toTable", "") or "").strip().casefold()
        to_cols = getattr(rel, "to_columns", None) or ([getattr(rel, "toColumn", None)] if getattr(rel, "toColumn", None) else [])
        for c in to_cols:
            if not c:
                continue
            c_cf = str(c).casefold()
            t_type = col_type.get((to_ds, c_cf), "")
            if ("DATE" in t_type or "TIME" in t_type) and c_cf in keys.get(to_ds, set()):
                found.add(to_ds)
    return found


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



    _ITERATOR_MAP = {

        "SUMX": "SUM",

        "AVERAGEX": "AVG",

        "MINX": "MIN",

        "MAXX": "MAX",

        "COUNTAX": "COUNT",

    }



    # DAX function calls this renderer turns into a numeric (FLOAT/aggregate)

    # SQL expression rather than an inherently boolean one. DAX allows using

    # such an expression directly as an IF condition — 0/BLANK() is falsy,

    # anything else is truthy (a common YoY/SPLY guard, e.g.

    # IF(CALCULATE([Sales], SAMEPERIODLASTYEAR('Date'[Date])), <growth calc>,

    # BLANK()) to skip the growth calc when there's no prior-year data).

    # Snowflake requires an explicit BOOLEAN for a CASE WHEN condition —

    # passing the raw FLOAT-valued aggregate directly fails with "Can not

    # convert parameter '...' of type [FLOAT] into expected type [BOOLEAN]"

    # and takes down the whole semantic-view DDL, not just this one metric.

    # See _render_if / _is_numeric_truthiness_condition.

    _NUMERIC_TRUTHINESS_FUNCS = frozenset({

        "SUM", "AVERAGE", "AVG", "COUNT", "COUNTA", "COUNTROWS", "COUNTBLANK",

        "DISTINCTCOUNT", "MIN", "MAX", "CALCULATE", "DIVIDE",

        "VALUE", "MROUND", "ROUNDUP", "ROUNDDOWN",

        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",

        "TOTALYTD", "TOTALMTD", "TOTALQTD",

    })



    # DAX names conventionally used for the date/calendar dimension across

    # this codebase (see dax_translator.py's "'Calendar'[Date]" dimension

    # reference and dax_engine.py's date_alias="calendar" default) — a

    # ColumnRefNode naming one of these is resolved to date_alias rather

    # than the primary table.

    def _ti_date_column_sql(self, args: List[DaxNode]) -> str:
        """The dates column exactly as written in the DAX, on its own table's alias."""
        search_args = args[1:] if len(args) > 1 else args
        for arg in search_args:
            if isinstance(arg, ColumnRefNode) and arg.column:
                alias = self._resolve_table_alias(arg.table) if arg.table else self.date_alias
                return f'{alias}."{sanitize_column(arg.column)}"'
            if isinstance(arg, IdentifierNode) and getattr(arg, "name", None):
                alias = self.date_alias
                return f'{alias}."{sanitize_column(arg.name)}"'
        for arg in args:
            if isinstance(arg, ColumnRefNode) and arg.column:
                alias = self._resolve_table_alias(arg.table) if arg.table else self.date_alias
                return f'{alias}."{sanitize_column(arg.column)}"'
            if isinstance(arg, IdentifierNode) and getattr(arg, "name", None):
                alias = self.date_alias
                return f'{alias}."{sanitize_column(arg.name)}"'
        raise self.DaxRenderError("no date column found for time-intelligence function")


    def __init__(

        self,

        table_alias: str = "T",

        date_alias: str = "Date",

        measure_sql_map: Optional[Dict[str, str]] = None,

        known_measure_names: Optional[Any] = None,

        anchor_flag_map: Optional[Dict[Tuple[str, ...], str]] = None,

        primary_table_name: Optional[str] = None,

        date_table_names: Optional[Any] = None,

        allow_unshifted_fallback: bool = False,

        advisory_categories: Optional[List[str]] = None,

        table_aliases: Optional[Dict[str, str]] = None,

        relationships: Optional[Iterable[Any]] = None,

        measure_non_additive_map: Optional[Dict[str, Tuple[Optional[str], str]]] = None,

        table_key_map: Optional[Dict[str, str]] = None,

        allow_window_enrichment: bool = False,

        datasets: Optional[Iterable[Any]] = None,

        model_metadata: Optional[Any] = None,
        dataset_col_lookup: Optional[Dict[str, set]] = None,
        current_measure_name: Optional[str] = None,
        measure_dax_map: Optional[Dict[str, str]] = None,
    ) -> None:

        # Keep alias as provided (caller passes the correct form)

        self.table_alias = table_alias

        self.date_alias = date_alias
        self.current_measure_name = current_measure_name

        self.measure_sql_map = measure_sql_map or {}
        self.measure_dax_map = measure_dax_map or {}
        self._current_loo_info: Optional[Dict[str, Any]] = None

        self.measure_non_additive_map = measure_non_additive_map or {}

        self.non_additive_dimensions: List[Tuple[Optional[str], str]] = []

        self.table_key_map = table_key_map or {}

        self.allow_window_enrichment = bool(allow_window_enrichment)

        self.window_precompute_columns: List[Tuple[str, str, str, str]] = []
        self.datasets = datasets
        self.model_metadata = model_metadata
        self.dataset_col_lookup = dataset_col_lookup

        self._userel_overrides: Dict[str, str] = {}
        self._in_shifted_calculate: int = 0
        self._comparison_context: Optional[Dict[str, Any]] = None
        self.comparison_unit: Optional[str] = None
        self.comparison_comment: Optional[str] = None
        self.last_render_error: Optional[str] = None

        # The DAX table name (e.g. "KPI") that table_alias physically

        # represents for this metric, if the caller knows it. None means

        # the caller hasn't opted into table-aware column resolution yet —

        # _resolve_table_alias then preserves the historical

        # single-table-assumed behavior exactly, so callers that don't pass

        # this are unaffected.

        self.primary_table_name = primary_table_name

        self._date_table_names_cf = {
            str(n).strip().strip("'").casefold()
            for n in (date_table_names or ())
        }

        self.table_aliases = table_aliases or {}

        self._table_aliases_cf = {
            str(k).strip().strip("'").casefold(): v for k, v in self.table_aliases.items()
        }

        self.relationships = relationships

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

        self._var_scopes: List[Dict[str, str]] = []
        self.row_context_stack: List[str] = []

    def _extract_table_name(self, node: Any) -> Optional[str]:
        if node is None:
            return None
        if isinstance(node, IdentifierNode):
            return node.name.strip("'\"")
        if isinstance(node, ColumnRefNode):
            return (node.table or "").strip("'\"") or None
        if isinstance(node, FunctionCallNode):
            fn = node.func.upper()
            if fn in ("KEEPFILTERS", "ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "FILTER") and node.args:
                return self._extract_table_name(node.args[0])
        name = getattr(node, "name", None) or getattr(node, "table", None) or getattr(node, "raw", None)
        if name and isinstance(name, str) and not name.startswith("["):
            return name.strip("'\"")
        return None

    @contextlib.contextmanager
    def _row_context(self, table_name: Optional[str]):
        if table_name:
            clean_name = str(table_name).strip().strip("'\"")
            self.row_context_stack.append(clean_name)
            try:
                yield
            finally:
                self.row_context_stack.pop()
        else:
            yield



    def _lookup_var(self, name: str) -> Optional[str]:

        clean = str(name or "").strip("[]").casefold()

        for scope in reversed(self._var_scopes):

            if clean in scope:

                return scope[clean]

        return None



    def _render_var_node(self, node: VarNode) -> str:

        new_scope: Dict[str, str] = {}

        self._var_scopes.append(new_scope)

        if not hasattr(self, "_var_ast_map"):
            self._var_ast_map = {}
        try:
            for var_name, var_expr in node.variables:
                clean_name = str(var_name or "").strip("[]").casefold()
                self._var_ast_map[clean_name] = var_expr
                if self._extract_closing_balance_dimension(var_expr) is not None:
                    continue
                try:
                    var_sql = self._render_node(var_expr)
                    if var_sql and validate_emitted_sql(var_sql):
                        new_scope[clean_name] = f"({var_sql})"
                except self.DaxRenderError:
                    pass
            body_sql = self._render_node(node.body)

            if not body_sql or not validate_emitted_sql(body_sql):

                raise self.DaxRenderError("Failed to render RETURN body")

            return body_sql

        finally:

            self._var_scopes.pop()



    def render(self, node: Optional[DaxNode]) -> Optional[str]:

        """

        Render a DAX AST node to Snowflake SQL.



        Returns None if the node cannot be safely translated.

        """

        if node is None:

            return None

        # Rule B: A measure whose DAX is only a numeric literal -> constant metric rendered as MAX(<literal>)
        num_lit = dax_numeric_literal_value(node)
        if num_lit is not None:
            return f"MAX({num_lit})"

        try:
            self._detect_and_setup_loo(node)
            inlined_for_comparison = self._inline_vars_and_measures(node)
            self._detect_and_setup_comparison(inlined_for_comparison)
            sql = self._render_node(node)
            if not validate_emitted_sql(sql):
                raise self.DaxRenderError(f"Emitted SQL contains un-translated DAX syntax: {sql}")
            if "CURRENT_DATE()" in sql.upper() and not _ast_contains_func(node, {"TODAY", "NOW"}):
                if ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE not in self._advisory_categories:
                    self._advisory_categories.append(ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE)
            return sql

        except self.DaxRenderError as exc:

            logger.debug(f"AST render failed (recoverable): {exc}")
            self.last_render_error = str(exc)

            return None

    def _extract_shift_amount(self, arg1: DaxNode) -> int:
        if isinstance(arg1, UnaryOpNode) and arg1.op == "-":
            if isinstance(arg1.operand, LiteralNode):
                try:
                    val = arg1.operand.value
                    if isinstance(val, int) and not isinstance(val, bool):
                        return -val
                    if isinstance(val, str) and re.match(r"^\d+$", val.strip()):
                        return -int(val.strip())
                except (ValueError, TypeError):
                    pass
        elif isinstance(arg1, LiteralNode):
            try:
                val = arg1.value
                if isinstance(val, int) and not isinstance(val, bool):
                    return val
                if isinstance(val, str) and re.match(r"^-?\d+$", val.strip()):
                    return int(val.strip())
            except (ValueError, TypeError):
                pass
        raise self.DaxRenderError("DATEADD shift amount must be a literal integer")

    def _inline_vars_and_measures(
        self,
        node: DaxNode,
        seen_measures: Optional[Set[str]] = None,
        var_env: Optional[Dict[str, DaxNode]] = None,
    ) -> DaxNode:
        """Inline VAR definitions and referenced measures into a single expression tree
        before detecting time comparisons or leave-one-out patterns."""
        if node is None:
            return node
        if seen_measures is None:
            seen_measures = set()
        if var_env is None:
            var_env = {}

        if isinstance(node, VarNode):
            new_env = dict(var_env)
            for vname, vexpr in node.variables:
                clean_v = str(vname or "").strip("[]").casefold()
                inlined_vexpr = self._inline_vars_and_measures(vexpr, seen_measures, new_env)
                new_env[clean_v] = inlined_vexpr
            return self._inline_vars_and_measures(node.body, seen_measures, new_env)

        if isinstance(node, IdentifierNode):
            clean_id = str(node.name or "").strip("[]").casefold()
            if clean_id in var_env:
                return var_env[clean_id]
            return node

        if isinstance(node, ColumnRefNode):
            if not node.table:
                clean_c = str(node.column or "").strip("[]").casefold()
                if clean_c in var_env:
                    return var_env[clean_c]
            return node

        if isinstance(node, MeasureRefNode):
            clean_m = str(node.name or "").strip("[]").casefold()
            if clean_m in var_env:
                return var_env[clean_m]
            measure_dax = getattr(self, "measure_dax_map", None) or {}
            dax_text = measure_dax.get(node.name) or measure_dax.get(node.name.upper()) or measure_dax.get(clean_m)
            if dax_text and clean_m not in seen_measures:
                try:
                    parsed = DaxAstParser().parse(dax_text)
                    if parsed:
                        return self._inline_vars_and_measures(parsed, seen_measures | {clean_m}, var_env)
                except Exception:
                    pass
            return node

        if isinstance(node, FunctionCallNode):
            new_args = [self._inline_vars_and_measures(a, seen_measures, var_env) for a in node.args]
            return FunctionCallNode(func=node.func, args=new_args)

        if isinstance(node, BinaryOpNode):
            new_l = self._inline_vars_and_measures(node.left, seen_measures, var_env)
            new_r = self._inline_vars_and_measures(node.right, seen_measures, var_env)
            return BinaryOpNode(left=new_l, op=node.op, right=new_r)

        if isinstance(node, UnaryOpNode):
            new_op = self._inline_vars_and_measures(node.operand, seen_measures, var_env)
            return UnaryOpNode(op=node.op, operand=new_op)

        return node

    def _detect_and_setup_loo(self, node: DaxNode) -> None:
        """Pre-scan the AST for a leave-one-out pattern:
        CALCULATE(agg, FILTER(ALL(T), T[c] <> SELECTEDVALUE(T[c]))).
        Sets up self._current_loo_info so all parts of the measure (including sibling
        variables like AllAvg) can reference the correct decomposed window expressions."""
        if node is None:
            return

        loo_found = []

        def _walk(n: DaxNode):
            if n is None or loo_found:
                return
            if isinstance(n, FunctionCallNode) and n.func.upper() == "CALCULATE" and len(n.args) >= 2:
                base_arg = n.args[0]
                for f_arg in n.args[1:]:
                    if isinstance(f_arg, FunctionCallNode) and f_arg.func.upper() == "FILTER" and len(f_arg.args) >= 2:
                        f_tbl = f_arg.args[0]
                        f_pred = f_arg.args[1]
                        if isinstance(f_tbl, FunctionCallNode) and f_tbl.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS"):
                            if isinstance(f_pred, BinaryOpNode) and f_pred.op in ("<>", "!="):
                                col_node, sv_node = None, None
                                if isinstance(f_pred.left, ColumnRefNode) and isinstance(f_pred.right, FunctionCallNode) and f_pred.right.func.upper() == "SELECTEDVALUE":
                                    col_node, sv_node = f_pred.left, f_pred.right
                                elif isinstance(f_pred.right, ColumnRefNode) and isinstance(f_pred.left, FunctionCallNode) and f_pred.left.func.upper() == "SELECTEDVALUE":
                                    col_node, sv_node = f_pred.right, f_pred.left
                                if col_node and sv_node and sv_node.args and isinstance(sv_node.args[0], ColumnRefNode):
                                    c1 = sanitize_column(col_node.column).upper()
                                    c2 = sanitize_column(sv_node.args[0].column).upper()
                                    if c1 == c2:
                                        t1 = getattr(col_node, "table", None) or ""
                                        loo_found.append((t1, c1, base_arg))
                                        return
            for child in getattr(n, "args", []) or []:
                _walk(child)
            for attr in ("left", "right", "operand", "body"):
                if hasattr(n, attr):
                    _walk(getattr(n, attr))
            if isinstance(n, VarNode):
                for _, ve in n.variables:
                    _walk(ve)

        _walk(node)
        if not loo_found:
            return

        t1, c1, base_arg = loo_found[0]
        inner_fn = getattr(base_arg, "func", "").upper() if isinstance(base_arg, FunctionCallNode) else ""
        if inner_fn in ("MIN", "MAX", "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"):
            raise self.DaxRenderError("leave-one-out not decomposable")

        tbl_alias = self.table_aliases.get(t1, t1).upper() if (t1 and self.table_aliases) else self.table_alias.upper()
        dim_ref = f'{tbl_alias}."{c1}"'

        agg_col_node = base_arg.args[0] if (isinstance(base_arg, FunctionCallNode) and base_arg.args) else None
        agg_col_name = getattr(agg_col_node, "column", None) or getattr(agg_col_node, "name", None) or "AMOUNT"
        safe_agg_col = sanitize_column(agg_col_name).upper()

        fact_alias = self.table_alias.upper()
        sum_total = f'SUM({fact_alias}."{safe_agg_col}_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
        sum_own = f'{fact_alias}."{safe_agg_col}_SUM"'
        cnt_total = f'SUM({fact_alias}."{safe_agg_col}_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
        cnt_own = f'{fact_alias}."{safe_agg_col}_CNT"'

        self._current_loo_info = {
            "dim_ref": dim_ref,
            "col_name": c1,
            "agg_col": safe_agg_col,
            "inner_fn": inner_fn,
            "sum_total": sum_total,
            "sum_own": sum_own,
            "cnt_total": cnt_total,
            "cnt_own": cnt_own,
        }

    def _detect_and_setup_comparison(self, node: DaxNode) -> None:
        """Detect if this measure compares an unshifted reference [X] with a shifted reference
        CALCULATE([X], <time_func>), and prepare the comparison context so both sides receive
        matching period flags."""
        if node is None:
            return

        shifted_calls: List[Tuple[Any, str, int, str, DaxNode]] = []

        def _find_shifted(n: DaxNode):
            if isinstance(n, FunctionCallNode):
                fn = n.func.upper()
                if fn == "CALCULATE" and len(n.args) >= 2:
                    base = n.args[0]
                    for mod in n.args[1:]:
                        if isinstance(mod, FunctionCallNode):
                            mfn = mod.func.upper()
                            if mfn in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
                                u = "YEAR" if "YEAR" in mfn else ("MONTH" if "MONTH" in mfn else "QUARTER")
                                shifted_calls.append((base, u, -1, mfn, n))
                            elif mfn in ("DATEADD", "PARALLELPERIOD") and len(mod.args) >= 3:
                                amt = self._extract_shift_amount(mod.args[1])
                                intv_raw = str(getattr(mod.args[2], "name", getattr(mod.args[2], "value", "YEAR"))).upper()
                                if "YEAR" in intv_raw:
                                    u = "YEAR"
                                elif "QUARTER" in intv_raw:
                                    u = "QUARTER"
                                elif "MONTH" in intv_raw:
                                    u = "MONTH"
                                elif "DAY" in intv_raw:
                                    u = "DAY"
                                else:
                                    raise self.DaxRenderError(f"Unsupported DATEADD interval '{intv_raw}'")
                                shifted_calls.append((base, u, amt, mfn, n))
                for a in n.args:
                    _find_shifted(a)
            elif isinstance(n, BinaryOpNode):
                _find_shifted(n.left)
                _find_shifted(n.right)
            elif isinstance(n, UnaryOpNode):
                _find_shifted(n.operand)
            elif isinstance(n, VarNode):
                for _, ve in n.variables:
                    _find_shifted(ve)
                _find_shifted(n.body)

        _find_shifted(node)
        if not shifted_calls:
            return

        first_call = shifted_calls[0]
        base_unit = first_call[1]
        base_amt = first_call[2]
        base_mfn = first_call[3]
        for _, u, amt, mfn, _ in shifted_calls[1:]:
            if u != base_unit or amt != base_amt:
                raise self.DaxRenderError("Conflicting time windows in comparison measure")
            if (base_mfn == "SAMEPERIODLASTYEAR") != (mfn == "SAMEPERIODLASTYEAR"):
                raise self.DaxRenderError("Conflicting time windows in comparison measure")

        target_name = None
        target_agg = None
        if isinstance(first_call[0], MeasureRefNode):
            target_name = first_call[0].name
        elif isinstance(first_call[0], FunctionCallNode):
            target_agg = first_call[0]

        shifted_calc_ids = {id(sc[4]) for sc in shifted_calls}
        found_unshifted = [False]

        def _find_unshifted(n: DaxNode):
            if id(n) in shifted_calc_ids:
                return
            if isinstance(n, MeasureRefNode):
                if target_name and n.name.casefold() == target_name.casefold():
                    found_unshifted[0] = True
            elif isinstance(n, FunctionCallNode):
                if target_agg and n.func.upper() == target_agg.func.upper():
                    found_unshifted[0] = True
                for a in n.args:
                    _find_unshifted(a)
            elif isinstance(n, BinaryOpNode):
                _find_unshifted(n.left)
                _find_unshifted(n.right)
            elif isinstance(n, UnaryOpNode):
                _find_unshifted(n.operand)
            elif isinstance(n, VarNode):
                for _, ve in n.variables:
                    _find_unshifted(ve)
                _find_unshifted(n.body)

        _find_unshifted(node)
        if not found_unshifted[0]:
            return

        curr_shape = (f"CURRENT_{base_unit}",)
        curr_flag = self.anchor_flag_map.get(curr_shape) if self.anchor_flag_map else None

        lag_comp = self.lag_shape_component(base_mfn, base_unit, base_amt)
        lag_shape = (lag_comp,)
        lag_candidates = [lag_shape]
        if base_unit != "YEAR":
            n = abs(int(base_amt))
            lag_candidates += [
                (f"PREV_{base_unit}" if n == 1 else f"PREV_{n}_{base_unit}",),
                (f"SPLY_{base_unit}" if n == 1 else f"SPLY_{n}_{base_unit}",),
            ]
        lag_flag = None
        if self.anchor_flag_map:
            for cand_shape in lag_candidates:
                if self.anchor_flag_map.get(cand_shape):
                    lag_flag = self.anchor_flag_map[cand_shape]
                    lag_shape = cand_shape
                    break
        # Only use flag-based comparison when the enriched view actually
        # declares both flags; never reference a guessed column name.
        if not curr_flag or not lag_flag:
            return

        self._comparison_context = {
            "target_name": target_name,
            "target_agg": target_agg,
            "unit": base_unit,
            "curr_flag": curr_flag,
            "lag_flag": lag_flag,
            "lag_shape": lag_shape,
        }
        self.comparison_unit = base_unit.lower()
        self.comparison_comment = f"latest {base_unit.lower()} vs previous {base_unit.lower()} only"

    def _is_operand_nullable(self, node: Optional[DaxNode], rendered_sql: str) -> bool:
        """Operand nullability decided by the OUTERMOST function:
        COUNT(...) is never nullable (even with CASE inside) -> False (non-nullable).
        Numeric/boolean literals and constants -> False (non-nullable).
        Otherwise (SUM, AVG, MIN, MAX, CASE without ELSE, arithmetic) -> True (nullable).
        """
        clean_sql = self._unwrap_str(rendered_sql)

        # 1. Outermost COUNT is never nullable (even with CASE inside)
        if re.match(r"^COUNT(?:_IF)?\s*\(", clean_sql, re.IGNORECASE):
            return False

        # 2. Numeric and boolean constants are never nullable
        if re.match(r"^-?\d+(\.\d+)?$", clean_sql):
            return False
        if clean_sql.upper() in ("TRUE", "FALSE"):
            return False

        # 3. Outer node is COUNT in AST
        if node is not None:
            unwrapped_node = unwrap_var_body(node)
            if isinstance(unwrapped_node, FunctionCallNode) and unwrapped_node.func.upper() in (
                "COUNT", "COUNTA", "COUNTROWS", "DISTINCTCOUNT"
            ):
                return False

        # 4. Any SUM/AVG/AVERAGE/MIN/MAX is nullable
        if re.search(r"\b(SUM|AVG|AVERAGE|MIN|MAX)\s*\(", clean_sql, re.IGNORECASE):
            return True

        # Check for a CASE without an ELSE
        tokens = re.findall(r"\b(CASE|ELSE|END)\b", clean_sql, re.IGNORECASE)
        if tokens:
            stack = []
            for tok in tokens:
                t = tok.upper()
                if t == "CASE":
                    stack.append(False)
                elif t == "ELSE":
                    if stack:
                        stack[-1] = True
                elif t == "END":
                    if stack:
                        has_else = stack.pop()
                        if not has_else:
                            return True

        if node is not None and self._node_contains_nullable_func(node):
            return True

        return True

    def _node_contains_nullable_func(self, node: DaxNode) -> bool:
        if isinstance(node, FunctionCallNode):
            func_u = node.func.upper()
            if func_u in ("SUM", "AVG", "AVERAGE", "MIN", "MAX"):
                return True
            return any(self._node_contains_nullable_func(a) for a in node.args)
        if isinstance(node, BinaryOpNode):
            return self._node_contains_nullable_func(node.left) or self._node_contains_nullable_func(node.right)
        if isinstance(node, UnaryOpNode):
            return self._node_contains_nullable_func(node.operand)
        if isinstance(node, VarNode):
            return any(self._node_contains_nullable_func(v[1]) for v in node.variables) or self._node_contains_nullable_func(node.body)
        return False

    def _unwrap_str(self, s: str) -> str:
        s = (s or "").strip()
        while s.startswith("(") and s.endswith(")"):
            depth = 0
            matched = True
            for c in s[:-1]:
                if c == "(": depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        matched = False
                        break
            if matched and depth == 1:
                s = s[1:-1].strip()
            else:
                break
        return s

    def _safe_coalesce_operand(self, s: str, is_nullable: bool) -> str:
        if not is_nullable:
            return s
        unwrapped = self._unwrap_str(s)
        u = unwrapped.upper()
        if (u.startswith("COALESCE(") and (u.endswith(", 0)") or u.endswith(", 0.0)"))) or (u.startswith("IFF(") and u.endswith(", 0)")):
            return s
        return f"COALESCE({s}, 0)"



    def _render_node(self, node: DaxNode) -> str:

        if isinstance(node, VarNode):

            return self._render_var_node(node)

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

            var_sql = self._lookup_var(node.name)

            if var_sql is not None:

                return var_sql

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
        col_clean = (node.column or "").strip("[]")
        col_cf = col_clean.casefold()
        tbl_clean = (node.table or "").strip("'\"")
        qualified_cf = f"{tbl_clean}[{col_clean}]".casefold() if tbl_clean else None

        if not tbl_clean:
            var_sql = self._lookup_var(col_clean)
            if var_sql is not None:
                return var_sql
            if col_cf in self._measure_sql_map_by_casefold:
                val = self._measure_sql_map_by_casefold[col_cf]
                if val.strip().upper() == "NULL":
                    return "NULL"
                if self.row_context_stack:
                    return self._row_level_measure_sql(col_clean, val)
                return f"({val})"
            if col_cf in self._known_measure_names_casefold:
                raise self.DaxRenderError(
                    f"Unresolved measure reference [{col_clean}]: known measure with no resolved SQL yet"
                )
        elif qualified_cf:
            if qualified_cf in self._measure_sql_map_by_casefold:
                val = self._measure_sql_map_by_casefold[qualified_cf]
                if val.strip().upper() == "NULL":
                    return "NULL"
                return f"({val})"
            if qualified_cf in self._known_measure_names_casefold:
                raise self.DaxRenderError(
                    f"Unresolved measure reference [{node.raw}]: known measure with no resolved SQL yet"
                )

        col = sanitize_column(node.column)
        if tbl_clean:
            alias = self._resolve_table_alias(tbl_clean)
        elif self.row_context_stack:
            alias = self._resolve_table_alias(self.row_context_stack[-1])
        else:
            alias = self.table_alias
        return f'{alias}."{col}"'

    def _resolve_table_alias(self, table_name: str) -> str:
        name_clean = str(table_name or "").strip().strip("'\"")
        name_cf = name_clean.casefold()

        if hasattr(self, "_userel_overrides") and self._userel_overrides:
            if name_cf in self._userel_overrides:
                return self._userel_overrides[name_cf]
            ali_cf = to_alias(name_clean).casefold()
            if ali_cf in self._userel_overrides:
                return self._userel_overrides[ali_cf]
            san_cf = sanitize_column(name_clean).casefold()
            if san_cf in self._userel_overrides:
                return self._userel_overrides[san_cf]

        if not name_clean:
            return self.table_alias

        if self.primary_table_name and name_cf == self.primary_table_name.strip().strip("'\"").casefold():
            return self.table_alias

        # No primary-table context, but the referenced table's own alias IS the
        # metric's alias (differing only in case): reuse the caller's exact
        # alias spelling so one metric never mixes `sales.` and `SALES.`.
        if (
            not self.primary_table_name
            and self.table_alias
            and to_alias(name_clean).casefold() == str(self.table_alias).casefold()
        ):
            return self.table_alias

        if hasattr(self, "_table_aliases_cf") and name_cf in self._table_aliases_cf:
            if ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS not in self._advisory_categories:
                self._advisory_categories.append(ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS)
            return self._table_aliases_cf[name_cf]

        if self.primary_table_name and self.relationships is not None:
            has_any_rel = any(
                str(getattr(r, "from_dataset", "")).casefold() == self.primary_table_name.strip("'\"").casefold()
                or str(getattr(r, "to_dataset", "")).casefold() == self.primary_table_name.strip("'\"").casefold()
                for r in self.relationships
            )
            if has_any_rel and not has_relationship_path(self.relationships, self.primary_table_name, name_clean):
                raise self.DaxRenderError(
                    f"Column reference to table '{table_name}' has no relationship path from "
                    f"'{self.primary_table_name}'"
                )

        if hasattr(self, "_table_aliases_cf") and name_cf in self._table_aliases_cf:
            if ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS not in self._advisory_categories:
                self._advisory_categories.append(ADVISORY_CATEGORY_CALCULATE_FILTER_SEMANTICS)
            return self._table_aliases_cf[name_cf]

        return to_alias(name_clean)



    _PRECEDENCE = {

        "OR": 10,

        "AND": 20,

        "=": 30, "<>": 30, "<": 30, "<=": 30, ">": 30, ">=": 30,

        "+": 40, "-": 40,

        "*": 50, "/": 50,

    }



    def _is_primary_table(self, table_name: Optional[str]) -> bool:
        t = str(table_name or "").strip().strip("'\"")
        if not t:
            return False
        if self.primary_table_name:
            p = self.primary_table_name.strip().strip("'\"")
            if t.casefold() == p.casefold():
                return True
        return to_alias(t).casefold() == str(self.table_alias or "").casefold() or t.casefold() == str(self.table_alias or "").casefold()

    def _row_level_measure_sql(self, name: str, measure_sql: str) -> str:
        """A measure referenced inside a row iterator (SUMX/FILTER/...) is
        evaluated with context transition: once per iterated row. When the
        iterated table is this metric's own fact table and the measure is a
        single SUM/MIN/MAX/AVG over that table's columns (or COUNT(*)), the
        per-row value is the row's own value (or 1), so the iterator can be
        rendered at row grain. Anything else needs per-row re-aggregation
        this translator cannot express -- declined."""
        tbl = self.row_context_stack[-1]
        const_probe = re.sub(r"'(?:''|[^'])*'", "''", measure_sql)
        if not self._AGG_CALL_START.search(const_probe) and not re.search(
            r"\b[A-Za-z_][A-Za-z0-9_$]*\s*\.\s*(?:\"|[A-Za-z_])", const_probe
        ) and not re.search(r"\b(CURRENT_DATE|CURRENT_TIMESTAMP)\b", const_probe, re.IGNORECASE):
            # A constant measure has the same value in every row context.
            return f"({self._unwrap_str(measure_sql)})"
        if not self._is_primary_table(tbl):
            raise self.DaxRenderError(
                f"not applicable: measure [{name}] is evaluated once per row of '{tbl}' (context transition) "
                "and re-aggregated; this per-group aggregation is not expressible as a single semantic-view metric"
            )
        s = self._unwrap_str(measure_sql)
        if re.fullmatch(r"COUNT\s*\(\s*\*\s*\)", s, re.IGNORECASE):
            return "1"
        m = re.match(r"^(SUM|MIN|MAX|AVG)\s*\(", s, re.IGNORECASE)
        if m:
            close = self._find_balanced_close_paren(s, m.end() - 1)
            if close == len(s) - 1:
                arg = s[m.end():close].strip()
                arg = re.sub(r"::FLOAT\s*$", "", arg, flags=re.IGNORECASE).strip()
                refs = re.findall(r"\b([A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*(?=\"|[A-Za-z_])", re.sub(r"'(?:''|[^'])*'", "''", arg))
                if not self._AGG_CALL_START.search(arg) and all(r.casefold() == str(self.table_alias).casefold() for r in refs):
                    return f"({arg})"
        raise self.DaxRenderError(
            f"not applicable: measure [{name}] inside a row iterator requires context transition over an "
            "expression that is not a single row-level aggregate of the iterated table"
        )

    def _has_many_to_one_path(self, src: str, dst: str) -> bool:
        src_cf = str(src or "").strip("'\"").casefold()
        dst_cf = str(dst or "").strip("'\"").casefold()
        if src_cf == dst_cf:
            return True
        edges: Dict[str, Set[str]] = {}
        for r in self.relationships or []:
            if getattr(r, "is_active", getattr(r, "isActive", True)) is False:
                continue
            f = str(getattr(r, "from_dataset", None) or getattr(r, "from_table", "") or "").strip("'\"").casefold()
            t = str(getattr(r, "to_dataset", None) or getattr(r, "to_table", "") or "").strip("'\"").casefold()
            if f and t:
                edges.setdefault(f, set()).add(t)
        seen, stack = {src_cf}, [src_cf]
        while stack:
            cur = stack.pop()
            for nxt in edges.get(cur, ()):
                if nxt == dst_cf:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def _render_related(self, node: FunctionCallNode) -> str:
        if len(node.args) != 1 or not isinstance(node.args[0], ColumnRefNode) or not node.args[0].table:
            raise self.DaxRenderError("RELATED requires a single Table[Column] argument")
        target = node.args[0].table
        src = self.row_context_stack[-1] if self.row_context_stack else self.primary_table_name
        if self.relationships is not None and src and not self._has_many_to_one_path(src, target):
            raise self.DaxRenderError(
                f"not applicable: RELATED('{target}') has no active many-to-one relationship path from '{src}'"
            )
        return self._render_column_ref(node.args[0])

    def _render_countblank(self, node: FunctionCallNode) -> str:
        if len(node.args) != 1 or not isinstance(node.args[0], ColumnRefNode):
            raise self.DaxRenderError("COUNTBLANK requires a single column argument")
        col = node.args[0]
        tbl = col.table or self.primary_table_name
        if tbl and not self._is_primary_table(tbl):
            raise self.DaxRenderError(
                f"not applicable: COUNTBLANK over '{tbl}' counts rows of a different table than this metric's fact table"
            )
        col_type = self._column_data_type(tbl, col.column)
        if col_type not in ("num", "date", "bool"):
            raise self.DaxRenderError(
                "declined: COUNTBLANK on a text or untyped column (empty-string handling) is not translated"
            )
        col_sql = self._render_column_ref(col)
        # DAX returns BLANK (not 0) when no blank rows are found.
        return f"NULLIF(COUNT(*) - COUNT({col_sql}), 0)"

    def _render_date_bound(self, node: DaxNode, func_name: str) -> Optional[str]:
        if isinstance(node, FunctionCallNode):
            fn = node.func.upper()
            if fn == "BLANK" and not node.args:
                return None
            if fn == "DATE" and len(node.args) == 3 and all(
                isinstance(a, LiteralNode) and isinstance(a.value, int) and not isinstance(a.value, bool)
                for a in node.args
            ):
                y, m, d = (a.value for a in node.args)
                import datetime as _dt
                try:
                    _dt.date(y, m, d)
                except ValueError:
                    raise self.DaxRenderError(f"not applicable: invalid DATE({y},{m},{d}) in {func_name}")
                return f"DATE '{y:04d}-{m:02d}-{d:02d}'"
            if fn in ("TODAY", "NOW") and not node.args:
                return "CURRENT_DATE()"
        raise self.DaxRenderError(
            f"not applicable: {func_name} bounds must be literal dates (DATE(y,m,d)), TODAY() or BLANK()"
        )

    def _render_datesbetween(self, node: FunctionCallNode) -> str:
        if len(node.args) != 3:
            raise self.DaxRenderError("DATESBETWEEN requires 3 arguments")
        d_sql = self._render_node(node.args[0])
        lo = self._render_date_bound(node.args[1], "DATESBETWEEN")
        hi = self._render_date_bound(node.args[2], "DATESBETWEEN")
        conds = []
        if lo:
            conds.append(f"{d_sql} >= {lo}")
        if hi:
            conds.append(f"{d_sql} <= {hi}")
        return " AND ".join(conds) if conds else "TRUE"

    def _render_datesinperiod(self, node: FunctionCallNode) -> str:
        if len(node.args) != 4:
            raise self.DaxRenderError("DATESINPERIOD requires 4 arguments")
        dates, start, n_node, unit_node = node.args
        n = self._extract_shift_amount(n_node)
        unit = str(getattr(unit_node, "name", getattr(unit_node, "value", "")) or "").strip("'\" ").upper()
        if unit not in ("DAY", "MONTH", "QUARTER", "YEAR"):
            raise self.DaxRenderError(f"not applicable: DATESINPERIOD interval '{unit}' not supported")
        d_sql = self._render_node(dates)
        anchor = None
        if isinstance(start, FunctionCallNode) and start.func.upper() in ("MAX", "LASTDATE") and start.args:
            # Last visible date of the filter context.
            if n < 0 and unit == "MONTH":
                flag = self.anchor_flag_map.get((f"R{abs(n)}M",)) if self.anchor_flag_map else None
                if flag:
                    return f'{self.table_alias}."{flag}"'
            anchor = "CURRENT_DATE()"
        else:
            anchor = self._render_date_bound(start, "DATESINPERIOD")
            if anchor is None:
                raise self.DaxRenderError("not applicable: DATESINPERIOD start date is BLANK()")
        if n == 0:
            return "FALSE"
        if n < 0:
            return f"{d_sql} > DATEADD({unit}, {n}, {anchor}) AND {d_sql} <= {anchor}"
        return f"{d_sql} >= {anchor} AND {d_sql} < DATEADD({unit}, {n}, {anchor})"

    _ALL_LIKE_FUNCS = ("ALL", "ALLEXCEPT", "REMOVEFILTERS", "ALLNOBLANKROW", "ALLSELECTED")

    def _render_calc_filter_predicate(self, f: DaxNode) -> str:
        """Row-level SQL predicate for one CALCULATE filter argument that is a
        boolean filter (column comparison / IN / NOT / ISBLANK / KEEPFILTERS /
        FILTER(table, pred) / DATESBETWEEN / DATESINPERIOD)."""
        if isinstance(f, (BinaryOpNode, UnaryOpNode)):
            return self._render_node(f)
        if isinstance(f, FunctionCallNode):
            fn = f.func.upper()
            if fn == "KEEPFILTERS":
                if not f.args:
                    raise self.DaxRenderError("KEEPFILTERS requires 1 argument")
                return self._render_calc_filter_predicate(f.args[0])
            if fn == "FILTER" and len(f.args) >= 2:
                t = f.args[0]
                if isinstance(t, FunctionCallNode) and t.func.upper() in self._ALL_LIKE_FUNCS:
                    tbl_name = self._extract_table_name(t.args[0]) if t.args else None
                    if not tbl_name or self._is_primary_table(tbl_name):
                        raise self.DaxRenderError(
                            "not applicable: FILTER(ALL(<fact table>), ...) removes every filter on the fact table "
                            "and is not expressible as a row-level CASE filter"
                        )
                iter_tbl = self._extract_table_name(t)
                with self._row_context(iter_tbl):
                    return self._render_node(f.args[1])
            if fn in ("NOT", "ISBLANK", "TRUE", "FALSE"):
                return self._render_node(f)
            if fn == "DATESBETWEEN":
                return self._render_datesbetween(f)
            if fn == "DATESINPERIOD":
                return self._render_datesinperiod(f)
            if fn == "USERELATIONSHIP":
                raise self.DaxRenderError(USERELATIONSHIP_REASON)
            raise self.DaxRenderError(f"not applicable: CALCULATE filter modifier {fn} is not supported")
        raise self.DaxRenderError(
            f"CALCULATE filter argument of type {type(f).__name__} "
            "is not a recognized filter modifier or comparison predicate"
        )

    def _render_measure_ref(self, node: MeasureRefNode) -> str:

        var_sql = self._lookup_var(node.name)

        if var_sql is not None:

            return var_sql

        name_cf = node.name.casefold()
        if name_cf in self._measure_sql_map_by_casefold:
            val = self._measure_sql_map_by_casefold[name_cf]
            if val.strip().upper() == "NULL":
                return "NULL"
            if self.row_context_stack:
                return self._row_level_measure_sql(node.name, val)
            if (
                getattr(self, "_comparison_context", None)
                and self._comparison_context.get("target_name")
                and name_cf == self._comparison_context["target_name"].casefold()
                and not getattr(self, "_in_shifted_calculate", 0)
            ):
                curr_flag = self._comparison_context["curr_flag"]
                filtered = self._inject_case_filter_into_rendered_aggregate(
                    val, f'{self.table_alias}."{curr_flag}"'
                )
                if filtered:
                    return f"({filtered})"
            return f"({val})"

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

        # Priority: innermost iterated table > home table
        if self.row_context_stack:
            target_table = self.row_context_stack[-1]
            alias = self._resolve_table_alias(target_table)
        else:
            target_table = self.primary_table_name or self.table_alias
            alias = self.table_alias

        known_cols = None
        if getattr(self, "dataset_col_lookup", None):
            for ds_k, cols in self.dataset_col_lookup.items():
                al = (self.table_aliases.get(ds_k) or ds_k).upper() if getattr(self, "table_aliases", None) else ds_k.upper()
                if al == alias.upper() or ds_k.upper() == str(target_table).upper() or ds_k.upper() == alias.upper():
                    known_cols = {c.upper() for c in cols}
                    break
        if known_cols is None and getattr(self, "datasets", None):
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                al = (self.table_aliases.get(ds_name) or ds_name).upper() if getattr(self, "table_aliases", None) else ds_name.upper()
                if al == alias.upper() or ds_name.upper() == str(target_table).upper() or ds_name.upper() == alias.upper():
                    known_cols = {getattr(c, "unique_name", getattr(c, "name", "")).upper() for c in (getattr(ds, "columns", []) or [])}
                    break

        if known_cols is not None:
            node_clean = re.sub(r'[^A-Z0-9]', '', node.name.upper())
            matching_col = None
            for kc in known_cols:
                if re.sub(r'[^A-Z0-9]', '', kc) == node_clean:
                    matching_col = kc
                    break
            if not matching_col:
                raise self.DaxRenderError(
                    f"Unresolved column or measure reference [{node.name}] in table '{alias}'"
                )
            col = sanitize_column(matching_col)
            return f'{alias}."{col}"'

        col = sanitize_column(node.name)
        return f'{alias}."{col}"'



    # ------------------------------------------------------------------
    # DAX comparison semantics (BLANK rules)
    # ------------------------------------------------------------------
    _CMP_FLIP = {"=": "=", "<>": "<>", "<": ">", ">": "<", "<=": ">=", ">=": "<="}

    def _literal_kind(self, node: Optional[DaxNode]) -> Optional[Tuple[str, Any]]:
        """('num'|'text'|'bool'|'blank', python value) for a literal operand."""
        if isinstance(node, LiteralNode):
            if isinstance(node.value, bool):
                return ("bool", node.value)
            if isinstance(node.value, (int, float)):
                return ("num", node.value)
            if isinstance(node.value, str):
                return ("text", node.value)
        if isinstance(node, UnaryOpNode) and node.op == "-" and isinstance(node.operand, LiteralNode):
            if isinstance(node.operand.value, (int, float)) and not isinstance(node.operand.value, bool):
                return ("num", -node.operand.value)
        if isinstance(node, FunctionCallNode) and not node.args:
            fn = node.func.upper()
            if fn == "BLANK":
                return ("blank", None)
            if fn in ("TRUE", "FALSE"):
                return ("bool", fn == "TRUE")
        return None

    def _column_data_type(self, table: Optional[str], column: str) -> Optional[str]:
        """'num'|'text'|'bool'|'date' from the model's dataset metadata, or None."""
        datasets = self.datasets or getattr(self.model_metadata, "datasets", None) or []
        tbl_cf = str(table or "").strip("'\"").casefold()
        col_cf = str(column or "").strip("[]").casefold()
        col_san = sanitize_column(col_cf).casefold() if col_cf else ""
        for ds in datasets:
            names = {
                str(getattr(ds, a, "") or "").strip("'\"").casefold()
                for a in ("unique_name", "name", "label")
            }
            names |= {to_alias(n).casefold() for n in list(names) if n}
            if tbl_cf and tbl_cf not in names and to_alias(tbl_cf).casefold() not in names:
                continue
            for col in getattr(ds, "columns", []) or []:
                cn = {str(getattr(col, a, "") or "").casefold() for a in ("unique_name", "name", "label")}
                if col_cf not in cn and col_san not in {sanitize_column(c).casefold() for c in cn if c}:
                    continue
                dt = getattr(col, "data_type", None)
                dt = str(getattr(dt, "value", dt) or "").lower()
                explicit = "data_type" in (getattr(col, "model_fields_set", None) or {"data_type"})
                if dt in ("integer", "decimal", "float", "int", "double", "number", "int64", "currency"):
                    return "num"
                if dt in ("boolean", "bool"):
                    return "bool"
                if dt in ("date", "datetime", "time", "timestamp"):
                    return "date"
                if dt in ("string", "text") and (explicit or getattr(col, "source_type", "")):
                    return "text"
                return None
            if tbl_cf:
                return None
        return None

    _NUMERIC_RESULT_FUNCS = frozenset({
        "SUM", "AVERAGE", "COUNT", "COUNTA", "COUNTROWS", "COUNTBLANK", "DISTINCTCOUNT",
        "DISTINCTCOUNTNOBLANK", "SUMX", "AVERAGEX", "COUNTX", "COUNTAX", "DIVIDE", "ABS", "ROUND",
        "ROUNDUP", "ROUNDDOWN", "INT", "VALUE", "TOTALYTD", "TOTALQTD", "TOTALMTD", "YEAR",
        "MONTH", "DAY", "QUARTER", "WEEKDAY", "WEEKNUM", "HOUR", "MINUTE", "SECOND", "DATEDIFF",
        "MROUND", "CEILING", "FLOOR", "LEN", "POWER", "SQRT", "MOD", "RANKX",
    })
    _DATE_RESULT_FUNCS = frozenset({"DATE", "TODAY", "NOW", "EOMONTH", "EDATE", "DATEVALUE", "LASTDATE", "FIRSTDATE"})
    _TEXT_RESULT_FUNCS = frozenset({"FORMAT", "CONCATENATE", "LEFT", "RIGHT", "MID", "UPPER", "LOWER", "TRIM", "SUBSTITUTE", "REPLACE"})

    def _expr_type(self, node: Optional[DaxNode], depth: int = 0) -> Optional[str]:
        if node is None or depth > 20:
            return None
        lk = self._literal_kind(node)
        if lk is not None:
            return None if lk[0] == "blank" else lk[0]
        if isinstance(node, MeasureRefNode):
            if self._lookup_var(node.name) is not None:
                var_ast = getattr(self, "_var_ast_map", {}).get(node.name.strip("[]").casefold())
                return self._expr_type(var_ast, depth + 1) if var_ast is not None else None
            if node.name.casefold() in self._measure_sql_map_by_casefold:
                return "num"
            tbl = self.row_context_stack[-1] if self.row_context_stack else self.primary_table_name
            return self._column_data_type(tbl, node.name)
        if isinstance(node, ColumnRefNode):
            if not node.table:
                if self._lookup_var(node.column) is not None:
                    var_ast = getattr(self, "_var_ast_map", {}).get(node.column.strip("[]").casefold())
                    return self._expr_type(var_ast, depth + 1) if var_ast is not None else None
                if node.column.strip("[]").casefold() in self._measure_sql_map_by_casefold:
                    return "num"
            tbl = node.table or (self.row_context_stack[-1] if self.row_context_stack else self.primary_table_name)
            return self._column_data_type(tbl, node.column)
        if isinstance(node, IdentifierNode):
            var_ast = getattr(self, "_var_ast_map", {}).get(str(node.name).strip("[]").casefold())
            return self._expr_type(var_ast, depth + 1) if var_ast is not None else None
        if isinstance(node, VarNode):
            return self._expr_type(node.body, depth + 1)
        if isinstance(node, UnaryOpNode):
            return "bool" if node.op.upper() == "NOT" else self._expr_type(node.operand, depth + 1)
        if isinstance(node, BinaryOpNode):
            if node.op in ("+", "-", "*", "/", "^"):
                return "num"
            if node.op == "&":
                return "text"
            return "bool"
        if isinstance(node, FunctionCallNode):
            fn = node.func.upper()
            if fn in self._NUMERIC_RESULT_FUNCS:
                return "num"
            if fn in self._DATE_RESULT_FUNCS:
                return "date"
            if fn in self._TEXT_RESULT_FUNCS:
                return "text"
            if fn in ("MIN", "MAX", "MINX", "MAXX", "CALCULATE", "COALESCE", "SELECTEDVALUE", "RELATED"):
                idx = 1 if fn in ("MINX", "MAXX") else 0
                if len(node.args) > idx:
                    t = self._expr_type(node.args[idx], depth + 1)
                    if t is None and fn == "CALCULATE":
                        return None
                    return t
            if fn in ("IF",) and len(node.args) >= 2:
                for a in node.args[1:]:
                    t = self._expr_type(a, depth + 1)
                    if t:
                        return t
            if fn in ("ISBLANK", "NOT", "AND", "OR", "HASONEVALUE", "ISFILTERED", "ISINSCOPE", "CONTAINSSTRING"):
                return "bool"
        return None

    def _negate(self, cond_sql: str) -> str:
        """DAX NOT over a predicate. A DAX comparison is never BLANK, so a
        SQL NULL produced by a comparison stands for DAX FALSE; NOT must turn
        it into TRUE (plain SQL NOT NULL is NULL, i.e. false)."""
        return f"NOT COALESCE({cond_sql}, FALSE)"

    @staticmethod
    def _blank_equiv_sql(kind: str) -> Tuple[str, Any]:
        return {"num": ("0", 0), "text": ("''", ""), "bool": ("FALSE", False)}[kind]

    @staticmethod
    def _py_compare(a: Any, op: str, b: Any) -> bool:
        try:
            if isinstance(a, str) and isinstance(b, str):
                a, b = a.casefold(), b.casefold()
            return {
                "=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b,
            }[op]
        except TypeError:
            return False

    def _render_in(self, node: BinaryOpNode) -> str:
        right = node.right
        if not (isinstance(right, FunctionCallNode) and right.func == TABLE_CONSTRUCTOR_FUNC):
            raise self.DaxRenderError("not applicable: IN over a table expression (only a literal list { ... } is supported)")
        items = []
        for item in right.args:
            lk = self._literal_kind(item)
            if lk is None:
                raise self.DaxRenderError("not applicable: IN list contains a non-literal value")
            if lk[0] == "blank" or (lk[0] in ("num", "text", "bool") and lk[1] == self._blank_equiv_sql(lk[0])[1]):
                raise self.DaxRenderError(
                    "not applicable: IN list containing a blank-equivalent value (BLANK(), 0, \"\" or FALSE) "
                    "has BLANK-matching semantics that are not translated"
                )
            items.append(self._render_node(item))
        if not items:
            return "FALSE"
        x_sql = self._render_node(node.left)
        return f"{x_sql} IN ({', '.join(items)})"

    def _render_comparison(self, node: BinaryOpNode) -> str:
        """Render a DAX comparison following DAX BLANK rules:
        BLANK = 0 / BLANK = "" / BLANK = FALSE are TRUE, and BLANK compares
        as 0 (numbers) or "" (text) under every relational operator."""
        op = node.op
        lk, rk = self._literal_kind(node.left), self._literal_kind(node.right)
        if lk is not None and rk is not None:
            if lk[0] == "blank" or rk[0] == "blank":
                other = rk if lk[0] == "blank" else lk
                if other[0] == "blank":
                    return "TRUE" if op in ("=", "<=", ">=") else "FALSE"
                beq = self._blank_equiv_sql(other[0])[1] if other[0] in ("num", "text", "bool") else None
                a, b = (beq, other[1]) if lk[0] == "blank" else (other[1], beq)
                return "TRUE" if self._py_compare(a, op, b) else "FALSE"
            return f"{self._render_node(node.left)} {op} {self._render_node(node.right)}"
        if lk is not None and rk is None:
            x_node, lit, op = node.right, lk, self._CMP_FLIP[op]
            lit_node = node.left
        elif rk is not None:
            x_node, lit, lit_node = node.left, rk, node.right
        else:
            # Neither side is a literal.
            left = self._render_node(node.left)
            right = self._render_node(node.right)
            if isinstance(node.left, BinaryOpNode) and node.left.op in self._CMP_FLIP:
                left = f"({left})"
            if isinstance(node.right, BinaryOpNode) and node.right.op in self._CMP_FLIP:
                right = f"({right})"
            lt, rt = self._expr_type(node.left), self._expr_type(node.right)
            if lt == "num" and rt == "num":
                left = self._safe_coalesce_operand(left, self._is_operand_nullable(node.left, left))
                right = self._safe_coalesce_operand(right, self._is_operand_nullable(node.right, right))
            elif lt == "text" and rt == "text":
                if self._is_operand_nullable(node.left, left):
                    left = f"COALESCE({left}, '')"
                if self._is_operand_nullable(node.right, right):
                    right = f"COALESCE({right}, '')"
            return f"{left} {op} {right}"

        x_sql = self._render_node(x_node)
        if isinstance(x_node, BinaryOpNode) and x_node.op in self._CMP_FLIP:
            x_sql = f"({x_sql})"
        if x_sql.strip().upper() in ("NULL", "(NULL)"):
            # A BLANK operand (e.g. an unresolvable parameter measure).
            if lit[0] == "blank":
                return "TRUE" if op in ("=", "<=", ">=") else "FALSE"
            beq_sql, _ = self._blank_equiv_sql(lit[0])
            return f"COALESCE({x_sql}, {beq_sql}) {op} {self._render_node(lit_node)}"
        if lit[0] == "blank":
            x_type = self._expr_type(x_node)
            if x_type in ("num", "text", "bool"):
                beq_sql, _ = self._blank_equiv_sql(x_type)
                return f"COALESCE({x_sql}, {beq_sql}) {op} {beq_sql}"
            if x_type == "date" and op in ("=", "<>"):
                return f"{x_sql} IS NULL" if op == "=" else f"{x_sql} IS NOT NULL"
            raise self.DaxRenderError(
                "declined: comparison with BLANK() on an expression whose data type is unknown "
                "(DAX treats BLANK as equal to 0 for numbers and to \"\" for text); "
                "use ISBLANK() for a pure blank test"
            )
        lit_sql = self._render_node(lit_node)
        beq_sql, beq_val = self._blank_equiv_sql(lit[0])
        if self._py_compare(beq_val, op, lit[1]) and self._is_operand_nullable(x_node, x_sql):
            return f"COALESCE({x_sql}, {beq_sql}) {op} {lit_sql}"
        return f"{x_sql} {op} {lit_sql}"

    def _render_binary(self, node: BinaryOpNode) -> str:
        if node.op.upper() == "IN":
            return self._render_in(node)
        if node.op == "&":
            raise self.DaxRenderError("not applicable: text result (& concatenation)")
        if node.op in self._CMP_FLIP:
            return self._render_comparison(node)
        left = self._render_node(node.left)
        right = self._render_node(node.right)

        op_clean = node.op.upper()

        parent_prec = self._PRECEDENCE.get(op_clean, 0)



        if isinstance(node.left, BinaryOpNode):

            left_prec = self._PRECEDENCE.get(node.left.op.upper(), 0)

            if left_prec < parent_prec:

                left = f"({left})"



        if isinstance(node.right, BinaryOpNode):

            right_prec = self._PRECEDENCE.get(node.right.op.upper(), 0)

            if right_prec < parent_prec or (op_clean in ("-", "/") and right_prec <= parent_prec):

                right = f"({right})"



        op_map = {

            "=": "=", "<>": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">=",

            "+": "+", "-": "-", "*": "*", "/": "/",

            "AND": "AND", "OR": "OR",

        }

        sql_op = op_map.get(op_clean, node.op)

        def _needs_coalesce_zero(expr_str: str) -> bool:
            s = expr_str.strip()
            while s.startswith("(") and s.endswith(")"):
                s = s[1:-1].strip()
            if s.upper() == "NULL":
                return True
            if re.search(r'\*\s*\(?\s*NULL\s*\)?\b', expr_str, re.IGNORECASE):
                return True
            return False

        if sql_op in ("AND", "OR"):
            return f"({left}) {sql_op} ({right})"

        def _is_numeric_literal(n: Optional[DaxNode], s: str) -> bool:
            if n is not None and isinstance(n, LiteralNode):
                try:
                    float(str(n.value).strip())
                    return True
                except (ValueError, TypeError):
                    pass
            clean_s = (s or "").strip()
            while clean_s.startswith("(") and clean_s.endswith(")"):
                clean_s = clean_s[1:-1].strip()
            try:
                float(clean_s)
                return True
            except (ValueError, TypeError):
                return False

        if sql_op in ("+", "-"):
            is_left_nullable = self._is_operand_nullable(node.left, left)
            is_right_nullable = self._is_operand_nullable(node.right, right)

            if not is_left_nullable and not is_right_nullable:
                return f"{left} {sql_op} {right}"

            clean_left = self._unwrap_str(left)
            clean_right = self._unwrap_str(right)

            left_cond = f"({clean_left})"
            right_cond = f"({clean_right})"

            coalesced_l = self._safe_coalesce_operand(left, is_left_nullable)
            coalesced_r = self._safe_coalesce_operand(right, is_right_nullable)

            return f"CASE WHEN {left_cond} IS NULL AND {right_cond} IS NULL THEN NULL ELSE {coalesced_l} {sql_op} {coalesced_r} END"

        if sql_op in ("=", "<>", "<", "<=", ">", ">="):
            if _needs_coalesce_zero(left):
                left = f"COALESCE({left}, 0)"
            if _needs_coalesce_zero(right):
                right = f"COALESCE({right}, 0)"
            return f"{left} {sql_op} {right}"

        if sql_op == "/":
            left = _wrap_parens_for_op(left)
            right_expr = right
            if right_expr.startswith("(") and right_expr.endswith(")"):
                depth = 0
                balanced = True
                for ch in right_expr[:-1]:
                    if ch == "(": depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            balanced = False
                            break
                if balanced:
                    right_expr = right_expr[1:-1]
            if right_expr.strip().upper() == "NULL":
                return f"{left} / NULL"
            if left.strip().upper() == "NULL":
                return "NULL"
            if "PARTITION BY EXCLUDING" in right_expr:
                if isinstance(node.left, MeasureRefNode):
                    left = f'{self.table_alias.upper()}."{sanitize_column(node.left.name)}"'
                elif isinstance(node.left, FunctionCallNode):
                    rendered_left = self._render_node(node.left)
                    for m_k, m_sql in self.measure_sql_map.items():
                        if m_sql.strip().upper() in (rendered_left.strip().upper(), f"({rendered_left.strip().upper()})"):
                            left = f'{self.table_alias.upper()}."{sanitize_column(m_k)}"'
                            break
            if self._is_nonzero_constant(node.right, right_expr) or right_expr.strip().upper().startswith("NULLIF("):
                return f"{left} / {right_expr}"
            return f"{left} / NULLIF({right_expr}, 0)"

        return f"{left} {sql_op} {right}"



    def _render_unary(self, node: UnaryOpNode) -> str:

        inner = self._render_node(node.operand)

        if node.op == "-":

            return f"(-{inner})"

        if node.op.upper() == "NOT":
            if isinstance(node.operand, FunctionCallNode) and node.operand.func.upper() == "ISBLANK":
                if len(node.operand.args) == 1:
                    arg_sql = self._render_node(node.operand.args[0])
                    return f"({arg_sql} IS NOT NULL)"
            return self._negate(f"({inner})")

        return inner



    def _render_function(self, node: FunctionCallNode) -> str:

        func = node.func.upper()



        if func in ("TRUE", "FALSE"):

            return func



        # CALCULATE (lazy/CASE-based evaluation of filters)

        if func == "CALCULATE":

            return self._render_calculate(node.args)



        # Time Intelligence (lazy OVER/window/LAG function rendering)

        if func in {"TOTALYTD", "TOTALMTD", "TOTALQTD"}:

            return self._render_period_to_date(func, node.args)



        if func in {"SAMEPERIODLASTYEAR", "PREVIOUSYEAR"}:

            return self._render_lag_period(node.args, interval="year", amount=-1, func_name=func)



        if func == "PREVIOUSMONTH":

            return self._render_lag_period(node.args, interval="month", amount=-1, func_name=func)



        if func == "PREVIOUSQUARTER":

            return self._render_lag_period(node.args, interval="quarter", amount=-1, func_name=func)



        if func == "DATEADD":

            return self._render_dateadd(node.args)



        # Iterators (SUMX, AVERAGEX, MINX, MAXX, COUNTAX)

        if func in self._ITERATOR_MAP:

            if len(node.args) < 2:

                raise self.DaxRenderError(f"Iterator {func} requires at least 2 arguments")

            sql_func = self._ITERATOR_MAP[func]

            table_arg = node.args[0]

            expr_arg = node.args[1]

            # Iteration grain check:
            # Table / FILTER(T) / KEEPFILTERS(T) -> row grain (unroll allowed).
            # VALUES / DISTINCT / SUMMARIZE / ALL(T[c]) -> group grain -> not one metric -> NULL
            chk_arg = table_arg
            while isinstance(chk_arg, FunctionCallNode) and chk_arg.func.upper() in ("KEEPFILTERS", "FILTER"):
                chk_arg = chk_arg.args[0] if chk_arg.args else None

            if isinstance(chk_arg, FunctionCallNode):
                fn_grain = chk_arg.func.upper()
                if fn_grain in ("VALUES", "DISTINCT", "SUMMARIZE", "SUMMARIZECOLUMNS"):
                    col_desc = ""
                    if chk_arg.args:
                        col_desc = getattr(chk_arg.args[0], "column", getattr(chk_arg.args[0], "name", str(chk_arg.args[0])))
                    raise self.DaxRenderError(
                        f"not applicable: {func} over {fn_grain}({col_desc or 'dimension values'}) evaluates its "
                        "expression once per distinct value (context transition) and then re-aggregates; "
                        "this multi-level aggregation is not expressible as a single semantic-view metric"
                    )
                if fn_grain in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS") and chk_arg.args:
                    if isinstance(chk_arg.args[0], ColumnRefNode) or "[" in str(chk_arg.args[0]):
                        col_desc = getattr(chk_arg.args[0], "column", str(chk_arg.args[0]))
                        # Special case: MAXX/MINX over ALL(T[c]) where expression is the column itself
                        # is a scalar MAX/MIN of the column, not a group aggregation.
                        is_same_col = False
                        if func in ("MINX", "MAXX") and isinstance(expr_arg, ColumnRefNode):
                            col_names = [getattr(a, "column", "").upper() for a in chk_arg.args]
                            if expr_arg.column.upper() in col_names:
                                is_same_col = True
                        if not is_same_col:
                            raise self.DaxRenderError(
                                f"BY_DESIGN_EXCLUDED: aggregation over groups of {col_desc} "
                                "requires multi-level grouping not supported in a single semantic metric"
                            )

            # Check if first arg is FILTER(table, predicate)
            if isinstance(table_arg, FunctionCallNode) and table_arg.func.upper() == "FILTER":
                if len(table_arg.args) < 2:
                    raise self.DaxRenderError("FILTER requires table and predicate arguments")
                filter_tbl_node = table_arg.args[0]
                filter_tbl_name = self._extract_table_name(filter_tbl_node) or self._extract_table_name(table_arg)
                with self._row_context(filter_tbl_name):
                    pred_sql = self._render_node(table_arg.args[1])
                    expr_sql = self._render_node(expr_arg)

                if func == "COUNTAX" and expr_sql in ("TRUE", "true", "True", "1"):
                    case_expr = f"CASE WHEN {pred_sql} THEN 1 END"
                else:
                    case_expr = f"CASE WHEN {pred_sql} THEN {expr_sql} END"
                cast = "::FLOAT" if sql_func in ("SUM", "AVG") else ""
                return f"{sql_func}({case_expr}{cast})"

            iter_tbl = self._extract_table_name(table_arg)
            with self._row_context(iter_tbl):
                expr_sql = self._render_node(expr_arg)

            if func == "COUNTAX" and expr_sql in ("TRUE", "true", "True", "1"):

                return "COUNT(*)"

            if func == "AVERAGEX" and expr_sql:

                cleaned = expr_sql.strip()

                while cleaned.startswith("(") and cleaned.endswith(")"):

                    depth = 0

                    encloses = False

                    for idx, ch in enumerate(cleaned):

                        if ch == "(":

                            depth += 1

                        elif ch == ")":

                            depth -= 1

                            if depth == 0:

                                encloses = (idx == len(cleaned) - 1)

                                break

                    if encloses:

                        cleaned = cleaned[1:-1].strip()

                    else:

                        break

                cleaned = re.sub(r"::FLOAT$", "", cleaned, flags=re.IGNORECASE).strip()

                sum_unroll = re.match(r"^SUM\s*\((.*)\)$", cleaned, re.IGNORECASE | re.DOTALL)

                if sum_unroll:

                    inner_row_expr = sum_unroll.group(1).strip()

                    inner_row_expr = re.sub(r"::FLOAT$", "", inner_row_expr, flags=re.IGNORECASE).strip()

                    return f"AVG({inner_row_expr})"

            cast = "::FLOAT" if sql_func in ("SUM", "AVG") else ""

            return f"{sql_func}({expr_sql}{cast})"



        if func == "KEEPFILTERS":
            if not node.args:
                raise self.DaxRenderError("KEEPFILTERS requires 1 argument")
            return self._render_node(node.args[0])
        if func == "USERELATIONSHIP":
            raise self.DaxRenderError(USERELATIONSHIP_REASON)
        if func == TABLE_CONSTRUCTOR_FUNC:
            raise self.DaxRenderError("not applicable: table constructor { ... } used as a value")
        if func == "COALESCE":
            if len(node.args) < 2:
                raise self.DaxRenderError("COALESCE requires at least 2 arguments")
            return "COALESCE(" + ", ".join(self._render_node(a) for a in node.args) + ")"
        if func == "RELATED":
            return self._render_related(node)
        if func == "COUNTBLANK":
            return self._render_countblank(node)



        if func == "NOT":

            if len(node.args) != 1:

                raise self.DaxRenderError("NOT requires 1 argument")

            inner = node.args[0]

            if isinstance(inner, FunctionCallNode) and inner.func.upper() == "ISBLANK":

                if len(inner.args) != 1:

                    raise self.DaxRenderError("ISBLANK requires 1 argument")

                arg_sql = self._render_node(inner.args[0])

                return f"({arg_sql} IS NOT NULL)"

            arg_sql = self._render_node(inner)
            return self._negate(f"({arg_sql})")



        if func == "DATEDIFF":

            if len(node.args) != 3:

                raise self.DaxRenderError("DATEDIFF requires 3 arguments (start_date, end_date, interval)")

            start_sql = self._render_node(node.args[0])

            end_sql = self._render_node(node.args[1])

            interval_node = node.args[2]

            interval_str = getattr(interval_node, "name", None) or getattr(interval_node, "raw", None) or str(interval_node)

            interval_clean = interval_str.strip("'\" ").upper()

            return f"DATEDIFF({interval_clean}, {start_sql}, {end_sql})"



        # Aggregations (SUM, AVERAGE, MIN, MAX, COUNT, etc.)

        if func in self._AGG_MAP:

            if func == "COUNTROWS":

                return self._render_countrows(node)

            if not node.args:

                raise self.DaxRenderError(f"Aggregation {func} requires at least 1 argument")

            return self._render_aggregation(func, node.args[0])



        # IF
        if func == "IF":
            return self._render_if(node.args)

        # Registry-driven templates (if configured)

        rendered_args = [self._render_node(a) for a in node.args]

        if func == "DIVIDE":

            return self._render_divide(node.args)



        registry_sql = self._func_registry.render(

            func,

            rendered_args,

            table_alias=self.table_alias,

            date_alias=self.date_alias,

        )

        if registry_sql:

            return registry_sql



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



        # VALUE -> TRY_TO_NUMBER

        if func == "VALUE":

            if not node.args:

                raise self.DaxRenderError("VALUE requires at least 1 argument")

            arg_sql = self._render_node(node.args[0])

            return f"TRY_TO_NUMBER({arg_sql})"



        # LEFT / RIGHT (optional length defaults to 1 in DAX)

        if func in ("LEFT", "RIGHT"):

            if not node.args:

                raise self.DaxRenderError(f"{func} requires at least 1 argument")

            s = self._render_node(node.args[0])

            n = self._render_node(node.args[1]) if len(node.args) > 1 else "1"

            return f"{func}({s}, {n})"



        # MID -> SUBSTR (1-based, same as Snowflake)

        if func == "MID":

            if len(node.args) < 3:

                raise self.DaxRenderError("MID requires 3 arguments")

            s = self._render_node(node.args[0])

            start = self._render_node(node.args[1])

            num = self._render_node(node.args[2])

            return f"SUBSTR({s}, {start}, {num})"



        # MROUND

        if func == "MROUND":

            if len(node.args) < 2:

                raise self.DaxRenderError("MROUND requires 2 arguments")

            x_sql = self._render_node(node.args[0])

            n_sql = self._render_node(node.args[1])

            return f"ROUND(({x_sql}) / ({n_sql})) * ({n_sql})"



        # ROUNDUP / ROUNDDOWN (negative-aware away-from-zero / towards-zero)

        if func in ("ROUNDUP", "ROUNDDOWN"):

            if not node.args:

                raise self.DaxRenderError(f"{func} requires at least 1 argument")

            x_sql = self._render_node(node.args[0])

            sql_fn = "CEIL" if func == "ROUNDUP" else "FLOOR"

            if len(node.args) >= 2:

                d_sql = self._render_node(node.args[1])

                return f"SIGN({x_sql}) * {sql_fn}(ABS({x_sql}), {d_sql})"

            return f"SIGN({x_sql}) * {sql_fn}(ABS({x_sql}))"



        # SELECTEDVALUE(col, [alt])

        if func == "SELECTEDVALUE":

            if not node.args:

                raise self.DaxRenderError("SELECTEDVALUE requires at least 1 argument")

            col_sql = self._render_node(node.args[0])

            alt_sql = self._render_node(node.args[1]) if len(node.args) > 1 else "NULL"

            return f"IFF(COUNT(DISTINCT {col_sql}) + MAX(IFF({col_sql} IS NULL, 1, 0)) = 1, MAX({col_sql}), {alt_sql})"



        # String and math functions with direct Snowflake equivalents

        if func in ("LASTDATE", "FIRSTDATE"):
            agg_name = "MAX" if func == "LASTDATE" else "MIN"
            if node.args:
                arg_sql = self._render_node(node.args[0])
                return f"{agg_name}({arg_sql})"

        _direct_sql = {

            "LEN": "LENGTH", "TRIM": "TRIM",

            "UPPER": "UPPER", "LOWER": "LOWER",

            "YEAR": "YEAR", "MONTH": "MONTH", "DAY": "DAY",

            "HOUR": "HOUR", "MINUTE": "MINUTE", "SECOND": "SECOND",

            "ABS": "ABS", "CEILING": "CEIL", "FLOOR": "FLOOR",

            "ROUND": "ROUND",

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



    def _render_countrows(self, node: FunctionCallNode) -> str:
        if not node.args:
            return "COUNT(*)"
        arg0 = node.args[0]
        # 1. IdentifierNode (table name): e.g. COUNTROWS('Orders')
        if isinstance(arg0, IdentifierNode):
            name = arg0.name.strip("'\"")
            if "[" in name or "." in name:
                raise self.DaxRenderError(f"non-table argument to COUNTROWS: '{arg0.name}'")
            return "COUNT(*)"

        if isinstance(arg0, FunctionCallNode):
            fn = arg0.func.upper()
            if fn == "KEEPFILTERS" and arg0.args:
                arg0 = arg0.args[0]
                fn = getattr(arg0, "func", "").upper() if isinstance(arg0, FunctionCallNode) else ""
                if isinstance(arg0, IdentifierNode):
                    return "COUNT(*)"

            # 2. FILTER(T, cond): COUNT(CASE WHEN <cond> THEN 1 END)
            if fn == "FILTER" and len(arg0.args) >= 2:
                iter_tbl = self._extract_table_name(arg0.args[0])
                with self._row_context(iter_tbl):
                    cond_sql = self._render_node(arg0.args[1])
                return f"COUNT(CASE WHEN {cond_sql} THEN 1 END)"

            # 3. VALUES(T[c]) or DISTINCT(T[c]): COUNT(DISTINCT c)
            if fn in ("VALUES", "DISTINCT") and arg0.args:
                col_arg = arg0.args[0]
                if isinstance(col_arg, ColumnRefNode):
                    col_sql = self._render_column_ref(col_arg)
                    return f"COUNT(DISTINCT {col_sql})"
                col_sql = self._render_node(col_arg)
                return f"COUNT(DISTINCT {col_sql})"

            if fn in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS"):
                if not arg0.args:
                    return "COUNT(*)"
                first = arg0.args[0]
                if isinstance(first, IdentifierNode):
                    return "COUNT(*)"
                elif isinstance(first, ColumnRefNode):
                    col_sql = self._render_column_ref(first)
                    return f"COUNT(DISTINCT {col_sql})"

        raise self.DaxRenderError(
            f"non-table argument to COUNTROWS: cannot count rows of {type(arg0).__name__}"
        )

    def _render_aggregation(self, func: str, arg: DaxNode) -> str:
        # Fan-out check: Never aggregate a dimension column with SUM/AVG/COUNT on the fact table.
        # Must not fire for a table's own columns, measure-only tables, or COUNT(DISTINCT)/MIN/MAX.
        if func.upper() in ("SUM", "AVG", "AVERAGE", "AVERAGEX", "COUNT", "COUNTA"):
            is_measure_table = (self.table_alias or "").upper() in ("METRICS", "MEASURES", "_MEASURES", "KEY_MEASURES", "ALL_MEASURES")
            if not is_measure_table:
                dim_table = None
                dim_col = None
                if isinstance(arg, ColumnRefNode):
                    t = (arg.table or "").strip("'\"")
                    if t and t.casefold() != (self.table_alias or "").casefold():
                        dim_table = t
                        dim_col = arg.column
                    elif not t and arg.column:
                        for rel in (self.relationships or []):
                            to_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "to_table", None)
                            from_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "from_table", None)
                            if to_tbl and from_tbl and from_tbl.casefold() == (self.table_alias or "").casefold():
                                prefix = f"{to_tbl}_".casefold()
                                if arg.column.casefold().startswith(prefix):
                                    dim_table = to_tbl
                                    dim_col = arg.column[len(prefix):]
                                    break

                if dim_table and dim_col:
                    is_dim = False
                    for rel in (self.relationships or []):
                        to_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "to_table", None)
                        from_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "from_table", None)
                        if to_tbl and from_tbl:
                            if to_tbl.casefold() == dim_table.casefold() and from_tbl.casefold() == (self.table_alias or "").casefold():
                                is_dim = True
                                break
                    if is_dim:
                        raise self.DaxRenderError(
                            f"BY_DESIGN_EXCLUDED: aggregating dimension column '{dim_table}[{dim_col}]' "
                            f"with {func.upper()} on fact table '{self.table_alias}' causes fan-out duplication "
                            "(measure should aggregate on dimension table directly)"
                        )

        col_sql = self._render_node(arg)

        template = self._AGG_MAP[func]

        

        # Snowflake: cast to FLOAT for SUM/AVG to handle BOOLEAN columns safely

        cast = "::FLOAT" if func in ("SUM", "AVERAGE", "AVERAGEX", "AVG") else ""

        

        if "{col}" in template:

            return template.format(col=f"{col_sql}{cast}")

        return f"{template}({col_sql}{cast})"



    def _is_nonzero_constant(self, node: Optional[DaxNode], rendered_sql: str) -> bool:
        if node is not None and isinstance(node, LiteralNode):
            try:
                val = float(str(node.value).strip())
                return val != 0.0
            except (ValueError, TypeError):
                pass
        s = (rendered_sql or "").strip()
        while s.startswith("(") and s.endswith(")"):
            s = s[1:-1].strip()
        try:
            val = float(s)
            return val != 0.0
        except (ValueError, TypeError):
            return False

    def _render_divide(self, args: List[DaxNode]) -> str:
        if len(args) < 2:
            raise self.DaxRenderError("DIVIDE requires at least 2 arguments")
        den = self._render_node(args[1])
        den_expr = f"({den})" if isinstance(args[1], BinaryOpNode) and args[1].op in ("+", "-") else den
        num = self._render_node(args[0])
        num_expr = f"({num})" if isinstance(args[0], BinaryOpNode) and args[0].op in ("+", "-") else num
        if "PARTITION BY EXCLUDING" in den_expr:
            if isinstance(args[0], MeasureRefNode):
                m_alias = sanitize_column(args[0].name)
                num_expr = f'{self.table_alias.upper()}."{m_alias}"'
            elif isinstance(args[0], FunctionCallNode):
                rendered_num = self._render_node(args[0])
                matched_m = None
                for m_k, m_sql in self.measure_sql_map.items():
                    if m_sql.strip().upper() in (rendered_num.strip().upper(), f"({rendered_num.strip().upper()})"):
                        matched_m = m_k
                        break
                if matched_m:
                    m_alias = sanitize_column(matched_m)
                    num_expr = f'{self.table_alias.upper()}."{m_alias}"'
                else:
                    num_expr = rendered_num
            else:
                num = self._render_node(args[0])
                num_expr = f"({num})" if isinstance(args[0], BinaryOpNode) and args[0].op in ("+", "-") else num
        else:
            num = self._render_node(args[0])
            num_expr = f"({num})" if isinstance(args[0], BinaryOpNode) and args[0].op in ("+", "-") else num
        num_expr = _wrap_parens_for_op(num_expr)
        den_expr = _wrap_parens_for_op(den_expr)
        if self._is_nonzero_constant(args[1], den_expr):
            return f"{num_expr} / {den_expr}"
        if len(args) > 2:
            alt_node = args[2]
            if isinstance(alt_node, FunctionCallNode) and alt_node.func.upper() == "BLANK":
                return f"{num_expr} / NULLIF({den_expr}, 0)"
            alt = self._render_node(alt_node)
            if alt.upper() in ("NULL", "NONE", "", "BLANK()"):
                return f"{num_expr} / NULLIF({den_expr}, 0)"
            return f"IFF(COALESCE({den_expr}, 0) = 0, {alt}, {num_expr} / {den_expr})"
        return f"{num_expr} / NULLIF({den_expr}, 0)"

    def _render_if(self, args: List[DaxNode]) -> str:

        if len(args) < 2:

            raise self.DaxRenderError("IF requires at least 2 arguments")

        cond_node = args[0]

        # Quick-measure error guard unwrap:
        # IF(..., ERROR(...), <calc>) -> <calc>
        # IF(..., <calc>, ERROR(...)) -> <calc>
        if len(args) >= 3:
            if isinstance(args[1], FunctionCallNode) and args[1].func.upper() == "ERROR":
                return self._render_node(args[2])
            if isinstance(args[2], FunctionCallNode) and args[2].func.upper() == "ERROR":
                return self._render_node(args[1])

        # Template helper guard unwrap: IF([_ShowValueForDates], <calc>) -> <calc>
        if isinstance(cond_node, MeasureRefNode):
            clean_m = cond_node.name.strip("[]").lower()
            if clean_m in ("_showvaluefordates", "showvaluefordates"):
                return self._render_node(args[1])

        # HASONEVALUE guard: IF(HASONEVALUE(col), calc[, alt]) returns calc only
        # where exactly one value of col is visible (and BLANK/alt at totals).
        # Mapping it to NON ADDITIVE BY col changes total/subtotal values, so
        # it is declined (see HASONEVALUE_GUARD_REASON).
        if isinstance(cond_node, FunctionCallNode) and cond_node.func.upper() in ("HASONEVALUE", "ISINSCOPE", "ISFILTERED", "HASONEFILTER"):
            raise self.DaxRenderError(HASONEVALUE_GUARD_REASON)

        cond = self._render_node(cond_node)

        if self._is_numeric_truthiness_condition(cond_node):

            cond = f"(({cond}) IS NOT NULL AND ({cond}) <> 0)"

        then_node = args[1]
        else_node = args[2] if len(args) > 2 else None

        # Rule A: IF(cond, <numeric>, "") or IF(cond, <numeric>, BLANK()) -> CASE WHEN cond THEN <numeric> END
        if _is_empty_branch(else_node):
            true_val = self._render_node(then_node)
            return f"CASE WHEN {cond} THEN {true_val} END"

        if _is_empty_branch(then_node) and else_node is not None:
            false_val = self._render_node(else_node)
            return f"CASE WHEN {self._negate(f'({cond})')} THEN {false_val} END"

        true_val = self._render_node(then_node)

        false_val = self._render_node(else_node) if else_node is not None else "NULL"

        return f"CASE WHEN {cond} THEN {true_val} ELSE {false_val} END"



    def _is_numeric_truthiness_condition(self, node: DaxNode) -> bool:

        """True when `node` is something this renderer turns into a numeric

        SQL expression rather than an inherently boolean one (see

        _NUMERIC_TRUTHINESS_FUNCS). Deliberately conservative: only

        recognizes function calls, arithmetic, and measure references this

        renderer itself is responsible for turning numeric, so a bare

        column reference — which may already be a genuine BOOLEAN column in

        the model — is left untouched rather than risk wrapping something

        Snowflake would then reject the other way (BOOLEAN compared to 0).



        A MeasureRefNode counts only when it resolves through

        measure_sql_map to another measure's own (numeric, aggregate) SQL —

        e.g. IF([Gross Margin SPLY], [YoY GM Var]/[Gross Margin SPLY],

        BLANK()), the common YoY/SPLY "skip if no prior-period data" guard,

        where [Gross Margin SPLY] is itself CALCULATE(SUM(...),

        SAMEPERIODLASTYEAR(...)). An unresolved reference falls through to

        _render_measure_ref's own bare-column fallback, which may be a

        genuine boolean column — left alone here for the same reason as any

        other ColumnRefNode.

        """

        if isinstance(node, FunctionCallNode):

            return node.func.upper() in self._NUMERIC_TRUTHINESS_FUNCS

        if isinstance(node, BinaryOpNode):

            return node.op in ("+", "-", "*", "/")

        if isinstance(node, MeasureRefNode):

            return node.name.casefold() in self._measure_sql_map_by_casefold

        return False



    def _render_iferror(self, args: List[DaxNode]) -> str:

        if len(args) < 2:

            raise self.DaxRenderError("IFERROR requires 2 arguments")

        expr = self._render_node(args[0])

        fallback = self._render_node(args[1])

        return f"IFF(TRY_TO_NUMBER(TO_VARCHAR({expr})) IS NULL, {fallback}, {expr})"



    def _render_switch(self, args: List[DaxNode]) -> str:

        if len(args) < 3:

            raise self.DaxRenderError("SWITCH requires at least 3 arguments")

        selector = args[0]
        is_searched = (
            (isinstance(selector, LiteralNode) and selector.value is True)
            or (isinstance(selector, FunctionCallNode) and selector.func.upper() == "TRUE" and not selector.args)
        )
        cases = []
        i = 1
        while i + 1 < len(args):
            if is_searched:
                # SWITCH(TRUE(), cond1, v1, ...) -> searched CASE WHEN cond1 THEN v1
                cond = self._render_node(args[i])
                if self._is_numeric_truthiness_condition(args[i]):
                    cond = f"(({cond}) IS NOT NULL AND ({cond}) <> 0)"
            else:
                cond = self._render_comparison(BinaryOpNode(op="=", left=selector, right=args[i]))
            result = self._render_node(args[i + 1])
            cases.append(f"WHEN {cond} THEN {result}")
            i += 2

        else_clause = f"ELSE {self._render_node(args[-1])}" if len(args) % 2 == 0 else "ELSE NULL"

        return "CASE " + " ".join(cases) + f" {else_clause} END"



    def _rewrite_filtered_aggregate(self, agg_node: FunctionCallNode, condition_sql: str) -> str:

        func = agg_node.func.upper()

        if func not in self._AGG_MAP and func not in self._ITERATOR_MAP:

            raise self.DaxRenderError(f"Function {func} is not a recognized direct aggregation")



        if self._AGG_CALL_START.search(condition_sql):

            raise self.DaxRenderError(

                f"Filter condition contains an aggregate function, which cannot be nested inside {func}: {condition_sql}"

            )

        

        if func == "COUNTROWS":
            if condition_sql and condition_sql.strip() not in ("1=1", "TRUE", "1 = 1", "(1=1)", "(TRUE)"):
                return f"COUNT(CASE WHEN {condition_sql} THEN 1 END)"
            return "COUNT(*)"



        if func in self._ITERATOR_MAP:

            sql_func = self._ITERATOR_MAP[func]

            if len(agg_node.args) < 2:

                raise self.DaxRenderError(f"Iterator {func} requires at least 2 arguments")

            expr_sql = self._render_node(agg_node.args[1])

            if func == "COUNTAX" and expr_sql in ("TRUE", "true", "True", "1"):

                case_expr = f"CASE WHEN {condition_sql} THEN 1 END"

            else:

                case_expr = f"CASE WHEN {condition_sql} THEN {expr_sql} END"

            cast = "::FLOAT" if sql_func in ("SUM", "AVG") else ""

            return f"{sql_func}({case_expr}{cast})"

            

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

        clean = self._unwrap_str(rendered_sql or "")
        if not clean or self._AGG_CALL_START.search(condition_sql):
            return None
        if re.search(r"\bOVER\s*\(", clean, re.IGNORECASE):
            return None  # window metric -- filtering inside it changes its meaning



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



        # Any column reference OUTSIDE an aggregate call is a citation of
        # another metric (or a precomputed helper column) -- it would stay
        # unfiltered, so the rewrite would be wrong. Fail closed.
        outside = clean
        for start, end, _f, _a in sorted(calls, key=lambda c: c[0], reverse=True):
            outside = outside[:start] + " 0 " + outside[end + 1:]
        outside = re.sub(r"'(?:''|[^'])*'", "''", outside)
        if re.search(r"\b[A-Za-z_][A-Za-z0-9_$]*\s*\.\s*(?:\"|[A-Za-z_])", outside):
            return None
        rewritten = clean
        for start, end, func, arg in sorted(calls, key=lambda c: c[0], reverse=True):
            if func == "COUNT" and re.match(r"^DISTINCT\b", arg, re.IGNORECASE):
                inner = arg[len("DISTINCT"):].strip()
                replacement = f"COUNT(DISTINCT CASE WHEN {condition_sql} THEN {inner} END)"
                rewritten = rewritten[:start] + replacement + rewritten[end + 1 :]
                continue

            if arg == "*":

                key_col = None

                if self.table_key_map and self.primary_table_name:

                    key_col = self.table_key_map.get(self.primary_table_name.strip("'").casefold())

                if key_col:

                    san_col = sanitize_column(key_col)

                    col_ref = f"{self.table_alias}.{san_col}" if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", san_col) else f'{self.table_alias}."{san_col}"'

                    replacement = f"{func}(CASE WHEN {condition_sql} THEN {col_ref} END)"

                else:

                    replacement = f"{func}(CASE WHEN {condition_sql} THEN 1 END)"

            else:

                replacement = f"{func}(CASE WHEN {condition_sql} THEN {arg} ELSE NULL END)"

            rewritten = rewritten[:start] + replacement + rewritten[end + 1 :]



        return rewritten



    def _extract_closing_balance_dimension(self, node: DaxNode) -> Optional[Tuple[Optional[str], str]]:
        """Detect closing-balance / last-period filter patterns:
          FILTER(ALL(table[col]), col = MAX(col))
          FILTER(table, col = MAX(col))
          col = MAX(col)
          LASTNONBLANK(table[col], ...)
          LASTNONBLANK(ALL(table[col]), ...)
        Returns (table_name, column_name) if matched, else None.
        """
        if isinstance(node, IdentifierNode):
            var_name = node.name.strip("[]").casefold()
            if hasattr(self, "_var_ast_map") and var_name in self._var_ast_map:
                node = self._var_ast_map[var_name]

        if isinstance(node, FunctionCallNode) and node.func.upper() == "LASTNONBLANK" and len(node.args) >= 1:
            first_arg = node.args[0]
            if isinstance(first_arg, FunctionCallNode) and first_arg.func.upper() in ("ALL", "ALLEXCEPT", "VALUES") and first_arg.args:
                first_arg = first_arg.args[0]
            if isinstance(first_arg, ColumnRefNode):
                return (first_arg.table, first_arg.column)
            if isinstance(first_arg, IdentifierNode):
                return (None, first_arg.name)

        pred_node = node
        if isinstance(node, FunctionCallNode) and node.func.upper() == "FILTER" and len(node.args) >= 2:
            pred_node = node.args[1]

        if isinstance(pred_node, BinaryOpNode) and pred_node.op in ("=", "=="):
            dim = self._match_col_equals_max(pred_node.left, pred_node.right) or self._match_col_equals_max(pred_node.right, pred_node.left)
            if dim:
                return dim

        return None



    def _match_col_equals_max(self, col_node: DaxNode, max_node: DaxNode) -> Optional[Tuple[Optional[str], str]]:

        if isinstance(col_node, ColumnRefNode) and isinstance(max_node, FunctionCallNode):

            if max_node.func.upper() == "MAX" and len(max_node.args) == 1:

                arg = max_node.args[0]

                if isinstance(arg, ColumnRefNode):

                    c1 = col_node.column.strip("[]'\"").casefold()

                    c2 = arg.column.strip("[]'\"").casefold()

                    if c1 == c2:

                        t1 = col_node.table.strip("[]'\"").casefold() if col_node.table else None

                        t2 = arg.table.strip("[]'\"").casefold() if arg.table else None

                        if t1 is None or t2 is None or t1 == t2:

                            return (col_node.table or arg.table, col_node.column)

        return None




    def _extract_agg_column_and_func(self, node: DaxNode) -> Optional[Tuple[str, Optional[str], str]]:
        if isinstance(node, FunctionCallNode):
            func = node.func.upper()
            if func == "COUNTROWS":
                return ("COUNT", None, "*")
            if func in ("SUM", "AVG", "AVERAGE", "COUNT", "MIN", "MAX"):
                if node.args:
                    arg = node.args[0]
                    if isinstance(arg, ColumnRefNode):
                        clean_col = (arg.column or "").strip("[]")
                        return (func, arg.table, clean_col)
                    elif isinstance(arg, MeasureRefNode):
                        return self._resolve_measure_agg_info(arg.name)
        elif isinstance(node, MeasureRefNode):
            return self._resolve_measure_agg_info(node.name)
        return None

    def _resolve_measure_agg_info(self, measure_name: str) -> Optional[Tuple[str, Optional[str], str]]:
        sql = self.measure_sql_map.get(measure_name) or self._measure_sql_map_by_casefold.get(measure_name.casefold())
        if sql:
            if re.search(r'\bCOUNT\s*\(\s*(\*|1)\s*\)', sql, re.IGNORECASE):
                return ("COUNT", None, "*")
            m = re.search(r'\b(SUM|AVG|COUNT|MIN|MAX)\s*\(\s*(?:([a-zA-Z0-9_]+)\.)?\"?([a-zA-Z0-9_]+)\"?\s*\)', sql, re.IGNORECASE)
            if m:
                return (m.group(1).upper(), m.group(2), m.group(3))
        return None

    def _get_emitted_dimensions_for_table(self, tbl_name: str, alias: str) -> List[str]:
        dims: List[str] = []
        if self.datasets:
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                if ds_name.casefold() == tbl_name.casefold():
                    cols = getattr(ds, "columns", []) or []
                    for col_item in cols:
                        c_name = getattr(col_item, "unique_name", getattr(col_item, "name", ""))
                        if c_name and not c_name.upper().startswith(("ALL_", "__SB_")):
                            safe_col = sanitize_column(c_name)
                            dim_ref = f'{alias}."{safe_col}"'
                            if dim_ref not in dims:
                                dims.append(dim_ref)
                    if dims:
                        return dims
        if getattr(self, "dataset_col_lookup", None):
            for ds_k, cols in self.dataset_col_lookup.items():
                if ds_k.casefold() == tbl_name.casefold():
                    for c_name in sorted(cols):
                        if c_name and not str(c_name).upper().startswith(("ALL_", "__SB_")):
                            safe_col = sanitize_column(c_name)
                            dim_ref = f'{alias}."{safe_col}"'
                            if dim_ref not in dims:
                                dims.append(dim_ref)
                    if dims:
                        return dims
        return dims

    def _get_minimal_groupable_dimensions(self, tbl_name: str, alias: str) -> List[str]:
        candidate_cols: List[str] = []
        if self.datasets:
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                if ds_name.casefold() == tbl_name.casefold():
                    cols = getattr(ds, "columns", []) or []
                    for col_item in cols:
                        c_name = getattr(col_item, "unique_name", getattr(col_item, "name", ""))
                        if c_name:
                            candidate_cols.append(c_name)
                    if candidate_cols:
                        break
        if not candidate_cols and getattr(self, "dataset_col_lookup", None):
            for ds_k, cols in self.dataset_col_lookup.items():
                if ds_k.casefold() == tbl_name.casefold():
                    candidate_cols = list(cols)
                    break

        rel_cols = set()
        if self.relationships:
            for rel in self.relationships:
                f_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "fromTable", "")
                t_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "toTable", "")
                if f_tbl and str(f_tbl).casefold() == tbl_name.casefold():
                    for c in (getattr(rel, "from_columns", None) or [getattr(rel, "fromColumn", None)]):
                        if c: rel_cols.add(re.sub(r"[^A-Z0-9]", "", str(c).upper()))
                if t_tbl and str(t_tbl).casefold() == tbl_name.casefold():
                    for c in (getattr(rel, "to_columns", None) or [getattr(rel, "toColumn", None)]):
                        if c: rel_cols.add(re.sub(r"[^A-Z0-9]", "", str(c).upper()))

        NUMERIC_TYPES = frozenset({"INTEGER", "INT", "BIGINT", "SMALLINT", "TINYINT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL", "NUMBER"})
        pk_cols = set()
        col_type_map = {}
        if self.datasets:
            for ds in self.datasets:
                ds_name = getattr(ds, "unique_name", getattr(ds, "name", ""))
                if ds_name.casefold() == tbl_name.casefold():
                    pks = getattr(ds, "primary_key", []) or []
                    if isinstance(pks, (str, bytes)): pks = [pks]
                    for pk in pks:
                        pk_cols.add(re.sub(r"[^A-Z0-9]", "", str(pk).upper()))
                    for col_item in getattr(ds, "columns", []) or []:
                        c_name = getattr(col_item, "unique_name", getattr(col_item, "name", ""))
                        if c_name:
                            c_norm = re.sub(r"[^A-Z0-9]", "", c_name.upper())
                            if getattr(col_item, "is_key", False) or getattr(col_item, "is_pk", False) or getattr(col_item, "is_unique", False):
                                pk_cols.add(c_norm)
                            dt = getattr(col_item, "data_type", None) or getattr(col_item, "type", None)
                            dt_str = str(dt.value if hasattr(dt, "value") else dt or "").upper()
                            col_type_map[c_norm] = dt_str
                    break

        report_layout_cols = set()
        if self.model_metadata:
            pres_meta = getattr(self.model_metadata, "presentation_metadata", None) or (self.model_metadata.get("presentation_metadata") if isinstance(self.model_metadata, dict) else None) or []
            for item in pres_meta:
                if isinstance(item, dict):
                    f_tbl = item.get("table")
                    if not f_tbl or str(f_tbl).casefold() == tbl_name.casefold():
                        f_col = item.get("field")
                        if f_col:
                            report_layout_cols.add(re.sub(r"[^A-Z0-9]", "", str(f_col).upper()))

        clean_candidates: List[str] = []
        for c in candidate_cols:
            cu = c.upper()
            if cu.startswith(("ALL_", "__SB_", "ROWNUMBER", "_")):
                continue
            norm_c = re.sub(r"[^A-Z0-9]", "", cu)
            if norm_c in rel_cols and len(candidate_cols) > 1:
                continue
            if norm_c in pk_cols and len(candidate_cols) > 1:
                continue
            dt_upper = col_type_map.get(norm_c, "")
            if dt_upper in NUMERIC_TYPES or any(num_t in dt_upper for num_t in ("INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "REAL", "NUMBER")):
                continue
            clean_candidates.append(c)

        if not clean_candidates:
            non_rel_non_pk = [c for c in candidate_cols if re.sub(r"[^A-Z0-9]", "", c.upper()) not in rel_cols and re.sub(r"[^A-Z0-9]", "", c.upper()) not in pk_cols]
            clean_candidates = non_rel_non_pk if non_rel_non_pk else candidate_cols
            if not clean_candidates:
                return []

        def rank_col(col_name: str) -> int:
            norm_c = re.sub(r"[^A-Z0-9]", "", col_name.upper())
            if norm_c in report_layout_cols:
                return 0
            if getattr(self, "current_measure_name", None):
                m_norm = re.sub(r"[^A-Z0-9]", "", self.current_measure_name.upper())
                if norm_c in m_norm and len(norm_c) >= 3:
                    return 1
            tbl_norm = re.sub(r"[^A-Z0-9]", "", tbl_name.upper().rstrip("S"))
            if norm_c == tbl_norm:
                return 2
            dt_upper = col_type_map.get(norm_c, "")
            if dt_upper in ("STRING", "TEXT", "VARCHAR", "CHAR") or any(txt_t in dt_upper for txt_t in ("STRING", "TEXT", "CHAR")):
                return 3
            return 4

        clean_candidates.sort(key=rank_col)
        best_col = clean_candidates[0]
        safe_col = sanitize_column(best_col)
        return [f'{alias}."{safe_col}"']

    def _has_nested_window(self, sql_expr: str, visited: Optional[Set[str]] = None) -> bool:
        if not sql_expr:
            return False
        if "OVER (" in sql_expr.upper() or "OVER(" in sql_expr.upper():
            return True
        if visited is None:
            visited = set()
        norm_map = {re.sub(r'[^A-Za-z0-9]', '', k.upper()): v for k, v in self.measure_sql_map.items()} if self.measure_sql_map else {}
        for ref in re.findall(r'"([A-Za-z0-9_]+)"', sql_expr):
            norm_ref = re.sub(r'[^A-Za-z0-9]', '', ref.upper())
            if norm_ref in visited:
                continue
            visited.add(norm_ref)
            ref_sql = norm_map.get(norm_ref)
            if ref_sql and self._has_nested_window(ref_sql, visited):
                return True
        return False

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

        if len(args) >= 2:
            from semabridge.converter.whole_table_all import match_whole_table_all_ast
            calc_node = FunctionCallNode(func="CALCULATE", args=args)
            pat = match_whole_table_all_ast(calc_node, fact_table=self.primary_table_name or self.table_alias)
            if pat:
                # Leave-One-Out (LOO) total expression substitution:
                # When CALCULATE(..., ALL(FactTable)) is evaluated inside a leave-one-out context (e.g. CSAT Impact),
                # directly use the precomputed total calculation expression rather than referencing an external helper column.
                loo = getattr(self, "_current_loo_info", None)
                if loo:
                    filter_arg = args[1]
                    if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
                        if filter_arg.args:
                            arg0 = filter_arg.args[0]
                            tbl = getattr(arg0, "name", getattr(arg0, "table", str(arg0))).strip("'\"")
                            fact_alias = (self.table_alias or "").upper()
                            fact_tbl = (self.primary_table_name or self.table_alias or "").upper()
                            if tbl.upper() in (fact_alias, fact_tbl) or (self.table_aliases and self.table_aliases.get(tbl, tbl).upper() == fact_alias):
                                if loo.get("inner_fn") in ("AVERAGE", "AVERAGEX"):
                                    return f'({loo["sum_total"]} / NULLIF({loo["cnt_total"]}, 0))'
                                elif loo.get("inner_fn") in ("COUNT", "COUNTROWS", "COUNTA"):
                                    return f'{loo["cnt_total"]}'
                                else:
                                    return f'{loo["sum_total"]}'
                tgt_alias = (self.table_alias or self.primary_table_name or "f").upper()
                return f'MAX({tgt_alias}."{pat.helper_name}")'

        userel_overrides = {}
        filter_args = args[1:]
        filtered_args = [args[0]]
        for filter_arg in args[1:]:
            if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() == "USERELATIONSHIP":
                # The semantic view always joins through the active
                # relationship; outer filters/groupings on the role-playing
                # dimension would keep flowing through it, so no CASE
                # rewrite reproduces DAX here.
                raise self.DaxRenderError(USERELATIONSHIP_REASON)
            filtered_args.append(filter_arg)

        prev_userel = getattr(self, "_userel_overrides", {})
        self._userel_overrides = {**prev_userel, **userel_overrides}

        try:
            agg_expr = self._render_node(filtered_args[0])

            filter_clauses: List[str] = []

            partition_cols: List[str] = []

            is_window = False

            # If base is a measure reference, check if it brings a non-additive dimension

            if isinstance(filtered_args[0], MeasureRefNode):

                m_name = filtered_args[0].name

                for k, v in self.measure_non_additive_map.items():

                    if k.casefold() == m_name.casefold():

                        if v and v not in self.non_additive_dimensions:

                            self.non_additive_dimensions.append(v)

                        break

            # Flatten CALCULATETABLE arguments into filter list
            expanded_filters: List[DaxNode] = []
            for f in filtered_args[1:]:
                if isinstance(f, FunctionCallNode) and f.func.upper() == "CALCULATETABLE":
                    expanded_filters.extend(f.args)
                else:
                    expanded_filters.append(f)

            # Time-intelligence modifiers (DATESYTD/.../SAMEPERIODLASTYEAR/
            # PREVIOUSx/DATEADD/PARALLELPERIOD). Every OTHER filter argument
            # is ANDed into the same window -- never dropped.
            ti_mods = []
            for filter_arg in expanded_filters:
                if isinstance(filter_arg, FunctionCallNode):
                    fname = filter_arg.func.upper()
                    if fname in ("DATESYTD", "DATESQTD", "DATESMTD", "TOTALYTD", "TOTALQTD", "TOTALMTD",
                                 "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER"):
                        ti_mods.append(filter_arg)
                    elif fname in ("DATEADD", "PARALLELPERIOD") and len(filter_arg.args) >= 3:
                        ti_mods.append(filter_arg)
            if len(ti_mods) > 1:
                raise self.DaxRenderError(
                    "not applicable: more than one time-intelligence filter in a single CALCULATE"
                )
            if ti_mods:
                ti = ti_mods[0]
                extra_preds: List[str] = []
                for other in expanded_filters:
                    if other is ti:
                        continue
                    if self._extract_closing_balance_dimension(other):
                        raise self.DaxRenderError(
                            "not applicable: time-intelligence filter combined with a closing-balance filter"
                        )
                    if isinstance(other, FunctionCallNode) and other.func.upper() in self._ALL_LIKE_FUNCS:
                        raise self.DaxRenderError(
                            "not applicable: time-intelligence filter combined with ALL/ALLEXCEPT/REMOVEFILTERS"
                        )
                    extra_preds.append(self._render_calc_filter_predicate(other))
                fname = ti.func.upper()
                date_arg = ti.args[0] if ti.args else None
                if date_arg is None:
                    raise self.DaxRenderError(f"{ti.func} requires a date-column argument")
                if fname in ("DATESYTD", "DATESQTD", "DATESMTD", "TOTALYTD", "TOTALQTD", "TOTALMTD"):
                    ti_sql = self._render_period_to_date(
                        fname, [filtered_args[0], date_arg], year_end_node=period_to_date_year_end_node(ti)
                    )
                else:
                    lag_interval, lag_amount = "year", -1
                    if fname in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR"):
                        lag_interval = "year"
                    elif fname == "PREVIOUSQUARTER":
                        lag_interval = "quarter"
                    elif fname == "PREVIOUSMONTH":
                        lag_interval = "month"
                    else:
                        lag_amount = self._extract_shift_amount(ti.args[1])
                        intv_raw = str(getattr(ti.args[2], "name", getattr(ti.args[2], "value", "year"))).lower()
                        if "year" in intv_raw:
                            lag_interval = "year"
                        elif "quarter" in intv_raw:
                            lag_interval = "quarter"
                        elif "month" in intv_raw:
                            lag_interval = "month"
                        elif "day" in intv_raw:
                            lag_interval = "day"
                        else:
                            raise self.DaxRenderError(f"Unsupported DATEADD interval '{intv_raw}'")
                    self._in_shifted_calculate += 1
                    try:
                        ti_sql = self._render_lag_period(
                            [filtered_args[0], date_arg], interval=lag_interval, amount=lag_amount, func_name=fname
                        )
                    finally:
                        self._in_shifted_calculate -= 1
                if not extra_preds:
                    return ti_sql
                where = " AND ".join(f"({p})" for p in extra_preds)
                combined = self._inject_case_filter_into_rendered_aggregate(ti_sql, where)
                if not combined:
                    raise self.DaxRenderError(
                        "not applicable: could not combine the time-intelligence window with the other CALCULATE filters"
                    )
                return combined
            for filter_arg in expanded_filters:

                # Closing-balance pattern check: handled via NON ADDITIVE BY instead of row-level predicate

                cb_dim = self._extract_closing_balance_dimension(filter_arg)

                if cb_dim:

                    if cb_dim not in self.non_additive_dimensions:

                        self.non_additive_dimensions.append(cb_dim)

                    continue

                if isinstance(filter_arg, FunctionCallNode):

                    fname = filter_arg.func.upper()

                    if fname in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):

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

                            f_tbl = filter_arg.args[0]
                            f_pred = filter_arg.args[1]
                            
                            # Generic Leave-One-Out (LOO) or Whole-table filtered aggregate:
                            # FILTER(ALL(<own fact>), ...)
                            if (isinstance(f_tbl, FunctionCallNode) and 
                                f_tbl.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS")):
                                f_tbl_arg = f_tbl.args[0] if f_tbl.args else None
                                f_tbl_raw = getattr(f_tbl_arg, "name", None) or getattr(f_tbl_arg, "table", None) or (str(f_tbl_arg) if f_tbl_arg else "")
                                f_tbl_name = f_tbl_raw.strip("'\"") if f_tbl_raw else (self.primary_table_name or self.table_alias or "")
                                fact_name = (self.primary_table_name or self.table_alias or "").strip("'\"")
                                is_own_fact = (f_tbl_name.upper() == fact_name.upper() or 
                                               self.table_aliases.get(f_tbl_name, f_tbl_name).strip("'\"").upper() == fact_name.upper())
                                
                                if is_own_fact and isinstance(f_pred, BinaryOpNode) and f_pred.op in ("<>", "!="):
                                    col_node, sv_node = None, None
                                    if isinstance(f_pred.left, ColumnRefNode) and isinstance(f_pred.right, FunctionCallNode) and f_pred.right.func.upper() == "SELECTEDVALUE":
                                        col_node, sv_node = f_pred.left, f_pred.right
                                    elif isinstance(f_pred.right, ColumnRefNode) and isinstance(f_pred.left, FunctionCallNode) and f_pred.left.func.upper() == "SELECTEDVALUE":
                                        col_node, sv_node = f_pred.right, f_pred.left
                                    
                                    if col_node and sv_node and sv_node.args and isinstance(sv_node.args[0], ColumnRefNode):
                                        c1 = sanitize_column(col_node.column).upper()
                                        c2 = sanitize_column(sv_node.args[0].column).upper()
                                        if c1 == c2:
                                            safe_col = c1
                                            fact_alias = self.table_alias.upper()
                                            dim_ref = f'{fact_alias}."{safe_col}"'
                                            base_arg = filtered_args[0]
                                            inner_fn = getattr(base_arg, "func", "").upper() if isinstance(base_arg, FunctionCallNode) else ""
                                            if inner_fn in ("MIN", "MAX", "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK"):
                                                raise self.DaxRenderError("leave-one-out not decomposable")
                                            
                                            agg_col_node = base_arg.args[0] if (isinstance(base_arg, FunctionCallNode) and base_arg.args) else None
                                            agg_col_name = getattr(agg_col_node, "column", None) or getattr(agg_col_node, "name", None) or "AMOUNT"
                                            safe_agg_col = sanitize_column(agg_col_name).upper()

                                            sum_total = f'SUM({fact_alias}."{safe_agg_col}_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
                                            sum_own = f'{fact_alias}."{safe_agg_col}_SUM"'
                                            cnt_total = f'SUM({fact_alias}."{safe_agg_col}_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING {dim_ref})'
                                            cnt_own = f'{fact_alias}."{safe_agg_col}_CNT"'

                                            self._current_loo_info = {
                                                "dim_ref": dim_ref,
                                                "col_name": c1,
                                                "agg_col": safe_agg_col,
                                                "inner_fn": inner_fn,
                                                "sum_total": sum_total,
                                                "sum_own": sum_own,
                                                "cnt_total": cnt_total,
                                                "cnt_own": cnt_own,
                                            }

                                            if inner_fn in ("AVERAGE", "AVERAGEX"):
                                                return f'(({sum_total} - {sum_own}) / NULLIF({cnt_total} - {cnt_own}, 0))'
                                            elif inner_fn in ("COUNT", "COUNTROWS", "COUNTA"):
                                                return f'({cnt_total} - {cnt_own})'
                                            else:
                                                return f'({sum_total} - {sum_own})'

                                if is_own_fact:
                                    raise self.DaxRenderError(
                                        "BY_DESIGN_EXCLUDED: whole-table filtered aggregate requires enriched view precompute"
                                    )

                            iter_tbl = self._extract_table_name(f_tbl)
                            with self._row_context(iter_tbl):
                                pred = self._render_node(filter_arg.args[1])

                            filter_clauses.append(pred)

                        else:

                            raise self.DaxRenderError("FILTER requires table and predicate arguments")

                    else:
                        filter_clauses.append(self._render_calc_filter_predicate(filter_arg))
                else:
                    # Direct comparison / NOT / IN inside CALCULATE, e.g. 'Date'[Year] = 2024
                    filter_clauses.append(self._render_calc_filter_predicate(filter_arg))

            if is_window:
                if True:
                    excl_dims = []
                    fact_alias = (self.table_alias or "").upper()
                    fact_tbl = (self.primary_table_name or self.table_alias or "").upper()

                    for filter_arg in filtered_args[1:]:
                        if not isinstance(filter_arg, FunctionCallNode):
                            continue
                        fmod = filter_arg.func.upper()
                        if fmod in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
                            if not filter_arg.args:
                                clean_base = "SUM_AMOUNT"
                                base_arg = filtered_args[0]
                                if isinstance(base_arg, MeasureRefNode):
                                    clean_base = re.sub(r'[^A-Za-z0-9_]', '_', base_arg.name).upper()
                                elif isinstance(base_arg, FunctionCallNode):
                                    col_part = getattr(base_arg.args[0], "column", str(base_arg.args[0])) if base_arg.args else ""
                                    clean_base = f"{base_arg.func.upper()}_{re.sub(r'[^A-Za-z0-9_]', '_', col_part).upper()}".strip("_")
                                clean_base = re.sub(r'^_?ALL_', '', clean_base)
                                tgt_alias = (fact_alias or fact_tbl or "f").upper()
                                return f'MAX({tgt_alias}."_ALL_{clean_base}")'
                            if (len(filter_arg.args) >= 3 and 
                                isinstance(filter_arg.args[0], ColumnRefNode) and 
                                getattr(filter_arg.args[1], "name", "") == "."):
                                date_col_node = filter_arg.args[0]
                                level_node = filter_arg.args[2]
                                level_name = getattr(level_node, "name", str(level_node))
                                t_name = date_col_node.table or self.table_alias
                                t_alias = self.table_aliases.get(t_name, t_name).upper() if self.table_aliases else t_name.upper()
                                dims_found = self._get_emitted_dimensions_for_table(t_name, t_alias)
                                norm_lvl = re.sub(r"[^A-Z0-9]", "", level_name.upper())
                                exact_matches = []
                                substr_matches = []
                                for d in dims_found:
                                    col_part = d.split(".")[-1].strip('"')
                                    norm_col = re.sub(r"[^A-Z0-9]", "", col_part.upper())
                                    if norm_col == norm_lvl:
                                        exact_matches.append(d)
                                    elif norm_lvl in norm_col:
                                        substr_matches.append((len(norm_col), d))
                                if exact_matches:
                                    matched_dim = exact_matches[0]
                                elif substr_matches:
                                    substr_matches.sort(key=lambda x: x[0])
                                    matched_dim = substr_matches[0][1]
                                else:
                                    matched_dim = None
                                if matched_dim:
                                    if matched_dim not in excl_dims:
                                        excl_dims.append(matched_dim)
                                else:
                                    raise self.DaxRenderError(f"no matching date hierarchy dimension for {level_name}")
                                continue

                            for arg in filter_arg.args:
                                if isinstance(arg, ColumnRefNode):
                                    tbl = arg.table or self.table_alias
                                    if self.table_aliases and tbl in self.table_aliases:
                                        tbl = self.table_aliases[tbl]
                                    safe_col = sanitize_column(arg.column)
                                    d_ref = f'{tbl.upper()}."{safe_col}"'
                                    if d_ref not in excl_dims:
                                        excl_dims.append(d_ref)
                                elif isinstance(arg, IdentifierNode):
                                    tbl_name = arg.name.strip('"\'')
                                    alias = self.table_aliases.get(tbl_name, tbl_name).upper() if self.table_aliases else tbl_name.upper()
                                    if alias == fact_alias or tbl_name.upper() == fact_tbl:
                                        loo = getattr(self, "_current_loo_info", None)
                                        if loo:
                                            if loo.get("inner_fn") in ("AVERAGE", "AVERAGEX"):
                                                return f'({loo["sum_total"]} / NULLIF({loo["cnt_total"]}, 0))'
                                            elif loo.get("inner_fn") in ("COUNT", "COUNTROWS", "COUNTA"):
                                                return f'{loo["cnt_total"]}'
                                            else:
                                                return f'{loo["sum_total"]}'
                                        clean_base = "TOTAL"
                                        base_arg = filtered_args[0]
                                        if isinstance(base_arg, MeasureRefNode):
                                            clean_base = re.sub(r'[^A-Za-z0-9_]', '_', base_arg.name).upper()
                                        elif isinstance(base_arg, FunctionCallNode):
                                            col_part = getattr(base_arg.args[0], "column", str(base_arg.args[0])) if base_arg.args else ""
                                            clean_base = f"{base_arg.func.upper()}_{re.sub(r'[^A-Za-z0-9_]', '_', col_part).upper()}".strip("_")
                                        elif hasattr(base_arg, "name"):
                                            clean_base = re.sub(r'[^A-Za-z0-9_]', '_', base_arg.name).upper()
                                        clean_base = re.sub(r'^_?ALL_', '', clean_base)
                                        tgt_alias = (fact_alias or fact_tbl or "f").upper()
                                        return f'MAX({tgt_alias}."_ALL_{clean_base}")'
                                    dims_found = self._get_minimal_groupable_dimensions(tbl_name, alias)
                                    for d in dims_found:
                                        if d not in excl_dims:
                                            excl_dims.append(d)
                        elif fmod == "ALLEXCEPT":
                            if not filter_arg.args:
                                continue
                            tbl_node = filter_arg.args[0]
                            tbl_name = (getattr(tbl_node, "table", None) or getattr(tbl_node, "name", None) or str(tbl_node)).strip('"\'')
                            alias = self.table_aliases.get(tbl_name, tbl_name).upper() if self.table_aliases else tbl_name.upper()
                            part_dims = []
                            keep_names = set()
                            for k_arg in filter_arg.args[1:]:
                                if isinstance(k_arg, ColumnRefNode):
                                    t = k_arg.table or tbl_name
                                    t_al = self.table_aliases.get(t, t).upper() if self.table_aliases else t.upper()
                                    col = sanitize_column(k_arg.column)
                                    part_dims.append(f'{t_al}."{col}"')
                                    keep_names.add(re.sub(r"[^A-Z0-9]", "", k_arg.column.upper()))
                                elif isinstance(k_arg, IdentifierNode):
                                    col = sanitize_column(k_arg.name)
                                    part_dims.append(f'{alias}."{col}"')
                                    keep_names.add(re.sub(r"[^A-Z0-9]", "", k_arg.name.upper()))

                            is_fact = (alias == fact_alias or tbl_name.upper() == fact_tbl)
                            if part_dims:
                                part_clause = ", ".join(part_dims)
                                base_arg = filtered_args[0]
                                if isinstance(base_arg, MeasureRefNode):
                                    m_alias = sanitize_column(base_arg.name)
                                    metric_ref = f'{self.table_alias.upper()}."{m_alias}"::FLOAT'
                                    return f'SUM({metric_ref}) OVER (PARTITION BY {part_clause})'
                                elif isinstance(base_arg, FunctionCallNode):
                                    fn_u = base_arg.func.upper()
                                    col_node = base_arg.args[0] if base_arg.args else None
                                    col_part = getattr(col_node, "column", None) or getattr(col_node, "name", "AMT") if col_node else "AMT"
                                    col_tbl = getattr(col_node, "table", None)
                                    target_alias = (self.table_aliases.get(col_tbl, col_tbl).upper() if self.table_aliases else col_tbl.upper()) if col_tbl else fact_alias
                                    c_alias = sanitize_column(col_part)
                                    if fn_u in ("AVERAGE", "AVERAGEX"):
                                        return f'SUM({target_alias}."{c_alias}_SUM"::FLOAT) OVER (PARTITION BY {part_clause}) / NULLIF(SUM({target_alias}."{c_alias}_CNT"::FLOAT) OVER (PARTITION BY {part_clause}), 0)'
                                    elif fn_u in ("COUNT", "COUNTROWS", "COUNTA"):
                                        return f'SUM({target_alias}."{c_alias}_CNT"::FLOAT) OVER (PARTITION BY {part_clause})'
                                    elif fn_u in ("SUM", "SUMX"):
                                        return f'SUM({target_alias}."{c_alias}_SUM"::FLOAT) OVER (PARTITION BY {part_clause})'
                                    elif fn_u in ("MIN", "MAX"):
                                        return f'{fn_u}({target_alias}."{c_alias}_{fn_u}"::FLOAT) OVER (PARTITION BY {part_clause})'
                                    else:
                                        raise self.DaxRenderError(f"ALLEXCEPT requires an additive base metric, got '{base_arg.func}'")

                            if is_fact:
                                raise self.DaxRenderError(_CONTEXT_TRANSITION_REASON)
                            target_tables = [tbl_name]
                            if is_fact and self.relationships:
                                for rel in self.relationships:
                                    from_tbl = getattr(rel, "from_dataset", None) or getattr(rel, "fromTable", "")
                                    to_tbl = getattr(rel, "to_dataset", None) or getattr(rel, "toTable", "")
                                    is_active = getattr(rel, "is_active", getattr(rel, "isActive", True))
                                    if is_active and from_tbl and from_tbl.casefold() == tbl_name.casefold() and to_tbl:
                                        if to_tbl not in target_tables:
                                            target_tables.append(to_tbl)

                            for t_name in target_tables:
                                t_alias = self.table_aliases.get(t_name, t_name).upper() if self.table_aliases else t_name.upper()
                                dims_found = self._get_minimal_groupable_dimensions(t_name, t_alias)
                                for d in dims_found:
                                    col_part = d.split(".")[-1].strip('"')
                                    if re.sub(r"[^A-Z0-9]", "", col_part.upper()) not in keep_names:
                                        if d not in excl_dims:
                                            excl_dims.append(d)

                    if excl_dims:
                        excl_clause = ", ".join(excl_dims)
                        base_arg = filtered_args[0]
                        if isinstance(base_arg, MeasureRefNode):
                            m_name = base_arg.name
                            sql_cand = self.measure_sql_map.get(m_name) or self.measure_sql_map.get(m_name.casefold())
                            if sql_cand:
                                if self._has_nested_window(sql_cand):
                                    raise self.DaxRenderError("nested window over a non-additive measure")
                                cand_u = sql_cand.upper()
                                if any(bad in cand_u for bad in ("AVG(", "AVERAGE(", "MEDIAN(", "STDDEV(", "VARIANCE(", "COUNT(DISTINCT")) or "/" in cand_u:
                                    m_alias = sanitize_column(m_name)
                                    ratio = f'SUM({fact_alias}."{m_alias}_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}) / NULLIF(SUM({fact_alias}."{m_alias}_DEN"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}), 0)'
                                    m_wrap = re.match(r'^ROUND\s*\(\s*(.+?)\s*(\*\s*\d+\s*,\s*\d+)\s*\)\s*/\s*(\d+)$', sql_cand.strip(), re.IGNORECASE | re.DOTALL)
                                    if m_wrap:
                                        return f'ROUND(({ratio}) {m_wrap.group(2)}) / {m_wrap.group(3)}'
                                    m_round_only = re.match(r'^ROUND\s*\(\s*(.+?)\s*,\s*(\d+)\s*\)$', sql_cand.strip(), re.IGNORECASE | re.DOTALL)
                                    if m_round_only:
                                        return f'ROUND(({ratio}), {m_round_only.group(2)})'
                                    return ratio
                            m_alias = sanitize_column(m_name)
                            metric_ref = f'{self.table_alias.upper()}."{m_alias}"::FLOAT'
                            return f'SUM({metric_ref}) OVER (PARTITION BY EXCLUDING {excl_clause})'
                        elif isinstance(base_arg, FunctionCallNode):
                            fn_u = base_arg.func.upper()
                            if fn_u in ("ROUND", "ROUNDUP", "ROUNDDOWN") and base_arg.args:
                                inner_calc = self._render_calculate([base_arg.args[0]] + list(filter_args))
                                dec = self._render_node(base_arg.args[1]) if len(base_arg.args) > 1 else "0"
                                return f"ROUND(({inner_calc}), {dec})"
                            if fn_u in ("AVERAGE", "AVERAGEX"):
                                col_node = base_arg.args[0] if base_arg.args else None
                                col_part = getattr(col_node, "column", None) or getattr(col_node, "name", "AMT") if col_node else "AMT"
                                col_tbl = getattr(col_node, "table", None)
                                target_alias = (self.table_aliases.get(col_tbl, col_tbl).upper() if self.table_aliases else col_tbl.upper()) if col_tbl else fact_alias
                                c_alias = sanitize_column(col_part)
                                return f'SUM({target_alias}."{c_alias}_SUM"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}) / NULLIF(SUM({target_alias}."{c_alias}_CNT"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}), 0)'
                            if fn_u == "DIVIDE":
                                if len(base_arg.args) >= 2 and isinstance(base_arg.args[1], LiteralNode) and isinstance(base_arg.args[1].value, (int, float)) and base_arg.args[1].value != 0:
                                    inner_calc = self._render_calculate([base_arg.args[0]] + list(filter_args))
                                    return f"({inner_calc}) / {base_arg.args[1].value}"
                                m_alias = sanitize_column(getattr(self, "current_measure_name", "") or "M")
                                if not m_alias or m_alias == "M" or m_alias.upper() == fact_alias.upper():
                                    m_alias = sanitize_column(self.table_alias)
                                return f'SUM({fact_alias}."{m_alias}_NUM"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}) / NULLIF(SUM({fact_alias}."{m_alias}_DEN"::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause}), 0)'
                            if fn_u not in ("SUM", "COUNT", "COUNTROWS", "COUNTA", "MIN", "MAX"):
                                raise self.DaxRenderError(f"Share-of-total requires an additive base metric (SUM, COUNT, COUNTROWS); got non-additive '{base_arg.func}'")
                            agg_op = "MIN" if fn_u == "MIN" else ("MAX" if fn_u == "MAX" else "SUM")
                            rendered_base = self._render_node(base_arg)
                            matched_m = None
                            for m_k, m_sql in self.measure_sql_map.items():
                                if m_sql.strip().upper() in (rendered_base.strip().upper(), f"({rendered_base.strip().upper()})"):
                                    matched_m = m_k
                                    break
                            if matched_m:
                                m_alias = sanitize_column(matched_m)
                                metric_ref = f'{self.table_alias.upper()}."{m_alias}"::FLOAT'
                                return f'{agg_op}({metric_ref}) OVER (PARTITION BY EXCLUDING {excl_clause})'
                            return f'{agg_op}({rendered_base}::FLOAT) OVER (PARTITION BY EXCLUDING {excl_clause})'
                        elif isinstance(base_arg, BinaryOpNode) and base_arg.op in ("+", "-"):
                            r_left = self._render_calculate([base_arg.left] + list(filter_args))
                            r_right = self._render_calculate([base_arg.right] + list(filter_args))
                            return f"({r_left} {base_arg.op} {r_right})"
                        elif isinstance(base_arg, BinaryOpNode) and base_arg.op == "*":
                            if isinstance(base_arg.right, LiteralNode) and isinstance(base_arg.right.value, (int, float)):
                                inner_calc = self._render_calculate([base_arg.left] + list(filter_args))
                                return f"({inner_calc}) * {base_arg.right.value}"
                            elif isinstance(base_arg.left, LiteralNode) and isinstance(base_arg.left.value, (int, float)):
                                inner_calc = self._render_calculate([base_arg.right] + list(filter_args))
                                return f"{base_arg.left.value} * ({inner_calc})" 

                raise self.DaxRenderError(
                    "not applicable: needs window metric"
                )

            elif filter_clauses:
                where = " AND ".join(
                    f"({c})" if (len(filter_clauses) > 1 and re.search(r"\bOR\b", c, re.IGNORECASE)) else c
                    for c in filter_clauses
                )

                if isinstance(filtered_args[0], FunctionCallNode) and (

                    filtered_args[0].func.upper() in self._AGG_MAP or filtered_args[0].func.upper() in self._ITERATOR_MAP

                ):

                    return self._rewrite_filtered_aggregate(filtered_args[0], where)

                rewritten = self._inject_case_filter_into_rendered_aggregate(agg_expr, where)

                if rewritten:

                    return rewritten

                raise self.DaxRenderError(

                    f"Base aggregation {filtered_args[0]} is too complex to rewrite as CASE-based filtered aggregate"

                )

            else:

                return agg_expr
        finally:
            self._userel_overrides = prev_userel




    # Must stay in sync with converter/time_intelligence_shapes.py's

    # PERIOD_TO_DATE_FUNCS / LAG_FUNCS — duplicated locally (not imported)

    # to avoid a circular import (that module imports node classes from

    # this one).

    _PERIOD_TO_DATE_COMPONENT = {
        "TOTALYTD": "YTD", "TOTALMTD": "MTD", "TOTALQTD": "QTD",
        "DATESYTD": "YTD", "DATESMTD": "MTD", "DATESQTD": "QTD",
    }

    _PERIOD_TO_DATE_UNIT = {
        "TOTALYTD": "YEAR", "TOTALMTD": "MONTH", "TOTALQTD": "QUARTER",
        "DATESYTD": "YEAR", "DATESMTD": "MONTH", "DATESQTD": "QUARTER",
    }



    def _render_period_to_date(
        self, func: str, args: List[DaxNode], year_end_node: Optional[DaxNode] = None
    ) -> str:
        """
        Translate TOTALYTD / TOTALMTD / TOTALQTD (and DATESxTD modifiers) to
        Snowflake CASE WHEN scalar aggregates (no window functions).
        Pattern: TOTALYTD(expression, dates[, filter][, year_end_date])

        Shape-flag mode references a precomputed boolean flag column on the
        enriched view (anchored to the data's last date); otherwise the
        window is anchored to CURRENT_DATE() and an advisory is recorded
        (see render()). Extra filter arguments are ANDed into the window;
        a fiscal year end that is a month end shifts the year start; any
        other year end is declined.
        """
        if not args:
            raise self.DaxRenderError(f"{func} requires at least 1 argument")
        if len(args) < 2:
            raise self.DaxRenderError(f"{func} requires a dates argument")
        extra_filter_nodes: List[DaxNode] = []
        rest = list(args[2:])
        # `Date[Date]` with an unquoted table name that is also a function
        # name lexes as Identifier + [Date]: that bracket is the dates
        # column, not a filter argument.
        if rest and isinstance(args[1], IdentifierNode) and isinstance(rest[0], MeasureRefNode):
            rest = rest[1:]
        if func in ("TOTALYTD", "TOTALQTD", "TOTALMTD"):
            for extra in rest:
                if func == "TOTALYTD" and isinstance(extra, LiteralNode) and isinstance(extra.value, str):
                    year_end_node = extra
                else:
                    extra_filter_nodes.append(extra)
        date_col = self._ti_date_column_sql(args[:2])
        component = self._PERIOD_TO_DATE_COMPONENT.get(func)
        unit = self._PERIOD_TO_DATE_UNIT.get(func)
        fiscal_month: Optional[int] = None
        if year_end_node is not None:
            if not (isinstance(year_end_node, LiteralNode) and isinstance(year_end_node.value, str)):
                raise self.DaxRenderError("not applicable: fiscal year-end not supported (non-literal year_end_date)")
            try:
                fiscal_month = parse_fiscal_year_end(year_end_node.value)
            except ValueError as exc:
                raise self.DaxRenderError(f"not applicable: fiscal year-end not supported ({exc})")
            if fiscal_month and unit != "YEAR":
                raise self.DaxRenderError("not applicable: fiscal year-end not supported for this function")
        if fiscal_month:
            component = fiscal_ytd_component(fiscal_month)
        flag_name = self.anchor_flag_map.get((component,)) if component else None
        if flag_name:
            condition_sql = f'{self.table_alias}."{flag_name}"'
        else:
            # No flag column provisioned for this shape (schema-blind
            # dry-run/preview, or enrichment couldn't anchor this fact
            # table) -- anchor to CURRENT_DATE(); render() records the
            # ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE advisory.
            max_date = "CURRENT_DATE()"
            if unit is None:
                raise self.DaxRenderError(f"Unhandled period-to-date func: {func}")
            if fiscal_month:
                start_sql = (
                    f"DATEADD(MONTH, {fiscal_month}, DATE_TRUNC('YEAR', DATEADD(MONTH, -{fiscal_month}, {max_date})))"
                )
            else:
                start_sql = f"DATE_TRUNC('{unit}', {max_date})"
            condition_sql = f"{date_col} >= {start_sql} AND {date_col} <= {max_date}"
        if extra_filter_nodes:
            preds = [self._render_calc_filter_predicate(n) for n in extra_filter_nodes]
            condition_sql = condition_sql + "".join(f" AND ({p})" for p in preds)
        # Unwrap the inner aggregation to build a CASE WHEN expression
        agg_node = args[0]
        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            if inner_func in ("COUNTROWS", "DISTINCTCOUNT"):
                return self._rewrite_filtered_aggregate(agg_node, condition_sql)
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else self._no_arg_aggregate()
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            if sql_func == "COUNTA":
                sql_func = "COUNT"
            cast = "::FLOAT" if inner_func in ("SUM", "AVERAGE") else ""
            return f"{sql_func}(CASE WHEN {condition_sql} THEN {col_sql}{cast} END)"
        # Base argument isn't a direct aggregation call -- typically a
        # measure reference. Inject the window into ITS aggregate leaves
        # rather than wrapping an already-aggregated expression.
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

    _LAG_COMPONENT = {"year": "SPLY_YEAR", "quarter": "SPLY_QUARTER", "month": "SPLY_MONTH", "day": "SPLY_DAY"}



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



    # Functions returning the FULL shifted period (not a same-period-to-date
    # shift): PREVIOUSYEAR/QUARTER/MONTH and PARALLELPERIOD.
    _FULL_PERIOD_LAG_FUNCS = frozenset({"PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH", "PARALLELPERIOD"})

    @staticmethod
    def lag_shape_component(func_name: Optional[str], interval: str, amount: int) -> str:
        """Canonical shape component -- identical to time_intelligence_shapes'
        discovery naming, so flag lookups and nested-shape composition agree."""
        unit = interval.upper()
        fn = (func_name or "").upper()
        n = abs(int(amount))
        if fn == "SAMEPERIODLASTYEAR":
            return "SPLY_YEAR"
        if fn in ("PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH"):
            return f"PREV_{unit}"
        if fn == "PARALLELPERIOD":
            return f"PREV_{unit}" if n == 1 else f"PREV_{n}_{unit}"
        return f"SPLY_{unit}" if n == 1 else f"SPLY_{n}_{unit}"

    def _render_lag_period(
        self, args: List[DaxNode], interval: str, amount: int, func_name: Optional[str] = None
    ) -> str:
        """
        Translate SAMEPERIODLASTYEAR / DATEADD (same period shifted) and
        PREVIOUSxxx / PARALLELPERIOD (FULL shifted period) to scalar CASE WHEN
        bounded aggregates. Snowflake METRICS clause does NOT allow window
        functions (OVER / LAG).
        """
        if not args:
            raise self.DaxRenderError("Period function requires arguments")
        if len(args) != 2:
            raise self.DaxRenderError("Period function expects (aggregate, date_column) arguments")
        date_col = self._ti_date_column_sql(args)
        max_date = "CURRENT_DATE()"
        unit = interval.upper()
        full_period = (func_name or "").upper() in self._FULL_PERIOD_LAG_FUNCS
        shift_expr = f"DATEADD({unit}, {amount}, {max_date})"
        agg_node = args[0]
        lag_component = self.lag_shape_component(func_name, interval, amount)
        if getattr(self, "_comparison_context", None) and self._comparison_context.get("lag_flag"):
            base_flag = self._comparison_context["lag_flag"]
            lag_component = (self._comparison_context.get("lag_shape") or (lag_component,))[0]
        else:
            n = abs(int(amount))
            candidates = [lag_component]
            if lag_component == f"SPLY_{unit}":
                candidates.append(f"SPLY_1_{unit}")
            # For MONTH/QUARTER/DAY a same-period shift and a full-period
            # shift select the same rows, so either flag is acceptable; for
            # YEAR they differ (year-to-date vs full year) and are never mixed.
            if unit != "YEAR":
                candidates += [f"PREV_{unit}" if n == 1 else f"PREV_{n}_{unit}",
                               f"SPLY_{unit}" if n == 1 else f"SPLY_{n}_{unit}"]
            base_flag = None
            if self.anchor_flag_map:
                for cand in candidates:
                    if self.anchor_flag_map.get((cand,)):
                        base_flag = self.anchor_flag_map[(cand,)]
                        break

        def _inline_condition() -> str:
            if unit == "DAY":
                return f"{date_col} = {shift_expr}"
            if unit == "YEAR":
                if full_period:
                    return f"YEAR({date_col}) = YEAR({shift_expr})"
                return (
                    f"YEAR({date_col}) = YEAR({shift_expr}) "
                    f"AND {date_col} BETWEEN DATEADD(YEAR, {amount}, DATE_TRUNC('YEAR', {max_date})) "
                    f"AND {shift_expr}"
                )
            if unit == "QUARTER":
                return (
                    f"YEAR({date_col}) = YEAR({shift_expr}) "
                    f"AND QUARTER({date_col}) = QUARTER({shift_expr})"
                )
            if unit == "MONTH":
                return (
                    f"YEAR({date_col}) = YEAR({shift_expr}) "
                    f"AND MONTH({date_col}) = MONTH({shift_expr})"
                )
            raise self.DaxRenderError(f"not applicable: period shift by '{interval}' not supported")

        if isinstance(agg_node, FunctionCallNode) and agg_node.func.upper() in self._AGG_MAP:
            inner_func = agg_node.func.upper()
            condition_sql = f'{self.table_alias}."{base_flag}"' if base_flag else _inline_condition()
            if inner_func in ("COUNTROWS", "DISTINCTCOUNT"):
                return self._rewrite_filtered_aggregate(agg_node, condition_sql)
            col_sql = self._render_node(agg_node.args[0]) if agg_node.args else self._no_arg_aggregate()
            sql_func = "AVG" if inner_func == "AVERAGE" else inner_func
            if sql_func == "COUNTA":
                sql_func = "COUNT"
            cast = "::FLOAT" if inner_func in ("SUM", "AVERAGE") else ""
            return f"{sql_func}(CASE WHEN {condition_sql} THEN {col_sql}{cast} END)"
        # Base argument isn't a direct aggregation call -- e.g. a measure
        # reference such as SAMEPERIODLASTYEAR([Total Sales], 'Date'[Date]).
        agg_sql = self._render_node(agg_node)
        if self.anchor_flag_map:
            # Shape-flag mode: nested composition swaps the inner shape's flag
            # for the combined shape's flag (see time_intelligence_shapes).
            for inner_shape, inner_flag in self.anchor_flag_map.items():
                inner_ref = f'{self.table_alias}."{inner_flag}"'
                if inner_ref not in agg_sql:
                    continue
                combined_flag = self.anchor_flag_map.get(inner_shape + (lag_component,))
                if combined_flag:
                    return agg_sql.replace(inner_ref, f'{self.table_alias}."{combined_flag}"')
                raise self.DaxRenderError(
                    f"Period function needs a flag column for the nested shape "
                    f"{inner_shape + (lag_component,)!r}, but none was provisioned -- "
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
        # The referenced measure already anchors its own window (e.g. a YTD
        # measure). Same-period shifts move the anchor back by the lag;
        # full-period functions re-anchor the inner window at the LAST day
        # of the shifted period (DAX: the inner measure is evaluated over the
        # whole previous period, so its LASTDATE is that period's end).
        if full_period and unit in ("YEAR", "QUARTER", "MONTH"):
            anchor_replacement = f"LAST_DAY({shift_expr}, '{unit}')"
        elif full_period:
            anchor_replacement = None
        else:
            anchor_replacement = shift_expr
        if max_date in agg_sql:
            if anchor_replacement is None:
                raise self.DaxRenderError("not applicable: full-period shift of an anchored measure by this interval")
            return agg_sql.replace(max_date, anchor_replacement)
        legacy_max_date = f'{self.table_alias}."MAX_DATE"'
        if legacy_max_date in agg_sql:
            legacy_shift = f"DATEADD({unit}, {amount}, {legacy_max_date})"
            if full_period:
                if unit not in ("YEAR", "QUARTER", "MONTH"):
                    raise self.DaxRenderError("not applicable: full-period shift of an anchored measure by this interval")
                legacy_shift = f"LAST_DAY({legacy_shift}, '{unit}')"
            return agg_sql.replace(legacy_max_date, legacy_shift)
        rewritten = self._inject_case_filter_into_rendered_aggregate(agg_sql, _inline_condition())
        if rewritten:
            return rewritten
        return self._lag_period_unshifted_fallback_or_raise(
            agg_sql,
            f"Period function base expression '{agg_sql}' is not a simple aggregate this "
            "renderer can safely apply a date-range filter to without risking a nested aggregate",
        )

    def _no_arg_aggregate(self) -> str:
        raise self.DaxRenderError("aggregate without an argument inside a time-intelligence function")

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

    table_aliases: Optional[Dict[str, str]] = None,

    relationships: Optional[Iterable[Any]] = None,

    measure_non_additive_map: Optional[Dict[str, Tuple[Optional[str], str]]] = None,

    non_additive_dimensions: Optional[List[Tuple[Optional[str], str]]] = None,

    table_key_map: Optional[Dict[str, str]] = None,

    allow_window_enrichment: bool = False,

    datasets: Optional[Iterable[Any]] = None,

    model_metadata: Optional[Any] = None,
    dataset_col_lookup: Optional[Dict[str, Any]] = None,
    render_errors: Optional[List[str]] = None,
) -> Optional[str]:

    """

    Attempt to translate a DAX expression to Snowflake SQL via AST parsing.



    This is the entry-point called by DAXTranslator for Tier 3/4 expressions

    that cannot be handled by the fast-path regex approach.

    """

    if date_table_names is None and (datasets or model_metadata):
        all_ds = datasets or getattr(model_metadata, "datasets", None) or []
        all_rels = relationships or getattr(model_metadata, "relationships", None) or []
        date_table_names = detect_date_tables(all_ds, all_rels, [dax])

    allow_window_enrichment = bool(allow_window_enrichment)

    clean_dax_upper = re.sub(r"\s+", " ", str(dax or "").upper())
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in clean_dax_upper or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in clean_dax_upper:
        return "TRUE"

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

        table_aliases,

        relationships,

        measure_non_additive_map,

        table_key_map,

        allow_window_enrichment,

    )

    if cache_key in _AST_CACHE:

        entry = _AST_CACHE[cache_key]

        cached_sql = entry[0]

        cached_categories = entry[1]

        cached_dims = entry[2] if len(entry) > 2 else ()
        cached_err = entry[3] if len(entry) > 3 else None
        if render_errors is not None and cached_err:
            render_errors.append(cached_err)

        if advisory_categories is not None:

            for category in cached_categories:

                if category not in advisory_categories:

                    advisory_categories.append(category)

        if non_additive_dimensions is not None:

            for d in cached_dims:

                if d not in non_additive_dimensions:

                    non_additive_dimensions.append(d)

        return cached_sql



    parser = DaxAstParser()

    ast = parser.parse(dax)

    if ast is None:
        err = "DAX expression could not be parsed"
        _AST_CACHE[cache_key] = (None, (), (), err)
        if render_errors is not None:
            render_errors.append(err)
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

        advisory_categories=None,

        table_aliases=table_aliases,

        relationships=relationships,

        measure_non_additive_map=measure_non_additive_map,

        table_key_map=table_key_map,

        allow_window_enrichment=allow_window_enrichment,

        datasets=datasets,

        model_metadata=model_metadata,
        dataset_col_lookup=dataset_col_lookup,

    )

    sql = renderer.render(ast)

    if len(_AST_CACHE) >= _AST_CACHE_MAX:

        _AST_CACHE.clear()

    # Advisories/dimensions recorded during a FAILED render describe SQL that
    # was never shipped -- never propagate them.
    produced_categories = tuple(renderer._advisory_categories) if sql else ()
    produced_dims = tuple(renderer.non_additive_dimensions) if sql else ()
    produced_err = None if sql else (renderer.last_render_error or "AST renderer declined the expression")
    _AST_CACHE[cache_key] = (sql, produced_categories, produced_dims, produced_err)
    if render_errors is not None and produced_err:
        render_errors.append(produced_err)

    if advisory_categories is not None:

        for category in produced_categories:

            if category not in advisory_categories:

                advisory_categories.append(category)

    if non_additive_dimensions is not None:

        for d in produced_dims:

            if d not in non_additive_dimensions:

                non_additive_dimensions.append(d)

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





def _ast_contains_func(node: Optional[DaxNode], names: Set[str]) -> bool:
    """True if any FunctionCallNode in `node` has a name in `names`."""
    upper = {n.upper() for n in names}
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, FunctionCallNode):
            if n.func.upper() in upper:
                return True
            stack.extend(n.args)
        elif isinstance(n, BinaryOpNode):
            stack.extend([n.left, n.right])
        elif isinstance(n, UnaryOpNode):
            stack.append(n.operand)
        elif isinstance(n, VarNode):
            stack.extend(v for _, v in n.variables)
            stack.append(n.body)
    return False


def _iter_children(n: DaxNode) -> List[DaxNode]:
    if isinstance(n, FunctionCallNode):
        return list(n.args)
    if isinstance(n, BinaryOpNode):
        return [n.left, n.right]
    if isinstance(n, UnaryOpNode):
        return [n.operand]
    if isinstance(n, VarNode):
        return [v for _, v in n.variables] + [n.body]
    return []


_GROUP_GRAIN_TABLE_FUNCS = frozenset({
    "VALUES", "DISTINCT", "SUMMARIZE", "SUMMARIZECOLUMNS", "ADDCOLUMNS", "GROUPBY",
    "TOPN", "CROSSJOIN", "UNION", "DATESYTD", "DATESMTD", "DATESQTD", "DATESINPERIOD",
    "DATESBETWEEN", "CALCULATETABLE", "SELECTCOLUMNS",
})
_SCOPE_GUARD_FUNCS = frozenset({"HASONEVALUE", "HASONEFILTER", "ISINSCOPE", "ISFILTERED", "ISCROSSFILTERED"})
_ITERATOR_FUNCS = frozenset({"SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX", "COUNTAX", "PRODUCTX", "CONCATENATEX"})


def dax_semantic_fidelity_failure_reason(
    parsed_ast: Optional[DaxNode],
    primary_table_name: Optional[str] = None,
) -> Optional[str]:
    """Structural pre-check for DAX shapes whose exact semantics no
    translation tier can reproduce in a semantic-view metric. Returns a
    specific, user-readable 'not applicable: ...' reason, or None.

    Runs before every translation tier so no regex/rule tier can ship an
    approximation for these shapes."""
    if parsed_ast is None:
        return None

    def _walk(n: Optional[DaxNode]) -> Optional[str]:
        if n is None:
            return None
        if isinstance(n, FunctionCallNode):
            fn = n.func.upper()
            if fn == "USERELATIONSHIP":
                return USERELATIONSHIP_REASON
            if fn == "IF" and len(n.args) >= 3:
                # IF(<guard>, ERROR(...), calc) quick-measure unwrap: only the
                # non-error branch is translated.
                for err_idx, keep_idx in ((1, 2), (2, 1)):
                    br = n.args[err_idx]
                    if isinstance(br, FunctionCallNode) and br.func.upper() == "ERROR":
                        return _walk(n.args[keep_idx])
            if fn in _SCOPE_GUARD_FUNCS:
                return HASONEVALUE_GUARD_REASON
            if fn in ("TOTALYTD", "DATESYTD"):
                ye = period_to_date_year_end_node(n)
                if ye is not None:
                    if not (isinstance(ye, LiteralNode) and isinstance(ye.value, str)):
                        return "not applicable: fiscal year-end not supported (non-literal year_end_date)"
                    try:
                        parse_fiscal_year_end(ye.value)
                    except ValueError as exc:
                        return f"not applicable: fiscal year-end not supported ({exc})"
            if fn == "TOPN":
                return "not applicable: TOPN (a top-N set that depends on the query context) is not supported"
            if fn in _ITERATOR_FUNCS and n.args:
                tbl = n.args[0]
                while isinstance(tbl, FunctionCallNode) and tbl.func.upper() in ("FILTER", "KEEPFILTERS") and tbl.args:
                    tbl = tbl.args[0]
                if isinstance(tbl, FunctionCallNode):
                    tfn = tbl.func.upper()
                    group_grain = tfn in _GROUP_GRAIN_TABLE_FUNCS or (
                        tfn in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "ALLSELECTED")
                        and tbl.args and isinstance(tbl.args[0], ColumnRefNode)
                    )
                    if group_grain:
                        expr = n.args[1] if len(n.args) > 1 else None
                        same_col_minmax = (
                            fn in ("MINX", "MAXX") and isinstance(expr, ColumnRefNode)
                            and tfn in ("VALUES", "DISTINCT", "ALL", "ALLNOBLANKROW")
                            and any(
                                isinstance(a, ColumnRefNode) and a.column.casefold() == expr.column.casefold()
                                for a in tbl.args
                            )
                        )
                        if not same_col_minmax:
                            what = _dax_text(tbl)
                            return (
                                f"not applicable: {fn} over {what} evaluates its expression once per group "
                                "(context transition) and re-aggregates; not expressible as a single "
                                "semantic-view metric"
                            )
                elif isinstance(tbl, IdentifierNode) and primary_table_name and len(n.args) > 1:
                    t = tbl.name.strip("'\"")
                    if t.casefold() != primary_table_name.strip("'\"").casefold() and (
                        _node_contains_measure_ref(n.args[1]) or _ast_contains_func(
                            n.args[1], {"SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTROWS", "DISTINCTCOUNT", "CALCULATE"}
                        )
                    ):
                        return (
                            f"not applicable: {fn} over '{t}' evaluates a measure once per '{t}' row "
                            "(context transition) and re-aggregates; not expressible as a single semantic-view metric"
                        )
        for c in _iter_children(n):
            r = _walk(c)
            if r:
                return r
        return None

    return _walk(parsed_ast)


def dax_has_overriding_calculate_filter(parsed_ast: Optional[DaxNode]) -> bool:
    """True when a CALCULATE (or TOTALxTD filter argument) applies a filter
    that DAX evaluates as a context OVERRIDE on a column (boolean predicate,
    FILTER(ALL(...)), DATESBETWEEN/DATESINPERIOD, time-intelligence date
    filters). The SUM(CASE WHEN ...) translation intersects instead, which is
    only equal when the query does not group/filter by that same column.
    KEEPFILTERS(...) and FILTER(<table>, ...) are true intersections."""
    if parsed_ast is None:
        return False

    def _is_override(f: DaxNode) -> bool:
        if isinstance(f, (BinaryOpNode, UnaryOpNode)):
            return True
        if isinstance(f, FunctionCallNode):
            fn = f.func.upper()
            if fn == "KEEPFILTERS":
                return False
            if fn == "FILTER" and f.args:
                t = f.args[0]
                return isinstance(t, FunctionCallNode) and t.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS", "ALLSELECTED")
            if fn in ("NOT", "ISBLANK", "DATESBETWEEN", "DATESINPERIOD", "DATESYTD", "DATESQTD", "DATESMTD",
                      "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH", "DATEADD",
                      "PARALLELPERIOD", "TOTALYTD", "TOTALQTD", "TOTALMTD"):
                return True
        return False

    stack = [parsed_ast]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if isinstance(n, FunctionCallNode):
            fn = n.func.upper()
            if fn == "CALCULATE" and any(_is_override(a) for a in n.args[1:]):
                return True
            if fn in ("TOTALYTD", "TOTALQTD", "TOTALMTD"):
                return True
            if fn in ("SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSMONTH"):
                return True
        stack.extend(_iter_children(n))
    return False


def is_by_design_excluded(sync_failure_reason: Optional[str]) -> bool:
    """True if `sync_failure_reason` marks a metric as by-design excluded
    (constant expression / string-producing root / non-additive dependency / not applicable)
    rather than a genuine, still-open DAX translation failure."""
    reason_str = str(sync_failure_reason or "").lower()
    return (
        reason_str.startswith(BY_DESIGN_EXCLUDED_PREFIX.lower())
        or "not applicable" in reason_str
        or "depends on non-additive" in reason_str
    )


_STRING_PRODUCING_FUNCS = {
    "CONCATENATE", "LEFT", "RIGHT", "MID", "FORMAT", "SUBSTITUTE", "REPT",
    "TRIM", "UPPER", "LOWER", "EXACT", "FIND", "SEARCH", "REPLACE",
    "CONCATENATEX", "COMBINEVALUES", "UNICHAR", "FIXED",
}


def _is_empty_branch(node: Optional[DaxNode]) -> bool:
    """True if node represents an empty DAX branch: empty string '', BLANK(), or None."""
    if node is None:
        return True
    if isinstance(node, LiteralNode):
        val = node.value
        if isinstance(val, str) and val.strip() == "":
            return True
        if val is None:
            return True
    if isinstance(node, FunctionCallNode) and node.func.upper() == "BLANK":
        return True
    return False


def dax_numeric_literal_value(node_or_dax: Union[DaxNode, str]) -> Optional[str]:
    """If node_or_dax represents only a numeric literal (integer or float, positive or negative),
    returns its string representation. Otherwise returns None.
    Empty expressions, text literals, and booleans return None."""
    if isinstance(node_or_dax, str):
        if not node_or_dax or not node_or_dax.strip():
            return None
        try:
            node = DaxAstParser().parse(node_or_dax)
        except Exception:
            return None
    else:
        node = node_or_dax

    if node is None:
        return None
    node = unwrap_var_body(node)

    if isinstance(node, LiteralNode):
        val = node.value
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return str(val)
    elif isinstance(node, UnaryOpNode) and node.op in ("+", "-"):
        if isinstance(node.operand, LiteralNode):
            val = node.operand.value
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return f"{node.op}{val}"
    return None


def _is_text_producing_node(node: DaxNode) -> bool:
    if isinstance(node, LiteralNode):
        if isinstance(node.value, str):
            # An empty string literal "" is an empty branch/blank in DAX, not a real text result
            return node.value.strip() != ""
        return False
    if isinstance(node, BinaryOpNode):
        if node.op == "&":
            return True
        if node.op in ("+", "-", "*", "/", "^"):
            return False
        if node.op in ("=", "<>", "<", "<=", ">", ">="):
            return False
    if isinstance(node, FunctionCallNode):
        func = node.func.upper()
        if func in _STRING_PRODUCING_FUNCS:
            return True
        if func == "IF" and len(node.args) >= 2:
            then_b = node.args[1]
            else_b = node.args[2] if len(node.args) >= 3 else None
            # Rule A: IF(cond, <numeric>, "") or IF(cond, <numeric>, BLANK()) -> CASE WHEN cond THEN <numeric> END.
            # Only when the non-empty branch is numeric; real text values stay NULL ("text result").
            if _is_empty_branch(else_b):
                return _is_text_producing_node(then_b)
            if _is_empty_branch(then_b):
                return _is_text_producing_node(else_b) if else_b is not None else False
            return _is_text_producing_node(then_b) or _is_text_producing_node(else_b)
        if func == "SWITCH":
            for i in range(2, len(node.args), 2):
                if _is_text_producing_node(node.args[i]):
                    return True
            if len(node.args) % 2 == 0 and len(node.args) > 2:
                if _is_text_producing_node(node.args[-1]):
                    return True
    if isinstance(node, VarNode):
        return _is_text_producing_node(node.body)
    return False


def _refs_any_data(node: DaxNode) -> bool:
    if isinstance(node, (ColumnRefNode, MeasureRefNode)):
        return True
    if isinstance(node, BinaryOpNode):
        return _refs_any_data(node.left) or _refs_any_data(node.right)
    if isinstance(node, UnaryOpNode):
        return _refs_any_data(node.operand)
    if isinstance(node, FunctionCallNode):
        func = node.func.upper()
        if func in ("COUNTROWS", "COUNTAX", "SUMX", "AVERAGEX", "MINX", "MAXX", "SELECTEDVALUE"):
            return True
        return any(_refs_any_data(a) for a in node.args)
    if isinstance(node, VarNode):
        return any(_refs_any_data(v) for _, v in node.variables) or _refs_any_data(node.body)
    return False


def _has_ranking_function(node: DaxNode) -> bool:
    res = False
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in ("RANKX", "ISINSCOPE", "RANK", "DENSE_RANK", "ROWNUMBER"):
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _has_time_intel_function(node: DaxNode) -> bool:
    res = False
    time_funcs = {
        "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER",
        "DATEADD", "PARALLELPERIOD", "TOTALYTD", "TOTALMTD", "TOTALQTD",
    }
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in time_funcs:
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _extract_closing_balance_dim(node: DaxNode) -> Optional[Tuple[Optional[str], str]]:
    pred_node = node
    if isinstance(node, FunctionCallNode) and node.func.upper() == "FILTER" and len(node.args) >= 2:
        pred_node = node.args[1]
    if isinstance(pred_node, BinaryOpNode) and pred_node.op in ("=", "=="):
        if isinstance(pred_node.left, ColumnRefNode) and isinstance(pred_node.right, FunctionCallNode) and pred_node.right.func.upper() == "MAX":
            return (pred_node.left.table, pred_node.left.column)
        if isinstance(pred_node.right, ColumnRefNode) and isinstance(pred_node.left, FunctionCallNode) and pred_node.left.func.upper() == "MAX":
            return (pred_node.right.table, pred_node.right.column)
    return None


def _has_closing_balance(node: DaxNode) -> bool:
    if isinstance(node, FunctionCallNode) and node.func.upper() == "CALCULATE":
        for a in node.args[1:]:
            if _extract_closing_balance_dim(a):
                return True
    return False


def _has_selectedvalue_in_filter_all(node: DaxNode) -> bool:
    res = False
    def _has_selectedvalue(n):
        if n is None:
            return False
        if isinstance(n, FunctionCallNode):
            if n.func.upper() == "SELECTEDVALUE":
                return True
            return any(_has_selectedvalue(a) for a in n.args)
        if isinstance(n, BinaryOpNode):
            return _has_selectedvalue(n.left) or _has_selectedvalue(n.right)
        if isinstance(n, UnaryOpNode):
            return _has_selectedvalue(n.operand)
        return False

    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() == "FILTER" and len(cur.args) >= 2:
                first_arg = cur.args[0]
                is_all_table = False
                if isinstance(first_arg, FunctionCallNode) and first_arg.func.upper() in ("ALL", "ALLEXCEPT", "ALLSELECTED"):
                    is_all_table = True
                if is_all_table and any(_has_selectedvalue(a) for a in cur.args[1:]):
                    res = True
                    return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _get_tables_in_node(node: DaxNode) -> Set[str]:
    tables: Set[str] = set()
    def _walk(cur):
        if cur is None:
            return
        if isinstance(cur, ColumnRefNode) and cur.table:
            tables.add(cur.table.strip("'"))
        for child in getattr(cur, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand"):
            if hasattr(cur, attr) and getattr(cur, attr) is not None:
                _walk(getattr(cur, attr))
        if isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return tables


def _is_selector_over_disconnected_table(node: DaxNode, relationships: Optional[Iterable[Any]]) -> bool:
    if not isinstance(node, FunctionCallNode) or node.func.upper() not in ("IF", "SWITCH"):
        return False
    if not relationships:
        return False
    connected: Set[str] = set()
    for r in relationships:
        ft = getattr(r, "from_dataset", None) or getattr(r, "from_table", None)
        tt = getattr(r, "to_dataset", None) or getattr(r, "to_table", None)
        if ft:
            connected.add(str(ft).casefold())
        if tt:
            connected.add(str(tt).casefold())

    if node.func.upper() == "IF" and len(node.args) >= 2:
        cond_tables = _get_tables_in_node(node.args[0])
        if cond_tables and all(t.casefold() not in connected for t in cond_tables):
            branch_tables: Set[str] = set()
            for b in node.args[1:]:
                branch_tables.update(_get_tables_in_node(b))
            other_tables = [t for t in branch_tables if not any(t.casefold() == ct.casefold() for ct in cond_tables)]
            if other_tables:
                return True
    elif node.func.upper() == "SWITCH" and len(node.args) >= 3:
        cond_tables = _get_tables_in_node(node.args[0])
        if cond_tables and all(t.casefold() not in connected for t in cond_tables):
            branch_tables: Set[str] = set()
            for i in range(2, len(node.args), 2):
                branch_tables.update(_get_tables_in_node(node.args[i]))
            if len(node.args) % 2 == 0 and len(node.args) > 2:
                branch_tables.update(_get_tables_in_node(node.args[-1]))
            other_tables = [t for t in branch_tables if not any(t.casefold() == ct.casefold() for ct in cond_tables)]
            if other_tables:
                return True
    return False


def _get_measure_refs(n: DaxNode) -> Set[str]:
    refs: Set[str] = set()
    def _walk(cur):
        if isinstance(cur, MeasureRefNode):
            refs.add(cur.name)
        for child in getattr(cur, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand"):
            if hasattr(cur, attr) and getattr(cur, attr) is not None:
                _walk(getattr(cur, attr))
        if isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(n)
    return refs


def _is_enrichable_all_measure(node: DaxNode, in_division: bool = False) -> bool:
    try:
        from semabridge.converter.whole_table_all import match_whole_table_all_ast
        if match_whole_table_all_ast(node) is not None:
            return True
    except Exception:
        pass
    if isinstance(node, FunctionCallNode):
        func = node.func.upper()
        if func == "DIVIDE" and len(node.args) >= 2:
            return _is_enrichable_all_measure(node.args[0], in_division=in_division) or _is_enrichable_all_measure(node.args[1], in_division=True)
        if func == "CALCULATE" and len(node.args) >= 2:
            has_all = False
            has_disqualifying_filter = False
            for a in node.args[1:]:
                if isinstance(a, FunctionCallNode) and a.func.upper() in ("ALL", "REMOVEFILTERS", "ALLNOBLANKROW"):
                    if not a.args:
                        has_disqualifying_filter = True
                    elif not in_division:
                        has_col = any(
                            isinstance(arg, (ColumnRefNode, MeasureRefNode))
                            or (isinstance(arg, IdentifierNode) and ("[" in getattr(arg, "name", "") or "." in getattr(arg, "name", "")))
                            for arg in a.args
                        )
                        if not has_col:
                            has_disqualifying_filter = True
                        else:
                            has_all = True
                    else:
                        has_all = True
                elif isinstance(a, (BinaryOpNode, FunctionCallNode)):
                    if not in_division:
                        has_disqualifying_filter = True
                    else:
                        pass
                else:
                    has_disqualifying_filter = True

            if has_all and not has_disqualifying_filter:
                if _has_allselected(node):
                    return False
                if _has_non_decomposable_aggregate(node):
                    return False
                return True
            return False

        return any(_is_enrichable_all_measure(a, in_division=in_division) for a in node.args if _calculate_has_direct_all_or_allexcept_modifier(a))
    if isinstance(node, BinaryOpNode):
        if node.op == "/":
            return _is_enrichable_all_measure(node.left, in_division=in_division) or _is_enrichable_all_measure(node.right, in_division=True)
        left_ok = _is_enrichable_all_measure(node.left, in_division=in_division) if _calculate_has_direct_all_or_allexcept_modifier(node.left) else False
        right_ok = _is_enrichable_all_measure(node.right, in_division=in_division) if _calculate_has_direct_all_or_allexcept_modifier(node.right) else False
        return left_ok or right_ok
    if isinstance(node, UnaryOpNode):
        return _is_enrichable_all_measure(node.operand, in_division=in_division)
    if isinstance(node, VarNode):
        return _is_enrichable_all_measure(node.body, in_division=in_division)
    return False

def _has_non_literal_dateadd(node: DaxNode) -> bool:
    if isinstance(node, FunctionCallNode):
        if node.func.upper() == "DATEADD":
            if len(node.args) >= 2:
                arg1 = node.args[1]
                if isinstance(arg1, UnaryOpNode) and arg1.op == "-":
                    if not isinstance(arg1.operand, LiteralNode):
                        return True
                    try:
                        val = arg1.operand.value
                        if isinstance(val, int) and not isinstance(val, bool):
                            pass
                        elif isinstance(val, str) and re.match(r"^\d+$", val.strip()):
                            pass
                        else:
                            return True
                    except Exception:
                        return True
                elif isinstance(arg1, LiteralNode):
                    try:
                        val = arg1.value
                        if isinstance(val, int) and not isinstance(val, bool):
                            pass
                        elif isinstance(val, str) and re.match(r"^-?\d+$", val.strip()):
                            pass
                        else:
                            return True
                    except Exception:
                        return True
                else:
                    return True
        return any(_has_non_literal_dateadd(a) for a in node.args)
    if isinstance(node, BinaryOpNode):
        return _has_non_literal_dateadd(node.left) or _has_non_literal_dateadd(node.right)
    if isinstance(node, UnaryOpNode):
        return _has_non_literal_dateadd(node.operand)
    if isinstance(node, VarNode):
        return any(_has_non_literal_dateadd(v[1]) for v in node.variables) or _has_non_literal_dateadd(node.body)
    return False

def _has_case_when_or_if(node: DaxNode) -> bool:
    res = False
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in ("IF", "SWITCH"):
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def _has_window_construct(node: DaxNode) -> bool:
    res = False
    def _walk(cur):
        nonlocal res
        if res or cur is None:
            return
        if isinstance(cur, FunctionCallNode):
            if cur.func.upper() in (
                "RANKX", "RANK", "DENSE_RANK", "ROWNUMBER", "ISINSCOPE",
                "ALLSELECTED", "WINDOW", "OFFSET", "INDEX"
            ):
                res = True
                return
            for a in cur.args:
                _walk(a)
        elif isinstance(cur, BinaryOpNode):
            _walk(cur.left)
            _walk(cur.right)
        elif isinstance(cur, UnaryOpNode):
            _walk(cur.operand)
        elif isinstance(cur, VarNode):
            for _, v in cur.variables:
                _walk(v)
            _walk(cur.body)
    _walk(node)
    return res


def classify_dax_ast_not_applicable(
    parsed_ast: DaxNode,
    relationships: Optional[Iterable[Any]] = None,
    non_additive_map: Optional[Dict[str, Any]] = None,
    metrics_context: Optional[Iterable[Any]] = None,
    allow_window_enrichment: bool = False,
) -> Optional[str]:
    """Classify unrepresentable DAX patterns from AST and model metadata before SQL rendering:
    1. text result -> 'not applicable: text result'
    2. constant -> 'not applicable: constant' (numeric literals translated as MAX(<literal>))
    3. ranking -> 'not applicable: needs window metric (ranking)'
    4. window metric -> 'not applicable: needs window metric'
    5. non-additive closing balance + time intelligence -> 'not applicable: depends on non-additive metric'
    5. non-additive dependency -> 'not applicable: depends on non-additive metric [X]'
    6. disconnected selector -> 'not applicable: selector over a disconnected table'
    """
    if parsed_ast is None:
        return None
    dax_str = str(parsed_ast).upper()
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in dax_str or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in dax_str:
        return None

    if _has_non_literal_dateadd(parsed_ast):
        return "not applicable: DATEADD shift amount must be a literal integer"

    node = unwrap_var_body(parsed_ast)

    # 1. Text result
    if _is_text_producing_node(node):
        return "not applicable: text result"

    # 2. Compile-time constant (Rule B: numeric literal -> constant metric MAX(<literal>))
    if not _refs_any_data(parsed_ast):
        if dax_numeric_literal_value(parsed_ast) is not None:
            pass
        else:
            return "not applicable: constant"

    # CASE WHEN / IF over a window metric (Snowflake error 010258 / 010273)
    if _has_case_when_or_if(parsed_ast) and _has_window_construct(parsed_ast):
        return "not applicable: CASE WHEN over a window metric is not supported in Snowflake semantic views"

    # 3. Ranking functions
    if _has_ranking_function(parsed_ast):
        return "not applicable: needs window metric (ranking)"

    # 4. Window metric & report selection
    if _has_allselected(parsed_ast):
        return "not applicable: depends on report selection (ALLSELECTED)"
    if _has_selectedvalue_in_filter_all(parsed_ast):
        if not _is_leave_one_out(parsed_ast) or _has_non_decomposable_aggregate(parsed_ast):
            return "not applicable: needs window metric"
    if _calculate_has_direct_all_or_allexcept_modifier(parsed_ast):
        if _is_enrichable_all_measure(parsed_ast):
            pass
        elif _is_leave_one_out(parsed_ast):
            if _has_non_decomposable_aggregate(parsed_ast):
                return "not applicable: not decomposable for a window"
        elif _calculate_has_allexcept_modifier(parsed_ast):
            if _has_non_decomposable_aggregate(parsed_ast):
                return "not applicable: not decomposable for a window"
        elif _has_non_decomposable_aggregate(parsed_ast):
            return "not applicable: not decomposable for a window"
        else:
            return "not applicable: not decomposable for a window"

    # 5. Non-additive closing balance + time intelligence
    if _has_closing_balance(parsed_ast) and _has_time_intel_function(parsed_ast):
        return "not applicable: depends on non-additive metric"

    # 5. Non-additive dependencies
    if non_additive_map:
        all_refs = _get_measure_refs(parsed_ast)
        non_add_refs = [
            m for m in all_refs
            if any(m.casefold() == k.casefold() for k in non_additive_map)
        ]
        if non_add_refs:
            first_ref = next((m for m in non_add_refs if non_additive_map.get(m) and non_additive_map[m][0]), non_add_refs[0])
            # CASE WHEN over a semi-additive metric triggers Snowflake compilation error 010902
            if _has_case_when_or_if(parsed_ast):
                return f"not applicable: CASE WHEN over a semi-additive metric is not supported in Snowflake semantic views [{first_ref}]"

            is_pure_filtered = False
            if isinstance(node, MeasureRefNode):
                is_pure_filtered = True
            elif (
                isinstance(node, FunctionCallNode)
                and node.func.upper() == "CALCULATE"
                and len(node.args) >= 1
                and isinstance(node.args[0], MeasureRefNode)
            ):
                filter_refs: Set[str] = set()
                has_time_intel = False
                for f_arg in node.args[1:]:
                    filter_refs.update(_get_measure_refs(f_arg))
                    if _has_time_intel_function(f_arg):
                        has_time_intel = True
                if not filter_refs and not has_time_intel:
                    is_pure_filtered = True

            if not is_pure_filtered:
                return f"not applicable: depends on non-additive metric [{first_ref}]"

    # 6. Disconnected selector
    if relationships is not None:
        if _is_selector_over_disconnected_table(node, relationships):
            return "not applicable: selector over a disconnected table"

    return None


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

    node = unwrap_var_body(DaxAstParser().parse(dax))

    if isinstance(node, LiteralNode):

        return isinstance(node.value, str) and node.value.strip() != ""

    if isinstance(node, FunctionCallNode):

        if node.func.upper() == "IF" and len(node.args) >= 2:
            then_b = node.args[1]
            else_b = node.args[2] if len(node.args) >= 3 else None
            if _is_empty_branch(else_b):
                return _is_text_producing_node(then_b)
            if _is_empty_branch(then_b):
                return _is_text_producing_node(else_b) if else_b is not None else False
            return _is_text_producing_node(then_b) or _is_text_producing_node(else_b)

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

    # Rule B: A measure whose DAX is only a numeric literal is translated as a constant metric MAX(<literal>)
    if dax_numeric_literal_value(dax) is not None:
        return False

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

            if n.func.upper() in ("COUNTROWS", "COUNTAX", "SUMX", "AVERAGEX", "MINX", "MAXX"):

                return True

            return any(_refs_data(a) for a in n.args)

        if isinstance(n, VarNode):

            # A VAR is a named sub-expression, not a boundary that hides

            # what it references — a reference inside a variable's own

            # definition (e.g. `VAR _today = [Today]`) is just as much a

            # real data dependency as one in the RETURN body. Check both,

            # for however many variables this block defines.

            return (

                any(_refs_data(value_expr) for _, value_expr in n.variables)

                or _refs_data(n.body)

            )

        return False



    return not _refs_data(node)





_CONTEXT_TRANSITION_REASON = "DAX ALLEXCEPT/ALL requires re-partitioning the aggregate independently of the query"

def _has_allselected(node: DaxNode) -> bool:
    found = False
    def _walk(n: DaxNode):
        nonlocal found
        if found or n is None:
            return
        if isinstance(n, FunctionCallNode) and n.func.upper() == "ALLSELECTED":
            found = True
            return
        for child in getattr(n, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand", "body"):
            if hasattr(n, attr):
                _walk(getattr(n, attr))
    _walk(node)
    return found

def _has_non_decomposable_aggregate(node: DaxNode) -> bool:
    found = False
    def _walk(n: DaxNode):
        nonlocal found
        if found or n is None:
            return
        if isinstance(n, FunctionCallNode) and n.func.upper() in (
            "DISTINCTCOUNT", "DISTINCTCOUNTNOBLANK", "MEDIAN", "MEDIANX", "PERCENTILE"
        ):
            found = True
            return
        for child in getattr(n, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand", "body"):
            if hasattr(n, attr):
                _walk(getattr(n, attr))
    _walk(node)
    return found

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

        if node.func.upper() == "CALCULATE":

            for filter_arg in node.args[1:]:

                if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() in ("ALL", "ALLEXCEPT", "ALLSELECTED", "REMOVEFILTERS", "ALLNOBLANKROW"):

                    return True

        return any(_calculate_has_direct_all_or_allexcept_modifier(a) for a in node.args)

    if isinstance(node, BinaryOpNode):

        return (

            _calculate_has_direct_all_or_allexcept_modifier(node.left)

            or _calculate_has_direct_all_or_allexcept_modifier(node.right)

        )

    if isinstance(node, UnaryOpNode):

        return _calculate_has_direct_all_or_allexcept_modifier(node.operand)

    if isinstance(node, VarNode):

        return any(

            _calculate_has_direct_all_or_allexcept_modifier(value_expr)

            for _, value_expr in node.variables

        ) or _calculate_has_direct_all_or_allexcept_modifier(node.body)

    return False





def _is_leave_one_out(node: DaxNode) -> bool:
    if node is None:
        return False
    found = False
    def _walk(n: DaxNode):
        nonlocal found
        if n is None or found:
            return
        if isinstance(n, FunctionCallNode) and n.func.upper() == "CALCULATE" and len(n.args) >= 2:
            for f_arg in n.args[1:]:
                if isinstance(f_arg, FunctionCallNode) and f_arg.func.upper() == "FILTER" and len(f_arg.args) >= 2:
                    f_tbl = f_arg.args[0]
                    f_pred = f_arg.args[1]
                    if isinstance(f_tbl, FunctionCallNode) and f_tbl.func.upper() in ("ALL", "ALLNOBLANKROW", "REMOVEFILTERS"):
                        if isinstance(f_pred, BinaryOpNode) and f_pred.op in ("<>", "!="):
                            col_node, sv_node = None, None
                            if isinstance(f_pred.left, ColumnRefNode) and isinstance(f_pred.right, FunctionCallNode) and f_pred.right.func.upper() == "SELECTEDVALUE":
                                col_node, sv_node = f_pred.left, f_pred.right
                            elif isinstance(f_pred.right, ColumnRefNode) and isinstance(f_pred.left, FunctionCallNode) and f_pred.left.func.upper() == "SELECTEDVALUE":
                                col_node, sv_node = f_pred.right, f_pred.left
                            if col_node and sv_node and sv_node.args and isinstance(sv_node.args[0], ColumnRefNode):
                                c1 = sanitize_column(col_node.column).upper()
                                c2 = sanitize_column(sv_node.args[0].column).upper()
                                if c1 == c2:
                                    found = True
                                    return
        for child in getattr(n, "args", []) or []:
            _walk(child)
        for attr in ("left", "right", "operand", "body"):
            if hasattr(n, attr):
                _walk(getattr(n, attr))
        if isinstance(n, VarNode):
            for _, ve in n.variables:
                _walk(ve)
    _walk(node)
    return found


def _calculate_has_allexcept_modifier(node: DaxNode) -> bool:
    if node is None:
        return False
    if isinstance(node, FunctionCallNode):
        if node.func.upper() == "CALCULATE":
            for filter_arg in node.args[1:]:
                if isinstance(filter_arg, FunctionCallNode) and filter_arg.func.upper() == "ALLEXCEPT":
                    return True
        return any(_calculate_has_allexcept_modifier(a) for a in node.args)
    if isinstance(node, BinaryOpNode):
        return _calculate_has_allexcept_modifier(node.left) or _calculate_has_allexcept_modifier(node.right)
    if isinstance(node, UnaryOpNode):
        return _calculate_has_allexcept_modifier(node.operand)
    if isinstance(node, VarNode):
        return any(_calculate_has_allexcept_modifier(ve) for _, ve in node.variables) or _calculate_has_allexcept_modifier(node.body)
    return False


def _is_pure_ratio_or_arithmetic_over_metrics(n: DaxNode) -> bool:
    if n is None:
        return False
    if isinstance(n, MeasureRefNode):
        return True
    if isinstance(n, LiteralNode):
        return True
    if isinstance(n, BinaryOpNode) and n.op in ("+", "-", "*", "/"):
        return _is_pure_ratio_or_arithmetic_over_metrics(n.left) and _is_pure_ratio_or_arithmetic_over_metrics(n.right)
    if isinstance(n, FunctionCallNode) and n.func.upper() in ("DIVIDE", "ROUND", "ROUNDUP", "ROUNDDOWN"):
        return all(_is_pure_ratio_or_arithmetic_over_metrics(a) for a in n.args)
    if isinstance(n, UnaryOpNode) and n.op in ("+", "-"):
        return _is_pure_ratio_or_arithmetic_over_metrics(n.operand)
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

    clean_dax_upper = re.sub(r"\s+", " ", str(dax).upper())
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in clean_dax_upper or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in clean_dax_upper:
        return None

    node = DaxAstParser().parse(dax)

    if node is None:

        return None

    if _calculate_has_direct_all_or_allexcept_modifier(node):

        if _is_enrichable_all_measure(node):

            return None

        if _is_leave_one_out(node):
            if _has_non_decomposable_aggregate(node):
                return "not applicable: leave-one-out not decomposable"
            return None

        if _calculate_has_allexcept_modifier(node):
            if _has_non_decomposable_aggregate(node):
                return "not applicable: not decomposable for a window"
            return None

        if _has_allselected(node):
            return "not applicable: depends on report selection (ALLSELECTED)"
        if _has_non_decomposable_aggregate(node):
            return "not applicable: not decomposable for a window"
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

    if isinstance(node, VarNode):

        for _, value_expr in node.variables:

            found = _calculate_lag_period_of_measure_reference(value_expr)

            if found:

                return found

        return _calculate_lag_period_of_measure_reference(node.body)

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

    clean_dax_upper = re.sub(r"\s+", " ", str(dax).upper())
    if "__FIRSTDATEVISIBLE <= __LASTDATEWITHDATA" in clean_dax_upper or "FIRSTDATEVISIBLE <= LASTDATEWITHDATA" in clean_dax_upper:
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

        elif isinstance(n, VarNode):

            for _, value_expr in n.variables:

                _walk(value_expr)

            _walk(n.body)



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

        elif isinstance(n, VarNode):

            for _, value_expr in n.variables:

                _walk(value_expr)

            _walk(n.body)



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

    if isinstance(node, VarNode):

        return any(_node_contains_measure_ref(v) for _, v in node.variables) or _node_contains_measure_ref(node.body)

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

    if isinstance(node, VarNode):

        return all(_node_is_pure_literal(v) for _, v in node.variables) and _node_is_pure_literal(node.body)

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

        elif isinstance(n, VarNode):

            for _, value_expr in n.variables:

                _walk(value_expr)

            _walk(n.body)



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



def dax_time_intelligence_failure_reason(dax: str) -> str:
    return 'time intelligence not supported yet'
