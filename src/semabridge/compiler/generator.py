from __future__ import annotations

from semabridge.compiler.ast import (
    CaseNode,
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


class SqlGenerator:
    def __init__(self, quote_identifiers: bool = True) -> None:
        self.quote_identifiers = quote_identifiers

    def generate(self, node: SqlNode | None) -> str:
        if node is None:
            return ""
        if isinstance(node, SqlRawNode):
            return node.sql
        if isinstance(node, SqlLiteralNode):
            if node.value is None:
                return "NULL"
            if isinstance(node.value, bool):
                return "TRUE" if node.value else "FALSE"
            if isinstance(node.value, str):
                escaped = node.value.replace("'", "''")
                return f"'{escaped}'"
            return str(node.value)
        if isinstance(node, SqlIdentifierNode):
            return self._quote(node.name)
        if isinstance(node, SqlColumnReferenceNode):
            return f'{self._quote(node.table)}.{self._quote(node.column)}'
        if isinstance(node, SqlUnaryOpNode):
            return f"{node.op} ({self.generate(node.operand)})"
        if isinstance(node, SqlBinaryOpNode):
            return f"{self.generate(node.left)} {node.op} {self.generate(node.right)}"
        if isinstance(node, CaseNode):
            parts = [f"WHEN {self.generate(cond)} THEN {self.generate(expr)}" for cond, expr in node.whens]
            else_expr = f" ELSE {self.generate(node.else_expr)}" if node.else_expr is not None else ""
            return f"CASE {' '.join(parts)}{else_expr} END"
        if isinstance(node, SqlAggregateNode):
            expr = self.generate(node.expression)
            if node.filter_condition is not None:
                if node.function == "SUM":
                    expr = f"CASE WHEN {self.generate(node.filter_condition)} THEN {expr} ELSE 0 END"
                elif node.function == "COUNT":
                    expr = f"CASE WHEN {self.generate(node.filter_condition)} THEN {expr} END"
                elif node.function == "COUNT_DISTINCT":
                    expr = f"CASE WHEN {self.generate(node.filter_condition)} THEN {expr} END"
                else:
                    expr = f"CASE WHEN {self.generate(node.filter_condition)} THEN {expr} END"
            if node.function == "COUNT_DISTINCT":
                return f"COUNT(DISTINCT {expr})"
            return f"{node.function}({expr})"
        if isinstance(node, WindowFunctionNode):
            self.validate_window_ast(node)
            expr = self.generate(node.expression)
            over_bits: list[str] = []
            if node.partition_by:
                over_bits.append("PARTITION BY " + ", ".join(self.generate(part) for part in node.partition_by))
            if node.order_by:
                over_bits.append("ORDER BY " + ", ".join(self.generate(order) for order in node.order_by))
            if node.frame_clause:
                over_bits.append(node.frame_clause)
            if node.offset is not None:
                return f"{node.function}({expr}, {node.offset}) OVER ({' '.join(over_bits)})"
            return f"{node.function}({expr}) OVER ({' '.join(over_bits)})"
        if isinstance(node, SqlFunctionNode):
            return f"{node.name}({', '.join(self.generate(arg) for arg in node.args)})"
        raise TypeError(f"Unsupported SQL node: {type(node).__name__}")

    def validate_window_ast(self, node: WindowFunctionNode) -> None:
        if node.expression is None:
            raise ValueError("window function expression is required")
        if not node.order_by:
            raise ValueError("window function order by is required")
        if any(part is None for part in node.partition_by):
            raise ValueError("window function partition by contains null nodes")
        if any(order is None for order in node.order_by):
            raise ValueError("window function order by contains null nodes")

    def _quote(self, identifier: str) -> str:
        clean = str(identifier or "").strip().replace('"', '')
        clean = clean.replace(".", "_")
        if not clean:
            return '""'
        if not self.quote_identifiers:
            return clean.upper()
        return f'"{clean.upper()}"'
