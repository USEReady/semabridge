from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from semabridge.compiler.ast import (
    BinaryOpNode,
    CaseNode,
    DaxNode,
    FunctionCallNode,
    IdentifierNode,
    MeasureReferenceNode,
    SqlAggregateNode,
    SqlBinaryOpNode,
    SqlColumnReferenceNode,
    SqlFunctionNode,
    SqlIdentifierNode,
    SqlLiteralNode,
    SqlNode,
    SqlRawNode,
    SqlUnaryOpNode,
    WindowFunctionNode,
)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SQLValidator:
    unsupported_functions = {
        "CALCULATE", "TOTALYTD", "TOTALMTD", "TOTALQTD", "SAMEPERIODLASTYEAR", "PREVIOUSYEAR",
        "PREVIOUSMONTH", "PREVIOUSQUARTER", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD",
        "PARALLELPERIOD", "OPENINGBALANCEYEAR", "CLOSINGBALANCEYEAR", "FILTER", "ALL",
        "IF", "ISBLANK", "CONCATENATE", "COUNTBLANK",
    }

    _allowed_keywords = {
        "SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING", "AS", "AND", "OR",
        "NOT", "NULL", "TRUE", "FALSE", "CASE", "WHEN", "THEN", "ELSE", "END", "OVER",
        "PARTITION", "ROWS", "RANGE", "BETWEEN", "UNBOUNDED", "PRECEDING", "CURRENT", "ROW",
        "DISTINCT", "ON", "IN", "IS", "LIKE", "ILIKE", "COUNT", "SUM", "AVG", "MIN", "MAX",
        "COUNT_IF", "COUNT_DISTINCT", "CONCAT", "COALESCE", "LAG", "LEAD", "EXISTS", "CAST",
        "DATEADD", "DATEDIFF", "CURRENT_DATE", "CURRENT_TIMESTAMP",
    }

    def validate(self, sql: str) -> ValidationResult:
        return validate_sql_expression(sql)

    def validate_ast(self, ast_node: SqlNode | None) -> ValidationResult:
        return validate_ast(ast_node)


def validate_sql_expression(sql: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not sql or not str(sql).strip():
        errors.append("empty sql")
        return ValidationResult(False, errors, warnings)

    text = str(sql)
    upper = text.upper()
    if " AS NULL" in upper:
        errors.append("AS NULL expression is forbidden")
    if re.search(r"\[[^\]]+\]", text):
        errors.append("unresolved symbolic reference")
    if any(f"{func}(" in upper for func in SQLValidator.unsupported_functions):
        errors.append("raw DAX leakage detected")
    if upper.count("(") != upper.count(")"):
        errors.append("unbalanced parentheses")
    if re.search(r"\bCASE\b", upper) and upper.count("CASE") > upper.count("END"):
        errors.append("malformed CASE expression")
    if re.search(r"\bOVER\s*\(\s*\)", upper) or re.search(r"\bOVER\s*\(\s*(PARTITION BY|ORDER BY)?\s*\)", upper):
        errors.append("malformed window syntax")
    if re.search(r"\b[A-Z][A-Z0-9_]*\[[^\]]+\]\[[^\]]+\]", text):
        errors.append("invalid graph path notation")
    if re.search(r"\b[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*\b", upper):
        errors.append("invalid graph path notation")
    if re.search(r"\b[A-Z_][A-Z0-9_]*\[[^\]]+\]", text):
        errors.append("invalid identifier shape")
    if re.search(r"\b[A-Z_][A-Z0-9_]*\b", upper):
        tokens = {
            token
            for token in re.findall(r"\b[A-Z_][A-Z0-9_]*\b", upper)
            if token not in SQLValidator._allowed_keywords and token not in SQLValidator.unsupported_functions
        }
        if any(token.endswith("_R12MS") or token.startswith("TOTAL_") for token in tokens):
            errors.append("unresolved metric identifier")

    duplicate_aliases = re.findall(r'\bAS\s+"([^"]+)"', text, flags=re.IGNORECASE)
    if len(duplicate_aliases) != len({alias.casefold() for alias in duplicate_aliases}):
        errors.append("duplicate aliases")

    return ValidationResult(not errors, errors, warnings)


def validate_ast(ast_node: SqlNode | None) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    def visit(node: Any, *, in_case: int = 0, in_window: int = 0) -> None:
        if node is None:
            errors.append("null ast node")
            return

        if isinstance(node, SqlRawNode):
            raw = str(node.sql or "").strip()
            if raw == "*":
                return
            if not raw:
                errors.append("empty raw sql node")
                return
            if any(f"{func}(" in raw.upper() for func in SQLValidator.unsupported_functions):
                errors.append("raw DAX leakage detected in AST")
            else:
                errors.append("raw sql passthrough is not allowed")
            return

        if isinstance(node, (SqlLiteralNode, SqlIdentifierNode)):
            return

        if isinstance(node, SqlColumnReferenceNode):
            if not node.table or not node.column:
                errors.append("invalid column reference")
            return

        if isinstance(node, SqlBinaryOpNode):
            visit(node.left, in_case=in_case, in_window=in_window)
            visit(node.right, in_case=in_case, in_window=in_window)
            return

        if isinstance(node, SqlUnaryOpNode):
            visit(node.operand, in_case=in_case, in_window=in_window)
            return

        if isinstance(node, SqlFunctionNode):
            if any(f"{func}(" in node.name.upper() for func in SQLValidator.unsupported_functions):
                errors.append(f"raw DAX leakage detected in function call: {node.name}")
            for arg in node.args:
                visit(arg, in_case=in_case, in_window=in_window)
            return

        if isinstance(node, SqlAggregateNode):
            visit(node.expression, in_case=in_case, in_window=in_window)
            if node.filter_condition is not None:
                visit(node.filter_condition, in_case=in_case, in_window=in_window)
            return

        if isinstance(node, CaseNode):
            for cond, expr in node.whens:
                visit(cond, in_case=in_case + 1, in_window=in_window)
                visit(expr, in_case=in_case + 1, in_window=in_window)
            if node.else_expr is None:
                errors.append("case expression missing else branch")
            else:
                visit(node.else_expr, in_case=in_case + 1, in_window=in_window)
            return

        if isinstance(node, WindowFunctionNode):
            if node.expression is None:
                errors.append("window function expression is required")
            if not node.order_by:
                errors.append("window function order by is required")
            if in_window > 0:
                errors.append("nested window function is not allowed")
            visit(node.expression, in_case=in_case, in_window=in_window + 1)
            for part in node.partition_by:
                visit(part, in_case=in_case, in_window=in_window + 1)
            for order in node.order_by:
                visit(order, in_case=in_case, in_window=in_window + 1)
            return

        if isinstance(node, (BinaryOpNode, CaseNode, FunctionCallNode, DaxNode, MeasureReferenceNode, IdentifierNode)):
            errors.append(f"unexpected dax node in sql ast: {type(node).__name__}")
            return

        errors.append(f"unsupported ast node: {type(node).__name__}")

    visit(ast_node)
    return ValidationResult(not errors, errors, warnings)
