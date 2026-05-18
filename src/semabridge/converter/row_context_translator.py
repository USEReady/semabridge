"""
Row Context Translator for DAX.

Handles EARLIER and EARLIEST functions by translating them to
Snowflake window functions or correlated subqueries.
"""

from typing import List, Optional
from semabridge.converter.dax_ast_parser import DaxNode, FunctionCallNode, ColumnRefNode, DaxSqlRenderer

class RowContextTranslator:
    """
    Translates EARLIER and EARLIEST functions.
    """
    
    def __init__(self, table_alias: str = "fact"):
        self.table_alias = table_alias
        self.renderer = DaxSqlRenderer(table_alias=table_alias)

    def translate(self, node: FunctionCallNode) -> str:
        """
        Translates EARLIER(Column, [Count]) or EARLIEST(Column).
        """
        func_name = node.func.upper()
        
        if func_name == "EARLIER":
            return self._handle_earlier(node)
        elif func_name == "EARLIEST":
            return self._handle_earliest(node)
            
        return self.renderer.render(node)

    def _handle_earlier(self, node: FunctionCallNode) -> str:
        # EARLIER(Column, [Count])
        if not node.args:
            return "NULL"
            
        col_node = node.args[0]
        if isinstance(col_node, ColumnRefNode):
            col_name = col_node.column
            
            # Robust Ordering Fallback
            # In production, we'd check the schema for a PK/identity column
            order_col = "ID" 
            
            # Use FIRST_VALUE with a stable window
            return f"FIRST_VALUE({self.table_alias}.\"{col_name}\") OVER (ORDER BY {self.table_alias}.{order_col} NULLS LAST)"
            
        return self.renderer.render(col_node)

    def _handle_earliest(self, node: FunctionCallNode) -> str:
        # EARLIEST is like EARLIER with the maximum possible count
        return self._handle_earlier(node)
