# src/semabridge/models/phase_2_semantic_intent.py

from typing import List, Optional, Dict, Any, Literal, Set
from dataclasses import dataclass, field
from enum import Enum

class FilterMode(str, Enum):
    """How a filter is applied"""
    INCLUDE = "include"       # Normal filter (WHERE clause)
    EXCLUDE = "exclude"       # NOT filter
    REPLACE = "replace"       # ADDFILTERS replaces previous
    REMOVE = "remove"         # REMOVEFILTERS/ALL removes

@dataclass
class FilterContext:
    """Single filter context in CALCULATE stack"""
    filter_table: str
    filter_column: str
    filter_values: Optional[List[Any]] = None  # Literal values
    filter_expression: Optional[str] = None     # Complex expression
    propagation_mode: FilterMode = FilterMode.INCLUDE
    semantic_intent: str = ""    # Why this filter (user intent)
    is_bidirectional: bool = False
    depth: int = 0               # Nesting depth

@dataclass
class ComplexCalculateIntent:
    """Semantic intent for complex CALCULATE expressions"""
    base_measure: str
    filter_contexts: List[FilterContext] = field(default_factory=list)
    context_depth: int = 0
    has_conflicting_filters: bool = False
    conflict_description: Optional[str] = None
    is_deterministic: bool = True
    complexity_tier: Literal[4, 5] = 4  # Tier 4-5 (complex)

@dataclass
class RowContextIntent:
    """Semantic intent for EARLIER/EARLIEST functions"""
    base_expression: str
    offset: Optional[int] = None              # For EARLIER(col, offset)
    row_context_type: Literal["current", "earlier", "earliest"] = "current"
    ordering_column: str = ""                 # What defines row order
    partition_by: Optional[str] = None        # Window partition column
    is_deterministic: bool = True
    requires_sort_stability: bool = True

@dataclass
class IteratorIntent:
    """Semantic intent for SUMX/AVERAGEX/RANKX"""
    iterator_function: Literal["SUMX", "AVERAGEX", "RANKX"] = "SUMX"
    table: str = ""
    expression: str = ""
    filter_contexts: List[FilterContext] = field(default_factory=list)
    nested_iterator: Optional["IteratorIntent"] = None
    output_type: str = "numeric"
    row_context_in_expression: bool = False
    nesting_depth: int = 1
