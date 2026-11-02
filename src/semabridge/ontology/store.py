"""In-Memory and File-Backed Store for Federated Domain Ontologies."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import (
    DomainOntology,
    FederatedEnterpriseOntology,
)

logger = logging.getLogger("semabridge.ontology.store")


class DomainOntologyStore:
    """Manages active domain ontologies and syncs the embedded AI semantic search index."""

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or Path("Config/ontologies")
        self.domains: Dict[str, DomainOntology] = {}
        self.search_index = SemanticSearchIndex()
        self.query_engine: Optional[NLQueryEngine] = None
        self._ensure_storage_dir()
        self._load_seed_defaults()

    def _ensure_storage_dir(self) -> None:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning("Could not create ontology storage dir %s: %s", self.storage_dir, e)

    def _load_seed_defaults(self) -> None:
        """Load any existing ontology files from disk or initialize baseline enterprise domains."""
        if self.storage_dir.is_dir():
            for f in sorted(list(self.storage_dir.glob("*.yaml")) + list(self.storage_dir.glob("*.yml"))):
                try:
                    dom = FederatedOntologyLoader.load_from_file(f)
                    self.domains[dom.domain] = dom
                except Exception as e:
                    logger.warning("Failed to load initial ontology %s: %s", f, e)

        # If empty, initialize standard enterprise sample domains (Finance and Sales)
        if not self.domains:
            self._seed_sample_domains()

        self._refresh_index()

    def _seed_sample_domains(self) -> None:
        finance_yaml = """
domain: Finance
owner: finance-governance@enterprise.com
version: 1.0.0
description: Core financial metrics, revenue recognition, and profitability concepts.
concepts:
  - name: GeneralLedger
    label: General Ledger
    concept_type: ENTITY
    description: Master general ledger transactions
    physical_table: FACT_GL_TRANSACTIONS
    synonyms: [GL, Ledger, BookOfRecord]
    business_rules:
      - "Debits must equal credits at posting close"
    data_steward: "CFO Office"
    attributes:
      - name: transaction_id
        is_primary_key: true
        data_type: VARCHAR
      - name: account_code
        data_type: VARCHAR
      - name: amount
        data_type: DECIMAL(18,2)
      - name: posting_date
        data_type: DATE
metrics:
  - name: gross_margin
    label: Gross Margin ($)
    description: Total revenue minus cost of goods sold
    sql_expression: "SUM(revenue) - SUM(cogs)"
    aggregation_type: SUM
    grain: [account_code, posting_date]
    synonyms: [GM, Margin, GrossProfit]
    business_rules:
      - "Excludes intercompany transfer eliminations"
    data_steward: "VP Financial Planning"
  - name: ebitda
    label: EBITDA
    description: Earnings before interest, taxes, depreciation, and amortization
    sql_expression: "SUM(operating_income) + SUM(depreciation) + SUM(amortization)"
    aggregation_type: SUM
    synonyms: [OperatingCashFlow, EBITDA_USD]
    data_steward: "VP Financial Planning"
relationships: []
"""
        sales_yaml = """
domain: Sales
owner: revops-team@enterprise.com
version: 1.0.0
description: Revenue operations, customer accounts, and order transactions.
concepts:
  - name: Customer
    label: Customer Account
    concept_type: ENTITY
    description: Master enterprise customer dimension
    physical_table: DIM_CUSTOMER
    synonyms: [Client, Account, Buyer]
    data_steward: "RevOps Team"
    attributes:
      - name: customer_id
        is_primary_key: true
        data_type: VARCHAR
      - name: region
        data_type: VARCHAR
      - name: segment
        data_type: VARCHAR
metrics:
  - name: arr
    label: Annual Recurring Revenue
    description: Annualized value of active recurring subscriptions
    sql_expression: "SUM(monthly_contract_value) * 12"
    aggregation_type: SUM
    grain: [customer_id, contract_id]
    synonyms: [ARR, SubscriptionRunRate]
    data_steward: "RevOps Lead"
relationships:
  - name: REL_GL_CUSTOMER
    from_concept: GeneralLedger
    to_concept: Customer
    cardinality: N:1
    join_expression: "GeneralLedger.customer_id = Customer.customer_id"
"""
        try:
            dom_fin = FederatedOntologyLoader.parse_yaml_string(finance_yaml, "Finance")
            dom_sales = FederatedOntologyLoader.parse_yaml_string(sales_yaml, "Sales")
            self.domains[dom_fin.domain] = dom_fin
            self.domains[dom_sales.domain] = dom_sales
        except Exception as e:
            logger.error("Error seeding sample domains: %s", e)

    def _refresh_index(self) -> None:
        """Re-merge federated domains and re-index the AI search engine."""
        fed = self.get_federated_ontology()
        self.search_index.index_ontology(fed)
        self.query_engine = NLQueryEngine(self.search_index, fed)

    def get_federated_ontology(self) -> FederatedEnterpriseOntology:
        return FederatedOntologyLoader.merge_domains(list(self.domains.values()))

    def add_or_update_domain(self, domain_ontology: DomainOntology, save_to_disk: bool = True) -> FederatedEnterpriseOntology:
        self.domains[domain_ontology.domain] = domain_ontology
        if save_to_disk and self.storage_dir.is_dir():
            file_name = f"{domain_ontology.domain.lower().replace(' ', '_')}.ontology.yaml"
            yaml_str = FederatedOntologyLoader.dump_domain_yaml(domain_ontology)
            try:
                (self.storage_dir / file_name).write_text(yaml_str, encoding="utf-8")
            except Exception as e:
                logger.warning("Could not persist ontology file %s: %s", file_name, e)

        self._refresh_index()
        return self.get_federated_ontology()

    def delete_domain(self, domain_name: str) -> bool:
        if domain_name in self.domains:
            del self.domains[domain_name]
            file_name = f"{domain_name.lower().replace(' ', '_')}.ontology.yaml"
            disk_file = self.storage_dir / file_name
            if disk_file.is_file():
                try:
                    disk_file.unlink()
                except Exception as e:
                    logger.warning("Could not delete file %s: %s", disk_file, e)
            self._refresh_index()
            return True
        return False


# Global singleton instance for application use
ontology_store = DomainOntologyStore()
