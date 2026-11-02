"""Domain Ontology Data Models and Schemas.

Represents federated, team-owned domain ontologies (e.g., finance.ontology.yaml,
marketing.ontology.yaml) conforming to Enterprise Data Mesh architecture.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ConceptType(str, Enum):
    ENTITY = "ENTITY"
    DIMENSION = "DIMENSION"
    ATTRIBUTE = "ATTRIBUTE"


class CardinalityType(str, Enum):
    ONE_TO_ONE = "1:1"
    ONE_TO_MANY = "1:N"
    MANY_TO_ONE = "N:1"
    MANY_TO_MANY = "N:N"


class DomainAttribute(BaseModel):
    name: str
    data_type: str = "VARCHAR"
    description: Optional[str] = None
    is_primary_key: bool = False
    synonyms: List[str] = Field(default_factory=list)


class DomainConcept(BaseModel):
    name: str
    label: Optional[str] = None
    concept_type: ConceptType = ConceptType.ENTITY
    domain: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    business_rules: List[str] = Field(default_factory=list)
    data_steward: Optional[str] = None
    source_dataset: Optional[str] = None
    physical_table: Optional[str] = None
    attributes: List[DomainAttribute] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class DomainMetric(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    sql_expression: str
    aggregation_type: str = "SUM"
    grain: List[str] = Field(default_factory=list)
    synonyms: List[str] = Field(default_factory=list)
    business_rules: List[str] = Field(default_factory=list)
    data_steward: Optional[str] = None
    domain: Optional[str] = None
    semantic_view_name: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class DomainRelationship(BaseModel):
    name: Optional[str] = None
    from_concept: str
    to_concept: str
    cardinality: CardinalityType = CardinalityType.MANY_TO_ONE
    join_expression: Optional[str] = None
    predicate_label: Optional[str] = None


class DomainOntology(BaseModel):
    """Represents a single team or domain-level ontology file (e.g. finance.ontology.yaml)."""
    domain: str
    owner: Optional[str] = None
    version: str = "1.0.0"
    description: Optional[str] = None
    concepts: List[DomainConcept] = Field(default_factory=list)
    metrics: List[DomainMetric] = Field(default_factory=list)
    relationships: List[DomainRelationship] = Field(default_factory=list)


class OntologyCollision(BaseModel):
    """Represents a conflict or collision detected across domains."""
    item_type: str  # 'metric' or 'concept'
    item_name: str
    conflicting_domains: List[str]
    descriptions: Dict[str, Optional[str]] = Field(default_factory=dict)
    expressions: Dict[str, Optional[str]] = Field(default_factory=dict)
    severity: str = "WARNING"  # 'WARNING' or 'ERROR'
    message: str


class FederatedEnterpriseOntology(BaseModel):
    """Unified enterprise ontology composed of multiple domain ontologies."""
    domains: Dict[str, DomainOntology] = Field(default_factory=dict)
    concepts: Dict[str, DomainConcept] = Field(default_factory=dict)
    metrics: Dict[str, DomainMetric] = Field(default_factory=dict)
    relationships: List[DomainRelationship] = Field(default_factory=list)
    collisions: List[OntologyCollision] = Field(default_factory=list)
    unresolved_references: List[str] = Field(default_factory=list)
