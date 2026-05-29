from __future__ import annotations

from semabridge.compiler.ast import CalculateNode, ColumnReferenceNode, FunctionCallNode, MeasureReferenceNode
from semabridge.compiler.parser import DaxParser
from semabridge.compiler.tokens import DaxLexer, TokenType


def test_lexer_tokenizes_calculate_expression() -> None:
    tokens = DaxLexer('CALCULATE([Revenue], Region[Country] = "US")').tokenize()
    kinds = [token.kind for token in tokens]

    assert kinds[:9] == [
        TokenType.FUNCTION,
        TokenType.LPAREN,
        TokenType.MEASURE,
        TokenType.COMMA,
        TokenType.TABLE,
        TokenType.COLUMN,
        TokenType.OPERATOR,
        TokenType.STRING,
        TokenType.RPAREN,
    ]


def test_parser_builds_nested_ast() -> None:
    node = DaxParser().parse('CALCULATE([Revenue], Region[Country] = "US")')

    assert isinstance(node, CalculateNode)
    assert isinstance(node.args[0], MeasureReferenceNode)
    assert isinstance(node.args[1], FunctionCallNode) or hasattr(node.args[1], "op")
    assert isinstance(node.args[1].left, ColumnReferenceNode)
    assert node.args[1].left.table == "Region"
    assert node.args[1].left.column == "Country"