"""
Graph utilities for the SemaBridge project.

Provides topological sorting for semantic model dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from typing import List

from semabridge.core.exceptions import ConversionError
from semabridge.sml.models import SMLModel


def sort_datasets_topologically(sml: SMLModel) -> List[List[str]]:
    """
    Sort datasets topologically into levels (waves) for concurrent execution.
    
    A dataset must be in a level > any dataset it references via foreign key.
    This ensures that target tables (e.g., base dimensions) are created before
    source tables (e.g., facts) that reference them.
    
    Args:
        sml: The Semantic Model to analyze.
        
    Returns:
        A list of levels, where each level is a list of dataset unique_names.
        Levels can be executed sequentially, and datasets within a level
        can be executed concurrently.
        
    Raises:
        ConversionError: If a cyclic dependency is detected.
    """
    dependencies: dict[str, set[str]] = defaultdict(set)
    datasets = {d.unique_name for d in sml.datasets}
    
    # Build dependency graph
    # If A has an FK to B, A depends on B (B must be created first)
    for rel in sml.relationships:
        if not rel.is_active:
            continue
            
        from_ds = rel.from_dataset
        to_ds = rel.to_dataset
        
        if from_ds in datasets and to_ds in datasets and from_ds != to_ds:
            dependencies[from_ds].add(to_ds)

    levels: List[List[str]] = []
    remaining = set(datasets)
    
    while remaining:
        # Find datasets with no dependencies currently in 'remaining'
        current_level = [
            node for node in remaining
            if not dependencies[node].intersection(remaining)
        ]
        
        if not current_level:
            # Cycle detected
            cycle_nodes = list(remaining)
            raise ConversionError(
                "Cyclic dependency detected in semantic model relationships. "
                "Unable to determine table creation order. "
                f"Nodes involved in cycle: {cycle_nodes}"
            )
        
        levels.append(current_level)
        remaining.difference_update(current_level)
        
    return levels
