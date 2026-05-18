from typing import List, Optional, Tuple, Dict
from semabridge.converter.dax_ast_parser import DaxNode, FunctionCallNode, DaxSqlRenderer
from semabridge.utils.logger import get_logger
import re

logger = get_logger(__name__)

class IteratorTranslator:
    """
    Translates DAX iterator functions with robust deterministic rules.
    """
    
    def __init__(self, table_alias: str = "fact"):
        self.table_alias = table_alias
        self.renderer = DaxSqlRenderer(table_alias=table_alias)
        self._last_failure_reason: Optional[str] = None

    def _log_failure_reason(self, reason: str):
        logger.debug(f"IteratorTranslator deterministic fail: {reason}")
        self._last_failure_reason = reason

    def translate(self, node: FunctionCallNode) -> str:
        """
        Translates SUMX, AVERAGEX, RANKX.
        """
        self._last_failure_reason = None
        func_name = node.func.upper()
        
        # Try specialized handlers
        if func_name == "SUMX":
            return self._handle_sumx(node)
        elif func_name == "AVERAGEX":
            return self._handle_averagex(node)
        elif func_name == "RANKX":
            return self._handle_rankx(node)
            
        return self.renderer.render(node)

    def _handle_sumx(self, node: FunctionCallNode) -> str:
        # SUMX(Table, Expression)
        if len(node.args) < 2:
            return "0"
            
        table_arg = node.args[0]
        expr_arg = node.args[1]
        
        # Phase 2: Detect complex function arguments
        from semabridge.converter.dax_ast_parser import FunctionCallNode, ColumnRefNode, LiteralNode
        if isinstance(table_arg, FunctionCallNode):
            self._log_failure_reason(f"SUMX: complex table argument (function '{table_arg.func}') requires LLM")
            raise ValueError(self._last_failure_reason)

        # Robust Column Extraction
        inner_expr = self.renderer.render(expr_arg)
        if not inner_expr:
            self._log_failure_reason("SUMX: failed to render expression argument")
            raise ValueError(self._last_failure_reason)
            
        return f"SUM({inner_expr})"

    def _handle_averagex(self, node: FunctionCallNode) -> str:
        if len(node.args) < 2: return "0"
        inner_expr = self.renderer.render(node.args[1])
        return f"AVG({inner_expr})"

    def _handle_rankx(self, node: FunctionCallNode) -> str:
        # RANKX(Table, Expression, [Value], [Order], [Ties])
        if len(node.args) < 2:
            self._log_failure_reason("RANKX: too few arguments")
            return "1"
            
        table_arg = node.args[0]
        expr_arg = node.args[1]
        
        # Phase 2: Detect complex function arguments
        from semabridge.converter.dax_ast_parser import FunctionCallNode, ColumnRefNode, LiteralNode
        if isinstance(table_arg, FunctionCallNode):
             self._log_failure_reason(f"RANKX: complex table argument '{table_arg.func}' requires LLM")
             raise ValueError(self._last_failure_reason)

        # Robust RANK() generation using AST renderer
        inner_expr = self.renderer.render(expr_arg)
        if not inner_expr:
            self._log_failure_reason("RANKX: failed to render ranking expression")
            raise ValueError(self._last_failure_reason)
            
        # Optional Order argument (Default is DESC)
        order = "DESC"
        if len(node.args) >= 4:
            order_node = node.args[3]
            if isinstance(order_node, LiteralNode) and str(order_node.value).upper() == "ASC":
                order = "ASC"

        return f"RANK() OVER (ORDER BY {inner_expr} {order})"
