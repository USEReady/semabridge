"""
Complex CALCULATE Translator.

Generates Snowflake SQL CTEs for complex nested CALCULATE expressions
to ensure context isolation and semantic parity.
"""

from typing import List, Optional
from semabridge.converter.dax_ast_parser import DaxNode, FunctionCallNode, DaxSqlRenderer
from semabridge.converter.filter_context_tracker import FilterContextTracker

class ComplexCalculateTranslator:
    """
    Translates complex CALCULATE expressions using CTE-based context isolation.
    """
    
    def __init__(self, table_alias: str = "fact"):
        self.table_alias = table_alias
        self.tracker = FilterContextTracker()
        self.renderer = DaxSqlRenderer(table_alias=table_alias)

    def translate_to_ctes(self, node: FunctionCallNode) -> str:
        """
        Translates a CALCULATE node into a chain of SQL CTEs.
        """
        if node.func.upper() != "CALCULATE":
            return self.renderer.render(node)
            
        # 1. Track context
        trace = self.tracker.track(node)
        
        # 2. Build CTE chain
        cte_parts = []
        last_cte = self.table_alias
        
        for i, step in enumerate(trace.steps):
            current_cte = f"ctx_lvl_{i+1}"
            
            # Combine applied filters
            applied_sql = []
            for f in step.filters_applied:
                # Assuming f is in Table[Column] format from tracker
                # We need the actual expression logic here
                # For now, we use a placeholder or extract from step if we improve tracker
                applied_sql.append(f"{f} = 'VALUE'") # Simplified
                
            where_clause = " AND ".join(applied_sql) if applied_sql else "1=1"
            
            # Handle REMOVEFILTERS by selecting from base table instead of previous CTE
            source = last_cte if not step.filters_removed else self.table_alias
            
            cte_parts.append(f"{current_cte} AS (SELECT * FROM {source} WHERE {where_clause})")
            last_cte = current_cte
            
        # 3. Final aggregation from the last context
        base_agg = self.renderer.render(node.args[0])
        
        sql = "WITH " + ",\n".join(cte_parts) + f"\nSELECT {base_agg} FROM {last_cte}"
        return sql
