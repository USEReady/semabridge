"""
Context Conflict Detector for DAX Filter Contexts.

Identifies potential semantic issues in filter context propagation.
"""

from typing import List, Dict, Set
from semabridge.models.phase_2_filter_context import FilterContextTrace, FilterConflict

class ContextConflictDetector:
    """
    Analyzes a FilterContextTrace for semantic conflicts.
    """
    
    def detect(self, trace: FilterContextTrace) -> List[FilterConflict]:
        conflicts = []
        
        # 1. Detect conflicts within individual steps
        for step in trace.steps:
            applied = set(step.filters_applied)
            removed = set(step.filters_removed)
            
            overlap = applied.intersection(removed)
            if overlap:
                conflicts.append(FilterConflict(
                    conflict_type="contradictory",
                    involved_filters=list(overlap),
                    description=f"Filter(s) {overlap} both applied and removed in the same {step.function} call.",
                    severity="error"
                ))
        
        # 2. Detect contradictions across steps (stack based)
        # (This would be more complex as it depends on how filters override each other)
        
        return conflicts
