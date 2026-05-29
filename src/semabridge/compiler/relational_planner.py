from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from semabridge.compiler.ast import CaseNode, SqlAggregateNode, SqlBinaryOpNode, SqlFunctionNode, SqlNode, SqlUnaryOpNode, WindowFunctionNode


@dataclass
class AggregateNode:
    group_by: list[SqlNode] = field(default_factory=list)
    aggregates: list[SqlNode] = field(default_factory=list)


@dataclass
class WindowNode:
    source: Optional[SqlNode] = None
    windows: list[WindowFunctionNode] = field(default_factory=list)


@dataclass
class FilterNode:
    source: Optional[SqlNode] = None
    predicate: Optional[SqlNode] = None


@dataclass
class ProjectionNode:
    source: Optional[SqlNode] = None
    expressions: list[SqlNode] = field(default_factory=list)


@dataclass
class JoinNode:
    left: Optional[SqlNode] = None
    right: Optional[SqlNode] = None
    join_type: str = "INNER"
    condition: Optional[SqlNode] = None


class RelationalPlanner:
    def __init__(self) -> None:
        self.last_plan: Any = None

    def plan(self, sql_ast: SqlNode | None) -> SqlNode | None:
        if sql_ast is None:
            self.last_plan = None
            return None
        self._reject_nested_window(sql_ast)
        self.last_plan = ProjectionNode(source=sql_ast, expressions=[sql_ast])
        return sql_ast

    def _reject_nested_window(self, node: SqlNode) -> None:
        if isinstance(node, SqlAggregateNode):
            if self._contains_window(node.expression):
                raise ValueError("nested OVER inside aggregate is not allowed")
            if self._contains_window(node.filter_condition):
                raise ValueError("nested OVER inside aggregate filter is not allowed")
        if isinstance(node, CaseNode):
            for cond, expr in node.whens:
                self._reject_nested_window(cond)
                self._reject_nested_window(expr)
            if node.else_expr is not None:
                self._reject_nested_window(node.else_expr)
        if isinstance(node, SqlBinaryOpNode):
            if node.left is not None:
                self._reject_nested_window(node.left)
            if node.right is not None:
                self._reject_nested_window(node.right)
        if isinstance(node, SqlUnaryOpNode):
            if node.operand is not None:
                self._reject_nested_window(node.operand)
        if isinstance(node, SqlFunctionNode):
            for arg in node.args:
                self._reject_nested_window(arg)

    def _contains_window(self, node: SqlNode | None) -> bool:
        if node is None:
            return False
        if isinstance(node, WindowFunctionNode):
            return True
        if isinstance(node, CaseNode):
            return any(self._contains_window(cond) or self._contains_window(expr) for cond, expr in node.whens) or self._contains_window(node.else_expr)
        if isinstance(node, SqlBinaryOpNode):
            return self._contains_window(node.left) or self._contains_window(node.right)
        if isinstance(node, SqlUnaryOpNode):
            return self._contains_window(node.operand)
        if isinstance(node, SqlAggregateNode):
            return self._contains_window(node.expression) or self._contains_window(node.filter_condition)
        if isinstance(node, SqlFunctionNode):
            return any(self._contains_window(arg) for arg in node.args)
        return False
