"""
Context Propagation Validator for DAX.

Validates that filter context propagates correctly according to DAX semantics.
"""

from typing import List
from semabridge.models.phase_2_filter_context import FilterContextTrace, FilterConflict

class ContextPropagationValidator:
    """
    Validates propagation of filter contexts.
    """
    
    def validate(self, trace: FilterContextTrace) -> List[str]:
        errors = []
        
        # 1. Check max depth
        if trace.max_context_depth > 5:
            errors.append(f"CALCULATE nesting depth ({trace.max_context_depth}) exceeds semantic limit of 5.")
            
        # 2. Check for circularity
        if trace.has_circular_dependency:
            errors.append("Circular filter dependency detected in context stack.")
            
        return errors
