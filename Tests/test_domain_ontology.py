"""Tests for Federated Domain Ontology Loader, Validator, and Merger."""

import pytest
from pathlib import Path

from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import (
    CardinalityType,
    ConceptType,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainRelationship,
)


SAMPLE_FINANCE_YAML = """
domain: Finance
owner: finance@corp.com
version: 1.0.0
description: Financial metrics and transactions
concepts:
  - name: GeneralLedger
    label: General Ledger
    concept_type: ENTITY
    description: Master transaction table
    physical_table: FACT_GL
    synonyms: [GL, Ledger]
    attributes:
      - name: id
        is_primary_key: true
        data_type: VARCHAR
      - name: revenue
        data_type: DECIMAL(18,2)
      - name: cogs
        data_type: DECIMAL(18,2)
metrics:
  - name: gross_margin
    label: Gross Margin ($)
    sql_expression: "SUM(revenue) - SUM(cogs)"
    aggregation_type: SUM
    synonyms: [GM, Margin]
    data_steward: "CFO"
"""

SAMPLE_SALES_YAML = """
domain: Sales
owner: sales@corp.com
version: 1.0.0
description: Sales pipeline and customers
concepts:
  - name: Customer
    label: Customer
    concept_type: ENTITY
    description: Customer master
    physical_table: DIM_CUSTOMER
    synonyms: [Client, Buyer]
metrics:
  - name: arr
    label: ARR
    sql_expression: "SUM(contract_value) * 12"
    aggregation_type: SUM
relationships:
  - name: REL_GL_CUSTOMER
    from_concept: GeneralLedger
    to_concept: Customer
    cardinality: N:1
    join_expression: "GeneralLedger.customer_id = Customer.id"
"""

COLLIDING_SALES_YAML = """
domain: SalesOps
owner: salesops@corp.com
version: 1.0.0
concepts: []
metrics:
  - name: gross_margin
    label: Sales Gross Margin
    sql_expression: "SUM(net_sales) * 0.4" # Conflict with Finance definition!
    aggregation_type: SUM
"""


def test_parse_domain_yaml():
    dom = FederatedOntologyLoader.parse_yaml_string(SAMPLE_FINANCE_YAML)
    assert dom.domain == "Finance"
    assert len(dom.concepts) == 1
    assert dom.concepts[0].name == "GeneralLedger"
    assert len(dom.metrics) == 1
    assert dom.metrics[0].name == "gross_margin"


def test_merge_domains_and_relationships():
    dom_fin = FederatedOntologyLoader.parse_yaml_string(SAMPLE_FINANCE_YAML)
    dom_sales = FederatedOntologyLoader.parse_yaml_string(SAMPLE_SALES_YAML)

    fed = FederatedOntologyLoader.merge_domains([dom_fin, dom_sales])

    assert len(fed.domains) == 2
    assert "GeneralLedger" in fed.concepts
    assert "Customer" in fed.concepts
    assert "gross_margin" in fed.metrics
    assert "arr" in fed.metrics
    assert len(fed.relationships) == 1
    assert len(fed.collisions) == 0
    assert len(fed.unresolved_references) == 0


def test_metric_collision_detection():
    dom_fin = FederatedOntologyLoader.parse_yaml_string(SAMPLE_FINANCE_YAML)
    dom_conflict = FederatedOntologyLoader.parse_yaml_string(COLLIDING_SALES_YAML)

    fed = FederatedOntologyLoader.merge_domains([dom_fin, dom_conflict])

    # Should detect collision on 'gross_margin'
    assert len(fed.collisions) == 1
    col = fed.collisions[0]
    assert col.item_type == "metric"
    assert col.item_name == "gross_margin"
    assert col.severity == "ERROR"
    assert "Finance" in col.conflicting_domains
    assert "SalesOps" in col.conflicting_domains


def test_unresolved_relationship_detection():
    orphan_yaml = """
domain: Marketing
concepts: []
metrics: []
relationships:
  - from_concept: NonExistentConceptA
    to_concept: NonExistentConceptB
    cardinality: 1:N
"""
    dom = FederatedOntologyLoader.parse_yaml_string(orphan_yaml)
    fed = FederatedOntologyLoader.merge_domains([dom])
    assert len(fed.unresolved_references) >= 2


def test_to_osi_model():
    dom_fin = FederatedOntologyLoader.parse_yaml_string(SAMPLE_FINANCE_YAML)
    dom_sales = FederatedOntologyLoader.parse_yaml_string(SAMPLE_SALES_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom_fin, dom_sales])

    osi = FederatedOntologyLoader.to_osi_model(fed, "ENTERPRISE_TEST")
    assert osi.unique_name == "ENTERPRISE_TEST"
    assert len(osi.datasets) == 2
    dataset_names = {d.unique_name for d in osi.datasets}
    assert "GeneralLedger" in dataset_names
    assert "Customer" in dataset_names
    assert len(osi.relationships) == 1
