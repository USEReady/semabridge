"""Read-only adapter to the shared DAX parser.

The planner reads each measure's DAX through the same parser the converter
uses, so both see the same structure. This is the only planner module that
imports the converter; it never changes converter behaviour (Snowflake
output stays byte-identical).
"""

from __future__ import annotations

from typing import Iterator, List, Optional

from semabridge.converter.dax_ast_parser import (  # noqa: F401  (re-exported node types)
    BinaryOpNode,
    ColumnRefNode,
    DaxAstParser,
    DaxNode,
    FunctionCallNode,
    IdentifierNode,
    LiteralNode,
    MeasureRefNode,
    UnaryOpNode,
    VarNode,
)


def parse(dax: Optional[str]) -> Optional[DaxNode]:
    """The DAX tree, or None when the text is empty or does not parse."""
    if not dax or not str(dax).strip():
        return None
    try:
        return DaxAstParser().parse(str(dax))
    except Exception:  # the parser raises its own errors on unsupported text
        return None


def children(node: DaxNode) -> List[DaxNode]:
    if isinstance(node, FunctionCallNode):
        return list(node.args)
    if isinstance(node, BinaryOpNode):
        return [node.left, node.right]
    if isinstance(node, UnaryOpNode):
        return [node.operand]
    if isinstance(node, VarNode):
        return [v for _, v in node.variables] + [node.body]
    return []


def walk(node: Optional[DaxNode]) -> Iterator[DaxNode]:
    if node is None:
        return
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(children(cur)))


def strip_quotes(name: str) -> str:
    name = (name or "").strip()
    if len(name) >= 2 and name[0] == name[-1] == "'":
        name = name[1:-1]
    return name.replace("''", "'")
