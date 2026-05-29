from __future__ import annotations

from typing import List, Optional

from semabridge.compiler.ast import (
    AggregateNode,
    BinaryOpNode,
    CalculateNode,
    ColumnReferenceNode,
    DivideNode,
    DaxNode,
    FunctionCallNode,
    IdentifierNode,
    IfNode,
    LiteralNode,
    MeasureReferenceNode,
    TimeIntelligenceNode,
    UnaryOpNode,
)
from semabridge.compiler.tokens import DaxLexer, Token, TokenType


class DaxParseError(Exception):
    pass


_AGGREGATES = {"SUM", "AVERAGE", "AVG", "MIN", "MAX", "COUNT", "COUNTA", "COUNTROWS", "DISTINCTCOUNT", "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX"}
_TIME_INTEL = {"TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD", "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR"}


class DaxParser:
    def __init__(self) -> None:
        self.tokens: List[Token] = []
        self.pos = 0

    def parse(self, dax: str) -> Optional[DaxNode]:
        if not dax or not dax.strip():
            return None
        lexer = DaxLexer(dax)
        self.tokens = lexer.tokenize()
        self.pos = 0
        node = self._parse_expr()
        return node

    def _peek(self) -> Token:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else Token(TokenType.EOF, "")

    def _advance(self) -> Token:
        token = self._peek()
        self.pos += 1
        return token

    def _match(self, *kinds: TokenType) -> bool:
        return self._peek().kind in kinds

    def _expect(self, kind: TokenType) -> Token:
        token = self._peek()
        if token.kind != kind:
            raise DaxParseError(f"Expected {kind}, got {token.kind}:{token.value}")
        return self._advance()

    def _parse_expr(self) -> DaxNode:
        return self._parse_or()

    def _parse_or(self) -> DaxNode:
        node = self._parse_and()
        while self._match(TokenType.KEYWORD) and self._peek().value == "OR":
            self._advance()
            node = BinaryOpNode(op="OR", left=node, right=self._parse_and())
        return node

    def _parse_and(self) -> DaxNode:
        node = self._parse_comparison()
        while self._match(TokenType.KEYWORD) and self._peek().value == "AND":
            self._advance()
            node = BinaryOpNode(op="AND", left=node, right=self._parse_comparison())
        return node

    def _parse_comparison(self) -> DaxNode:
        node = self._parse_addition()
        while self._match(TokenType.OPERATOR) and self._peek().value in {"=", "<>", "<", "<=", ">", ">="}:
            op = self._advance().value
            node = BinaryOpNode(op=op, left=node, right=self._parse_addition())
        return node

    def _parse_addition(self) -> DaxNode:
        node = self._parse_multiplication()
        while self._match(TokenType.OPERATOR) and self._peek().value in {"+", "-"}:
            op = self._advance().value
            node = BinaryOpNode(op=op, left=node, right=self._parse_multiplication())
        return node

    def _parse_multiplication(self) -> DaxNode:
        node = self._parse_unary()
        while self._match(TokenType.OPERATOR) and self._peek().value in {"*", "/"}:
            op = self._advance().value
            node = BinaryOpNode(op=op, left=node, right=self._parse_unary())
        return node

    def _parse_unary(self) -> DaxNode:
        if (self._match(TokenType.KEYWORD) and self._peek().value == "NOT") or (self._match(TokenType.OPERATOR) and self._peek().value == "-"):
            token = self._advance()
            return UnaryOpNode(op=token.value, operand=self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> DaxNode:
        token = self._peek()
        if token.kind == TokenType.LPAREN:
            self._advance()
            node = self._parse_expr()
            self._expect(TokenType.RPAREN)
            return node
        if token.kind == TokenType.NUMBER:
            self._advance()
            value = float(token.value) if "." in token.value else int(token.value)
            return LiteralNode(value=value, raw=token.value)
        if token.kind == TokenType.STRING:
            self._advance()
            return LiteralNode(value=token.value, raw=token.value)
        if token.kind == TokenType.KEYWORD:
            self._advance()
            if token.value == "TRUE":
                return LiteralNode(value=True, raw=token.value)
            if token.value == "FALSE":
                return LiteralNode(value=False, raw=token.value)
            if token.value == "BLANK":
                return LiteralNode(value=None, raw=token.value)
            return IdentifierNode(name=token.value)
        if token.kind == TokenType.MEASURE:
            self._advance()
            return MeasureReferenceNode(name=token.value)
        if token.kind == TokenType.TABLE:
            self._advance()
            if self._match(TokenType.COLUMN):
                column = self._advance().value
                return ColumnReferenceNode(table=token.value, column=column, raw=f"{token.value}[{column}]")
            return IdentifierNode(name=token.value)
        if token.kind in {TokenType.FUNCTION, TokenType.IDENTIFIER}:
            return self._parse_function_or_identifier()
        if token.kind == TokenType.EOF:
            raise DaxParseError("Unexpected end of expression")
        self._advance()
        return IdentifierNode(name=token.value)

    def _parse_function_or_identifier(self) -> DaxNode:
        token = self._advance()
        name = token.value
        if not self._match(TokenType.LPAREN):
            return IdentifierNode(name=name)
        self._advance()
        args: list[DaxNode] = []
        while not self._match(TokenType.RPAREN, TokenType.EOF):
            args.append(self._parse_expr())
            if self._match(TokenType.COMMA):
                self._advance()
        self._expect(TokenType.RPAREN)
        upper = name.upper()
        node_cls = {
            "CALCULATE": CalculateNode,
            "DIVIDE": DivideNode,
            "IF": IfNode,
        }.get(upper)
        if upper in _TIME_INTEL:
            return TimeIntelligenceNode(func=upper, args=args)
        if upper in _AGGREGATES:
            return AggregateNode(func=upper, args=args)
        if node_cls:
            return node_cls(func=upper, args=args)
        return FunctionCallNode(func=upper, args=args)
