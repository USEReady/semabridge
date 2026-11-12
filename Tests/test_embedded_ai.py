"""Tests for Embedded SemaBridge AI Layer (Semantic Search & NL Query)."""

import pytest

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.federated_loader import FederatedOntologyLoader


DEMO_YAML = """
domain: Finance
concepts:
  - name: SalesTransactions
    label: Sales Transactions
    concept_type: ENTITY
    description: Individual order and sales ledger entries
    physical_table: FACT_SALES
    synonyms: [Orders, Invoices]
    attributes:
      - name: region
        data_type: VARCHAR
        description: Geographic sales territory
        synonyms: [Territory, Geography]
      - name: customer_id
        data_type: VARCHAR
      - name: sale_date
        data_type: DATE
metrics:
  - name: gross_margin
    label: Gross Margin
    description: Revenue minus cost of goods sold
    sql_expression: "SUM(revenue) - SUM(cogs)"
    synonyms: [Margin, GM, Profitability]
  - name: arr
    label: Annual Recurring Revenue
    description: Annualized contract run rate
    sql_expression: "SUM(contract_val) * 12"
    synonyms: [RunRate, ARR]
relationships: []
"""


def test_semantic_search_exact_and_synonym():
    dom = FederatedOntologyLoader.parse_yaml_string(DEMO_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])

    index = SemanticSearchIndex()
    index.index_ontology(fed)

    # 1. Exact match
    hits = index.search("gross_margin", limit=3)
    assert len(hits) > 0
    assert hits[0].name == "gross_margin"
    assert hits[0].match_type in ("EXACT", "HYBRID")

    # 2. Synonym match ("Profitability" should find gross_margin)
    hits_syn = index.search("Profitability", limit=3)
    assert len(hits_syn) > 0
    assert hits_syn[0].name == "gross_margin"

    # 3. Fuzzy / concept match ("Invoices" should find SalesTransactions)
    hits_concept = index.search("Invoices", limit=3)
    assert len(hits_concept) > 0
    assert hits_concept[0].name == "SalesTransactions"


def test_nl_query_generation():
    dom = FederatedOntologyLoader.parse_yaml_string(DEMO_YAML)
    fed = FederatedOntologyLoader.merge_domains([dom])

    index = SemanticSearchIndex()
    index.index_ontology(fed)
    engine = NLQueryEngine(index, fed)

    plan = engine.resolve_query("What is the gross margin by region in 2024?")
    assert plan.confidence_score > 0.4
    assert "Gross Margin" in plan.detected_metrics
    assert "region" in plan.detected_dimensions
    assert "YEAR(sale_date) = 2024" in plan.filters
    assert "SELECT" in plan.generated_sql
    assert "SUM(revenue) - SUM(cogs) AS gross_margin" in plan.generated_sql
    assert "GROUP BY" in plan.generated_sql
