"""Regression tests for ontology defects A1-A5 (A6 lives in test_ontology_ai_api.py)."""

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.ontology.federated_loader import FederatedOntologyLoader as L


def _dom(yaml_text, name=None):
    return L.parse_yaml_string(yaml_text, name)


def _engine(yaml_text):
    fed = L.merge_domains([_dom(yaml_text)])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    return NLQueryEngine(idx, fed)


# ---------------- A1: first-wins merge ----------------

def test_a1_merge_first_wins_camelcase_and_reports_collision():
    a = _dom("domain: A\nconcepts:\n  - name: CustomerOrder\n    description: first\n"
             "metrics:\n  - name: TotalSales\n    sql_expression: SUM(a)\n")
    b = _dom("domain: B\nconcepts:\n  - name: CustomerOrder\n    description: second\n"
             "metrics:\n  - name: TotalSales\n    sql_expression: SUM(b)\n")
    c = _dom("domain: C\nconcepts:\n  - name: CustomerOrder\n    description: third\n")
    fed = L.merge_domains([a, b, c])
    assert fed.concepts["CustomerOrder"].description == "first"
    assert fed.metrics["TotalSales"].sql_expression == "SUM(a)"
    kinds = {x.item_type for x in fed.collisions}
    assert kinds == {"concept", "metric"}


# ---------------- A2: metric binds to real dataset ----------------

_OSI_YAML = """
domain: Sales
concepts:
  - name: Orders
    physical_table: FACT_ORDERS
    attributes:
      - {name: order_id, is_primary_key: true, data_type: NUMBER}
      - {name: amount, data_type: "DECIMAL(10,2)"}  # quoted: unquoted, YAML flow syntax splits it into a bogus "2)" key
      - {name: order_date, data_type: DATE}
  - name: Customer
    physical_table: DIM_CUSTOMER
    attributes:
      - {name: customer_id, is_primary_key: true, data_type: VARCHAR}
metrics:
  - name: revenue
    sql_expression: SUM(amount)
    grain: [order_date]
  - name: cust_count
    sql_expression: COUNT(customer_id)
    concept: Customer
relationships:
  - from_concept: Orders
    to_concept: Customer
    cardinality: N:1
    join_expression: "DIM_CUSTOMER.customer_id = FACT_ORDERS.cust_id"
"""


def test_a2_metrics_bind_to_existing_datasets():
    osi = L.to_osi_model(L.merge_domains([_dom(_OSI_YAML)]))
    names = {d.unique_name for d in osi.datasets}
    by = {m.unique_name: m.dataset for m in osi.metrics}
    assert by["revenue"] == "Orders"       # inferred from expression/grain
    assert by["cust_count"] == "Customer"  # explicit binding
    assert set(by.values()) <= names


def test_a2_metric_skipped_when_no_concepts():
    dom = _dom("domain: X\nmetrics:\n  - name: m\n    sql_expression: COUNT(*)\n")
    assert L.to_osi_model(L.merge_domains([dom])).metrics == []


# ---------------- A3: relationship columns from join expression ----------------

def test_a3_reversed_join_expression_orientation():
    osi = L.to_osi_model(L.merge_domains([_dom(_OSI_YAML)]))
    rel = osi.relationships[0]
    assert rel.from_dataset == "Orders" and rel.to_dataset == "Customer"
    assert rel.from_columns == ["cust_id"]
    assert rel.to_columns == ["customer_id"]


def test_a3_multi_column_join():
    y = _OSI_YAML.replace(
        '"DIM_CUSTOMER.customer_id = FACT_ORDERS.cust_id"',
        '"Orders.a = Customer.x AND Orders.b = Customer.y"',
    )
    rel = L.to_osi_model(L.merge_domains([_dom(y)])).relationships[0]
    assert rel.from_columns == ["a", "b"] and rel.to_columns == ["x", "y"]


def test_a3_pk_fallback_and_skip_when_unresolvable():
    fallback = _OSI_YAML.replace(
        '    join_expression: "DIM_CUSTOMER.customer_id = FACT_ORDERS.cust_id"\n', ""
    ).replace("order_id, is_primary_key: true", "order_id, is_primary_key: true").replace(
        "- {name: order_date, data_type: DATE}",
        "- {name: order_date, data_type: DATE}\n      - {name: customer_id, data_type: VARCHAR}",
    )
    rel = L.to_osi_model(L.merge_domains([_dom(fallback)])).relationships[0]
    assert rel.from_columns == ["customer_id"] and rel.to_columns == ["customer_id"]

    unresolvable = _OSI_YAML.replace(
        '    join_expression: "DIM_CUSTOMER.customer_id = FACT_ORDERS.cust_id"\n', ""
    )
    assert L.to_osi_model(L.merge_domains([_dom(unresolvable)])).relationships == []


def test_a3_duplicate_relationship_names_get_suffix():
    y = _OSI_YAML + (
        "  - from_concept: Orders\n    to_concept: Customer\n    cardinality: N:1\n"
        '    join_expression: "Orders.cust_id = Customer.customer_id"\n'
    )
    rels = L.to_osi_model(L.merge_domains([_dom(y)])).relationships
    assert len(rels) == 2
    assert len({r.unique_name for r in rels}) == 2


# ---------------- A4 / A5: NL query engine ----------------

_NL_YAML = """
domain: Finance
concepts:
  - name: Sales
    physical_table: FACT_SALES
    attributes:
      - {name: region, data_type: VARCHAR}
      - {name: sale_date, data_type: DATE}
metrics:
  - name: gross_margin
    sql_expression: "SUM(revenue) - SUM(cogs)"
"""


def test_a4_no_duplicate_dimensions():
    plan = _engine(_NL_YAML).resolve_query("gross margin by Sales")
    assert len(plan.detected_dimensions) == len(set(plan.detected_dimensions))
    gb = plan.generated_sql.split("GROUP BY", 1)[-1]
    cols = [c.strip() for c in gb.split(",")]
    assert len(cols) == len(set(cols))


def test_a5_date_column_resolved_and_quarter_numeric():
    eng = _engine(_NL_YAML)
    plan = eng.resolve_query("gross margin by Sales in 2024")
    assert "YEAR(sale_date) = 2024" in plan.filters
    plan_q = eng.resolve_query("gross margin by Sales in Q1 2024")
    assert "QUARTER(sale_date) = 1" in plan_q.filters
    assert not any("'Q1'" in f for f in plan_q.filters)


def test_a5_fallback_date_column_emits_warning():
    nodate = _NL_YAML.replace("      - {name: sale_date, data_type: DATE}\n", "")
    plan = _engine(nodate).resolve_query("gross margin by Sales in 2024")
    assert "YEAR(date) = 2024" in plan.filters
    assert plan.warnings


# ---------------- from_osi_model & merge preservation ----------------

def test_from_osi_model_conversion_and_enrichment_merge():
    # 1. Compile sample ontology to OSIModel
    dom = _dom(_OSI_YAML, "Sales")
    fed = L.merge_domains([dom])
    osi = L.to_osi_model(fed, model_name="SALES_MODEL")

    # 2. Add human enrichments to existing domain
    dom.owner = "steward@enterprise.com"
    dom.concepts[0].business_rules = ["Must be valid orders"]
    dom.concepts[0].data_steward = "Orders Steward"
    dom.metrics[0].business_rules = ["Exclude test orders"]
    dom.metrics[0].data_steward = "Finance Steward"

    # 3. Bootstrap from OSIModel while passing existing enriched ontology
    new_dom = L.from_osi_model(osi, domain_name="Sales", existing_ontology=dom)

    # 4. Verify concepts and metrics converted
    assert len(new_dom.concepts) == len(osi.datasets)
    assert len(new_dom.metrics) == len(osi.metrics)
    assert len(new_dom.relationships) == len(osi.relationships)

    # 5. Verify human-authored rules & stewards preserved
    orders_c = next(c for c in new_dom.concepts if c.name == "Orders")
    assert orders_c.business_rules == ["Must be valid orders"]
    assert orders_c.data_steward == "Orders Steward"

    rev_m = next(m for m in new_dom.metrics if m.name == "revenue")
    assert rev_m.business_rules == ["Exclude test orders"]
    assert rev_m.data_steward == "Finance Steward"
    assert rev_m.concept == "Orders"
    assert new_dom.owner == "steward@enterprise.com"


# ---------------- Point 2 & Point 4: Metric Lineage, Cycle Validation & Enforceable Policies ----------------

def test_metric_cycle_and_self_dependency_rejected():
    import pytest
    from semabridge.ontology.federated_loader import OntologyValidationError

    with pytest.raises(OntologyValidationError, match="cannot derive from itself"):
        _dom("domain: D\nmetrics:\n  - name: m1\n    derives_from: [m1]\n")

    with pytest.raises(OntologyValidationError, match="Circular metric dependency"):
        _dom(
            "domain: D\nmetrics:\n"
            "  - name: m1\n    derives_from: [m2]\n"
            "  - name: m2\n    derives_from: [m1]\n"
        )


def test_nl_query_enforces_policies_and_expands_derived_metrics():
    yaml_text = """
domain: Finance
policies:
  - name: exclude_intercompany
    description: Exclude intercompany eliminations
    filter_expression: "is_intercompany = FALSE"
    applies_to: [Sales, net_margin]
concepts:
  - name: Sales
    physical_table: FACT_SALES
    filter_expression: "is_deleted = FALSE"
    attributes:
      - {name: revenue, data_type: DECIMAL}
      - {name: cogs, data_type: DECIMAL}
      - {name: sale_date, data_type: DATE}
metrics:
  - name: total_revenue
    concept: Sales
    sql_expression: "SUM(revenue)"
  - name: total_cogs
    concept: Sales
    sql_expression: "SUM(cogs)"
  - name: net_margin
    concept: Sales
    sql_expression: "total_revenue - total_cogs"
    filter_expression: "order_status = 'COMPLETED'"
"""
    eng = _engine(yaml_text)
    plan = eng.resolve_query("net margin by Sales in 2024")
    # 1. Composite metric expression is expanded from upstream metrics
    assert "(SUM(revenue)) - (SUM(cogs)) AS net_margin" in plan.generated_sql
    assert set(plan.derived_from_metrics) == {"total_revenue", "total_cogs"}
    # 2. Concept filter, metric filter, and domain policy filter are all enforced in WHERE clause
    assert "YEAR(sale_date) = 2024" in plan.filters
    assert "is_deleted = FALSE" in plan.filters
    assert "order_status = 'COMPLETED'" in plan.filters
    assert "is_intercompany = FALSE" in plan.filters
    assert "WHERE YEAR(sale_date) = 2024 AND is_deleted = FALSE AND order_status = 'COMPLETED' AND is_intercompany = FALSE" in plan.generated_sql
    assert "exclude_intercompany" in plan.applied_policies


