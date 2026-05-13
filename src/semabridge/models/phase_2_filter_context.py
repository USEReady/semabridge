# src/semabridge/models/phase_2_filter_context.py
from typing import List, Optional, Literal
from dataclasses import dataclass, field

@dataclass
class FilterStep:
    """Single step in filter application"""
    step_number: int
    line_number: int
    function: str                  # CALCULATE, FILTER, REMOVEFILTERS, etc
    filters_applied: List[str]
    filters_removed: List[str]
    context_depth_before: int
    context_depth_after: int
    semantic_operation: str         # What semantically happens

@dataclass
class FilterContextTrace:
    """Complete trace of filter application"""
    measure: str
    steps: List[FilterStep] = field(default_factory=list)
    final_filters: List[str] = field(default_factory=list)
    conflicts_detected: List[str] = field(default_factory=list)
    has_circular_dependency: bool = False
    is_deterministic: bool = True
    max_context_depth: int = 0

@dataclass
class FilterConflict:
    """Detected conflict in filter application"""
    involved_filters: List[str]
    conflict_type: Literal["circular", "contradictory", "ambiguous"] = "ambiguous"
    description: str = ""
    severity: Literal["warning", "error"] = "warning"
    suggested_resolution: Optional[str] = None
