"""
Cost Attribution Engine for Semabridge.

Tracks and attributes query costs to semantic entities (measures, datasets).
"""

from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class CostMetric:
    entity_name: str
    entity_type: str
    total_cost: float
    query_count: int
    last_accessed: datetime = field(default_factory=datetime.now)

class CostAttributor:
    """
    Attributes Snowflake query costs to semantic model components.
    """
    
    def __init__(self):
        self.metrics: Dict[str, CostMetric] = {}

    def attribute_cost(self, entity_name: str, entity_type: str, cost: float):
        """
        Records cost for a specific semantic entity.
        """
        key = f"{entity_type}:{entity_name}"
        if key not in self.metrics:
            self.metrics[key] = CostMetric(entity_name, entity_type, 0.0, 0)
            
        metric = self.metrics[key]
        metric.total_cost += cost
        metric.query_count += 1
        metric.last_accessed = datetime.now()

    def get_top_cost_entities(self, limit: int = 10) -> List[CostMetric]:
        """
        Returns the most expensive entities.
        """
        return sorted(self.metrics.values(), key=lambda x: x.total_cost, reverse=True)[:limit]
