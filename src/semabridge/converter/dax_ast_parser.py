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
# Facade: the renderer and the feasibility classifiers live in
# semabridge/converter/dax/ (split for size); every name stays importable
# from this module. Imported last because they use the names above.
# ---------------------------------------------------------------------------
from semabridge.converter.dax import feasibility as _feasibility  # noqa: E402
from semabridge.converter.dax import sql_renderer as _sql_renderer  # noqa: E402

for _name in ['ADVISORY_CATEGORY_DISCONNECTED_SELECTOR_DECOMPOSED', 'ADVISORY_CATEGORY_PENDING_LIVE_SCHEMA_ENRICHMENT', 'ADVISORY_CATEGORY_UNREACHABLE_DIMENSION', 'BY_DESIGN_EXCLUDED_PREFIX', 'DecomposedSelectorBranch', 'DisconnectedSelectorDecomposition', 'ReachableDimensionFilterPendingEnrichment', 'UnreachableDimensionFilter', '_CONTEXT_TRANSITION_REASON', '_GROUP_GRAIN_TABLE_FUNCS', '_ITERATOR_FUNCS', '_LAG_PERIOD_FUNCS', '_SCOPE_GUARD_FUNCS', '_STRING_PRODUCING_FUNCS', '_ast_contains_func', '_calculate_has_allexcept_modifier', '_calculate_has_direct_all_or_allexcept_modifier', '_calculate_lag_period_of_measure_reference', '_collect_column_ref_tables', '_collect_measure_ref_names', '_dax_text', '_extract_closing_balance_dim', '_find_all_calculate_nodes', '_get_measure_refs', '_get_tables_in_node', '_has_allselected', '_has_case_when_or_if', '_has_closing_balance', '_has_non_decomposable_aggregate', '_has_non_literal_dateadd', '_has_ranking_function', '_has_selectedvalue_in_filter_all', '_has_time_intel_function', '_has_treatas', '_has_window_construct', '_is_empty_branch', '_is_enrichable_all_measure', '_is_leave_one_out', '_is_pure_ratio_or_arithmetic_over_metrics', '_is_selector_over_disconnected_table', '_is_text_producing_node', '_iter_children', '_node_contains_measure_ref', '_node_is_pure_literal', '_refs_any_data', '_resolve_single_branch_dataset', '_walk_if_chain', 'classify_dax_ast_not_applicable', 'dax_calculate_filters_reachable_dimension_pending_enrichment', 'dax_calculate_filters_unreachable_dimension', 'dax_context_transition_failure_reason', 'dax_has_overriding_calculate_filter', 'dax_has_zero_data_dependencies', 'dax_lag_period_of_measure_reference_failure_reason', 'dax_numeric_literal_value', 'dax_root_is_string_producing', 'dax_semantic_fidelity_failure_reason', 'dax_time_intelligence_failure_reason', 'is_by_design_excluded', 'try_decompose_disconnected_selector_metric']:
    if hasattr(_feasibility, _name):  # absent only mid-import; the module back-patches it
        globals()[_name] = getattr(_feasibility, _name)
if hasattr(_sql_renderer, "DaxSqlRenderer"):
    DaxSqlRenderer = _sql_renderer.DaxSqlRenderer
del _name
