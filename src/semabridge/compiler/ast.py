from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


@dataclass
class Node:
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DaxNode(Node):
    pass


@dataclass
class SqlNode(Node):
    pass


@dataclass
class LiteralNode(DaxNode):
    value: Any = None
    raw: str = ""


@dataclass
class MeasureReferenceNode(DaxNode):
    name: str = ""


@dataclass
class ColumnReferenceNode(DaxNode):
    table: str = ""
    column: str = ""
    raw: str = ""


@dataclass
class IdentifierNode(DaxNode):
    name: str = ""


@dataclass
class FunctionCallNode(DaxNode):
    func: str = ""
    args: list[DaxNode] = field(default_factory=list)


@dataclass
class BinaryOpNode(DaxNode):
    op: str = ""
    left: DaxNode | None = None
    right: DaxNode | None = None


@dataclass
class UnaryOpNode(DaxNode):
    op: str = ""
    operand: DaxNode | None = None


@dataclass
class CalculateNode(FunctionCallNode):
    pass


@dataclass
class DivideNode(FunctionCallNode):
    pass


@dataclass
class IfNode(FunctionCallNode):
    pass


@dataclass
class TimeIntelligenceNode(FunctionCallNode):
    pass


@dataclass
class AggregateNode(FunctionCallNode):
    pass


@dataclass
class CaseNode(SqlNode):
    whens: list[tuple[SqlNode, SqlNode]] = field(default_factory=list)
    else_expr: Optional[SqlNode] = None


@dataclass
class WindowFunctionNode(SqlNode):
    function: str = ""
    expression: Optional[SqlNode] = None
    partition_by: list[SqlNode] = field(default_factory=list)
    order_by: list[SqlNode] = field(default_factory=list)
    frame_clause: Optional[str] = None
    offset: Optional[int] = None


@dataclass
class SqlAggregateNode(SqlNode):
    function: str = ""
    expression: Optional[SqlNode] = None
    distinct: bool = False
    filter_condition: Optional[SqlNode] = None


@dataclass
class SqlBinaryOpNode(SqlNode):
    op: str = ""
    left: SqlNode | None = None
    right: SqlNode | None = None


@dataclass
class SqlUnaryOpNode(SqlNode):
    op: str = ""
    operand: SqlNode | None = None


@dataclass
class SqlLiteralNode(SqlNode):
    value: Any = None


@dataclass
class SqlIdentifierNode(SqlNode):
    name: str = ""


@dataclass
class SqlColumnReferenceNode(SqlNode):
    table: str = ""
    column: str = ""


@dataclass
class SqlFunctionNode(SqlNode):
    name: str = ""
    args: list[SqlNode] = field(default_factory=list)


@dataclass
class SqlRawNode(SqlNode):
    sql: str = ""


def clone_metadata(source: Node, target: Node) -> Node:
    target.metadata = dict(getattr(source, "metadata", {}) or {})
    return target
