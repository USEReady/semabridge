"""
Phase 3 Core Models for Semantic Runtime, Security, and Governance.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Literal, Dict
from datetime import datetime

@dataclass
class RLSPolicy:
    """Represents a Row-Level Security policy from Fabric."""
    id: str
    role: str
    table: str
    filter_expression: str  # DAX or SQL
    policy_type: Literal["row_filter", "column_mask"] = "row_filter"
    masked_columns: Optional[List[str]] = None
    created_at: datetime = field(default_factory=datetime.now)

@dataclass
class SemanticModelChange:
    """Represents a change detected during bidirectional sync."""
    change_id: str
    object_type: Literal["measure", "column", "relationship", "table"]
    object_name: str
    old_definition: Optional[str]
    new_definition: str
    change_type: Literal["added", "modified", "deleted"]
    source: Literal["fabric", "snowflake"]
    timestamp: datetime = field(default_factory=datetime.now)

@dataclass
class LineageNode:
    """Represents a node in the semantic lineage graph."""
    node_id: str
    node_type: Literal["fabric_measure", "snowflake_view", "intermediate_cte", "final_result"]
    name: str
    definition: str
    parents: List[str] = field(default_factory=list) # node_ids
    metadata: Dict = field(default_factory=dict)

@dataclass
class GovernancePolicy:
    """Represents data classification and access policies."""
    policy_id: str
    classification: Literal["public", "internal", "confidential", "pii", "regulated"]
    masking_strategy: Optional[Literal["redact", "hash", "tokenize", "null"]] = None
    allowed_roles: List[str] = field(default_factory=list)
