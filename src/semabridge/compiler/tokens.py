from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class TokenType(Enum):
    IDENTIFIER = auto()
    FUNCTION = auto()
    TABLE = auto()
    COLUMN = auto()
    MEASURE = auto()
    NUMBER = auto()
    STRING = auto()
    OPERATOR = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    DOT = auto()
    KEYWORD = auto()
    EOF = auto()


@dataclass
class Token:
    kind: TokenType
    value: str
    pos: int = 0


_FUNCTION_NAMES = {
    "SUM", "AVERAGE", "AVG", "MIN", "MAX", "COUNT", "COUNTA", "COUNTROWS", "DISTINCTCOUNT",
    "SUMX", "AVERAGEX", "MINX", "MAXX", "COUNTX",
    "CALCULATE", "CALCULATETABLE", "FILTER", "ALL", "ALLEXCEPT", "ALLSELECTED",
    "IF", "SWITCH", "IFERROR", "DIVIDE", "ISBLANK", "CONCATENATE",
    "TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", "PREVIOUSYEAR", "PREVIOUSMONTH",
    "PREVIOUSQUARTER", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
    "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR",
}


class DaxLexer:
    def __init__(self, text: str) -> None:
        self.text = text or ""
        self.pos = 0

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        while self.pos < len(self.text):
            self._skip_ws()
            if self.pos >= len(self.text):
                break
            ch = self.text[self.pos]
            if ch == "'":
                tokens.extend(self._read_table_reference(quoted=True))
            elif ch == "[":
                tokens.append(self._read_measure())
            elif ch == '"':
                tokens.append(self._read_string())
            elif ch.isdigit() or (ch == "-" and self._peek_digit()):
                tokens.append(self._read_number())
            elif ch.isalpha() or ch == "_":
                tokens.extend(self._read_identifier_or_table())
            else:
                tokens.append(self._read_operator())
        tokens.append(Token(TokenType.EOF, "", self.pos))
        return tokens

    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1

    def _peek_digit(self) -> bool:
        nxt = self.pos + 1
        return nxt < len(self.text) and self.text[nxt].isdigit()

    def _peek_non_ws(self) -> str:
        idx = self.pos
        while idx < len(self.text) and self.text[idx].isspace():
            idx += 1
        return self.text[idx] if idx < len(self.text) else ""

    def _read_until(self, terminator: str) -> str:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] != terminator:
            self.pos += 1
        value = self.text[start:self.pos]
        if self.pos < len(self.text) and self.text[self.pos] == terminator:
            self.pos += 1
        return value

    def _read_string(self) -> Token:
        start = self.pos
        self.pos += 1
        value = self._read_until('"')
        return Token(TokenType.STRING, value, start)

    def _read_number(self) -> Token:
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        while self.pos < len(self.text) and (self.text[self.pos].isdigit() or self.text[self.pos] == "."):
            self.pos += 1
        return Token(TokenType.NUMBER, self.text[start:self.pos], start)

    def _read_measure(self) -> Token:
        start = self.pos
        self.pos += 1
        value = self._read_until("]")
        return Token(TokenType.MEASURE, value.strip(), start)

    def _read_table_reference(self, quoted: bool) -> List[Token]:
        start = self.pos
        if quoted:
            self.pos += 1
            table_name = self._read_until("'")
        else:
            table_start = self.pos
            while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_#@ -/"):
                self.pos += 1
            table_name = self.text[table_start:self.pos].strip()

        tokens = [Token(TokenType.TABLE, table_name, start)]
        self._skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "[":
            self.pos += 1
            column_name = self._read_until("]")
            tokens.append(Token(TokenType.COLUMN, column_name.strip(), self.pos))
        return tokens

    def _read_identifier_or_table(self) -> List[Token]:
        start = self.pos
        while self.pos < len(self.text) and (self.text[self.pos].isalnum() or self.text[self.pos] in "_#@ /-"):
            self.pos += 1
        word = self.text[start:self.pos].strip()
        upper = word.upper()
        self._skip_ws()
        if self.pos < len(self.text) and self.text[self.pos] == "(":
            return [Token(TokenType.FUNCTION, upper, start)]
        if self.pos < len(self.text) and self.text[self.pos] == ".":
            return [Token(TokenType.IDENTIFIER, word, start), Token(TokenType.DOT, ".", self.pos)]
        if self.pos < len(self.text) and self.text[self.pos] == "[":
            tokens = [Token(TokenType.TABLE, word, start)]
            self.pos += 1
            column_name = self._read_until("]")
            tokens.append(Token(TokenType.COLUMN, column_name.strip(), self.pos))
            return tokens
        if upper in {"AND", "OR", "NOT", "TRUE", "FALSE", "BLANK"}:
            return [Token(TokenType.KEYWORD, upper, start)]
        if upper in _FUNCTION_NAMES:
            return [Token(TokenType.FUNCTION, upper, start)]
        return [Token(TokenType.IDENTIFIER, word, start)]

    def _read_operator(self) -> Token:
        start = self.pos
        ch = self.text[self.pos]
        nxt = self.text[self.pos + 1] if self.pos + 1 < len(self.text) else ""
        two = ch + nxt
        if two in {"<=", ">=", "<>"}:
            self.pos += 2
            return Token(TokenType.OPERATOR, two, start)
        if ch in "+-*/=<>":
            self.pos += 1
            return Token(TokenType.OPERATOR, ch, start)
        if ch == "(":
            self.pos += 1
            return Token(TokenType.LPAREN, ch, start)
        if ch == ")":
            self.pos += 1
            return Token(TokenType.RPAREN, ch, start)
        if ch == ",":
            self.pos += 1
            return Token(TokenType.COMMA, ch, start)
        if ch == ".":
            self.pos += 1
            return Token(TokenType.DOT, ch, start)
        self.pos += 1
        return Token(TokenType.OPERATOR, ch, start)
