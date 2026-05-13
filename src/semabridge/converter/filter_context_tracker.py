"""
Filter Context Tracker for Advanced DAX Translation.

Tracks filter context propagation through nested CALCULATE, FILTER, 
and other context-modifying functions.
"""

from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

from semabridge.converter.dax_ast_parser import DaxNode, FunctionCallNode, ColumnRefNode, BinaryOpNode, LiteralNode
from semabridge.models.phase_2_filter_context import FilterStep, FilterContextTrace, FilterConflict
from semabridge.models.phase_2_semantic_intent import FilterContext, FilterMode, ComplexCalculateIntent
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class FilterContextTracker:
    """
    Tracks the evolution of filter contexts during DAX translation.
    
    Supports:
    - Nested CALCULATE depth tracking
    - REMOVEFILTERS / ADDFILTERS / ALL awareness
    - Context propagation through filters
    """
    
    def __init__(self):
        self.stack: List[List[FilterContext]] = [[]]  # Stack of filter sets
        self.trace: List[FilterStep] = []
        self.current_depth = 0
        self.step_counter = 0

    def track(self, node: DaxNode) -> FilterContextTrace:
        """
        Walks the AST and builds a filter context trace.
        """
        self.stack = [[]]
        self.trace = []
        self.current_depth = 0
        self.step_counter = 0
        
        self._visit(node)
        
        # Flatten stack for final filters (simplified for now)
        final_filters = []
        for context_list in self.stack:
            for ctx in context_list:
                final_filters.append(f"{ctx.filter_table}[{ctx.filter_column}]")
        
        return FilterContextTrace(
            measure="N/A",  # Caller should set this
            steps=self.trace,
            final_filters=final_filters,
            max_context_depth=self.current_depth
        )

    def _visit(self, node: DaxNode):
        """Recursive visitor for DAX nodes."""
        if isinstance(node, FunctionCallNode):
            self._visit_function(node)
        elif isinstance(node, BinaryOpNode):
            self._visit(node.left)
            self._visit(node.right)
        # Literals and ColumnRefs don't modify context usually unless part of a filter arg
    
    def _visit_function(self, node: FunctionCallNode):
        func_name = node.func.upper()
        
        if func_name == "CALCULATE":
            self._handle_calculate(node)
        elif func_name == "FILTER":
            self._handle_filter(node)
        else:
            # Continue traversal for other functions
            for arg in node.args:
                self._visit(arg)

    def _handle_calculate(self, node: FunctionCallNode):
        """
        CALCULATE(Expression, [Filter1], [Filter2], ...)
        """
        self.current_depth += 1
        depth_before = self.current_depth - 1
        
        applied_filters = []
        removed_filters = []
        
        # First argument is the base expression, following are filters
        for filter_arg in node.args[1:]:
            ctx = self._extract_filter_context(filter_arg)
            if ctx:
                if ctx.propagation_mode == FilterMode.REMOVE:
                    removed_filters.append(ctx)
                else:
                    applied_filters.append(ctx)
        
        self.stack.append(applied_filters)
        
        self.step_counter += 1
        self.trace.append(FilterStep(
            step_number=self.step_counter,
            line_number=0,
            function="CALCULATE",
            filters_applied=[f"{f.filter_table}[{f.filter_column}]" for f in applied_filters],
            filters_removed=[f"{f.filter_table}[{f.filter_column}]" for f in removed_filters],
            context_depth_before=depth_before,
            context_depth_after=self.current_depth,
            semantic_operation="Applying CALCULATE filter context"
        ))
        
        # Recurse into the expression (first argument)
        if node.args:
            self._visit(node.args[0])
            
        # Pop context after visiting
        self.stack.pop()
        self.current_depth -= 1

    def _handle_filter(self, node: FunctionCallNode):
        """
        FILTER(Table, Predicate)
        """
        # FILTER adds context to its inner expression
        # For simplicity, we just visit arguments for now
        for arg in node.args:
            self._visit(arg)

    def _extract_filter_context(self, node: DaxNode) -> Optional[FilterContext]:
        """
        Extracts semantic filter context from a DAX node.
        """
        if isinstance(node, BinaryOpNode) and node.op == "=":
            if isinstance(node.left, ColumnRefNode) and isinstance(node.right, LiteralNode):
                return FilterContext(
                    filter_table=node.left.table,
                    filter_column=node.left.column,
                    filter_values=[node.right.value],
                    propagation_mode=FilterMode.INCLUDE,
                    depth=self.current_depth
                )
        
        if isinstance(node, FunctionCallNode):
            fname = node.func.upper()
            if fname == "REMOVEFILTERS" or fname == "ALL":
                # Handle REMOVEFILTERS(Table[Column])
                if node.args and isinstance(node.args[0], ColumnRefNode):
                    return FilterContext(
                        filter_table=node.args[0].table,
                        filter_column=node.args[0].column,
                        propagation_mode=FilterMode.REMOVE,
                        depth=self.current_depth
                    )
        
        return None
