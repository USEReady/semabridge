"""Tests for Governance Guards, Plan Checks, and Accuracy in Ask AI & BI-Truth.

Covers the 8 required guard classes:
1. Fan-out path rejected/flagged (_detect_fan_out_path / chasm trap)
2. Two-fact query not multiplied (aggregates separately then combines via CTEs)
3. Window metric without its dimension rejected (PARTITION BY EXCLUDING)
4. Filtered SPLY not NULL (outer WHERE clause on SEMANTIC_VIEW)
5. Semi-additive (NON ADDITIVE BY) metric never summed over non-additive dimension
6. NULL metric not used (deployed as NULL or marked unanswerable)
7. Breakdown exceeding total flagged (possible double counting / NULL share)
8. Certified only with governed source and passing checks
"""

from unittest.mock import patch
import pytest
from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.api.routers.ai_router import (
    ExecuteQueryRequest,
    ExecuteQueryResponse,
    check_query_results,
    execute_semantic_query,
)
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import (
    CardinalityType,
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainRelationship,
)


def _build_test_engine(domain: DomainOntology) -> NLQueryEngine:
    fed = FederatedOntologyLoader.merge_domains([domain])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    return NLQueryEngine(idx, fed)


# ============================================================================
# Class 1: Fan-out path rejected/flagged (chasm trap)
# ============================================================================
def test_class1_fan_out_path_rejected():
    """Join path going many->one->many is detected and rejected before execution."""
    dom = DomainOntology(
        domain="CHASM_TEST",
        semantic_view_name="DB.SC.CHASM_SV",
        concepts=[
            DomainConcept(name="FACT_ORDERS", attributes=[
                DomainAttribute(name="ORDER_ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="CUSTOMER_ID", data_type="INTEGER"),
                DomainAttribute(name="AMOUNT", data_type="FLOAT"),
            ]),
            DomainConcept(name="DIM_CUSTOMER", attributes=[
                DomainAttribute(name="CUSTOMER_ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="CUSTOMER_NAME", data_type="VARCHAR"),
            ]),
            DomainConcept(name="FACT_PAYMENTS", attributes=[
                DomainAttribute(name="PAYMENT_ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="CUSTOMER_ID", data_type="INTEGER"),
                DomainAttribute(name="PAYMENT_AMOUNT", data_type="FLOAT"),
                DomainAttribute(name="PAYMENT_METHOD", data_type="VARCHAR"),
            ]),
        ],
        metrics=[
            DomainMetric(
                name="TOTAL_ORDER_AMOUNT",
                label="Total Order Amount",
                sql_expression="SUM(FACT_ORDERS.AMOUNT)",
                concept="FACT_ORDERS",
            ),
        ],
        relationships=[
            # FACT_ORDERS -> DIM_CUSTOMER (many-to-one)
            DomainRelationship(
                name="r_ord_cust",
                from_concept="FACT_ORDERS",
                to_concept="DIM_CUSTOMER",
                cardinality=CardinalityType.MANY_TO_ONE,
                join_expression="FACT_ORDERS.CUSTOMER_ID = DIM_CUSTOMER.CUSTOMER_ID",
            ),
            # DIM_CUSTOMER -> FACT_PAYMENTS (one-to-many: customer to many payments)
            DomainRelationship(
                name="r_cust_pay",
                from_concept="DIM_CUSTOMER",
                to_concept="FACT_PAYMENTS",
                cardinality=CardinalityType.ONE_TO_MANY,
                join_expression="DIM_CUSTOMER.CUSTOMER_ID = FACT_PAYMENTS.CUSTOMER_ID",
            ),
        ],
    )
    engine = _build_test_engine(dom)

    # Querying TOTAL_ORDER_AMOUNT by PAYMENT_METHOD traverses FACT_ORDERS -> DIM_CUSTOMER -> FACT_PAYMENTS (many->one->many)
    plan = engine.resolve_query("What is the Total Order Amount by PAYMENT_METHOD?")
    assert not plan.answerable
    assert not plan.plan_checks_passed
    assert "fan-out" in plan.explanation.lower() or "chasm" in plan.explanation.lower()


# ============================================================================
# Class 2: Two-fact query not multiplied (separate CTEs)
# ============================================================================
def test_class2_two_fact_query_separate_ctes():
    """Metrics from different fact tables are aggregated separately before combining."""
    dom = DomainOntology(
        domain="MULTI_FACT_TEST",
        semantic_view_name="DB.SC.MULTI_FACT_SV",
        concepts=[
            DomainConcept(name="FACT_SHIPMENTS", attributes=[
                DomainAttribute(name="SHIPMENT_ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="LOCATION_ID", data_type="INTEGER"),
                DomainAttribute(name="SHIPPED_QTY", data_type="FLOAT"),
            ]),
            DomainConcept(name="FACT_RETURNS", attributes=[
                DomainAttribute(name="RETURN_ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="LOCATION_ID", data_type="INTEGER"),
                DomainAttribute(name="RETURNED_QTY", data_type="FLOAT"),
            ]),
            DomainConcept(name="DIM_LOCATION", attributes=[
                DomainAttribute(name="LOCATION_ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="REGION_NAME", data_type="VARCHAR"),
            ]),
        ],
        metrics=[
            DomainMetric(
                name="TOTAL_SHIPPED",
                label="Total Shipped",
                sql_expression="SUM(FACT_SHIPMENTS.SHIPPED_QTY)",
                concept="FACT_SHIPMENTS",
            ),
            DomainMetric(
                name="TOTAL_RETURNED",
                label="Total Returned",
                sql_expression="SUM(FACT_RETURNS.RETURNED_QTY)",
                concept="FACT_RETURNS",
            ),
        ],
        relationships=[
            DomainRelationship(
                name="r_ship_loc",
                from_concept="FACT_SHIPMENTS",
                to_concept="DIM_LOCATION",
                cardinality=CardinalityType.MANY_TO_ONE,
                join_expression="FACT_SHIPMENTS.LOCATION_ID = DIM_LOCATION.LOCATION_ID",
            ),
            DomainRelationship(
                name="r_ret_loc",
                from_concept="FACT_RETURNS",
                to_concept="DIM_LOCATION",
                cardinality=CardinalityType.MANY_TO_ONE,
                join_expression="FACT_RETURNS.LOCATION_ID = DIM_LOCATION.LOCATION_ID",
            ),
        ],
    )
    engine = _build_test_engine(dom)

    # Query requesting metrics from both fact tables with physical SQL generation
    plan = engine.resolve_query(
        "Total Shipped and Total Returned by REGION_NAME",
        semantic_view=None,  # triggers multi-fact CTE aggregation
    )
    assert plan.answerable
    assert "__agg_fact_1" in plan.generated_sql.lower()
    assert "__agg_fact_2" in plan.generated_sql.lower()
    assert "full outer join" in plan.generated_sql.lower()


# ============================================================================
# Class 3: Window metric without its dimension rejected
# ============================================================================
def test_class3_window_metric_missing_partition_dim_rejected():
    """Window metric with PARTITION BY EXCLUDING rejects queries missing required dimension."""
    dom = DomainOntology(
        domain="WINDOW_TEST",
        semantic_view_name="DB.SC.WINDOW_SV",
        concepts=[
            DomainConcept(name="FACT_METRICS", attributes=[
                DomainAttribute(name="ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="DEPARTMENT_NAME", data_type="VARCHAR"),
                DomainAttribute(name="VAL", data_type="FLOAT"),
            ]),
        ],
        metrics=[
            DomainMetric(
                name="DEPT_SHARE_PCT",
                label="Dept Share Pct",
                sql_expression="SUM(FACT_METRICS.VAL) / SUM(FACT_METRICS.VAL) OVER (PARTITION BY EXCLUDING (FACT_METRICS.DEPARTMENT_NAME))",
                concept="FACT_METRICS",
            ),
        ],
    )
    engine = _build_test_engine(dom)

    # Querying without DEPARTMENT_NAME must be rejected by plan checks
    plan = engine.resolve_query(
        "What is the Dept Share Pct?",
        semantic_view="DB.SC.WINDOW_SV",
        dialect="SNOWFLAKE",
    )
    assert not plan.answerable
    assert not plan.plan_checks_passed
    assert "window" in plan.explanation.lower()
    assert "department_name" in plan.explanation.lower()


# ============================================================================
# Class 4: Filtered SPLY not NULL (outer WHERE clause on SEMANTIC_VIEW)
# ============================================================================
def test_class4_filtered_sply_outer_where_clause():
    """Period filter on window/LAG metric is applied in outer WHERE to prevent NULL SPLY."""
    dom = DomainOntology(
        domain="TIME_INTEL_TEST",
        semantic_view_name="DB.SC.TIME_SV",
        concepts=[
            DomainConcept(name="FACT_DATA", attributes=[
                DomainAttribute(name="ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="ORDER_DATE", data_type="DATE"),
                DomainAttribute(name="VAL", data_type="FLOAT"),
            ]),
        ],
        metrics=[
            DomainMetric(
                name="SPLY_VAL",
                label="SPLY Val",
                sql_expression="LAG(SUM(FACT_DATA.VAL), 1) OVER (ORDER BY ORDER_DATE)",
                concept="FACT_DATA",
            ),
        ],
    )
    engine = _build_test_engine(dom)

    # Query asking for SPLY with year filter
    plan = engine.resolve_query(
        "What is the SPLY Val in 2024?",
        dialect="SNOWFLAKE",
        semantic_view="DB.SC.TIME_SV",
    )
    assert plan.answerable
    assert "SEMANTIC_VIEW(DB.SC.TIME_SV" in plan.generated_sql
    # Period filter must NOT be inside the SEMANTIC_VIEW parentheses
    sv_clause = plan.generated_sql.split("SEMANTIC_VIEW(")[1].split(")")[0]
    assert "2024" not in sv_clause
    # Period filter must be on the outer SELECT query
    assert "WHERE" in plan.generated_sql
    assert "2024" in plan.generated_sql


# ============================================================================
# Class 5: Semi-additive (NON ADDITIVE BY) metric not summed
# ============================================================================
def test_class5_semi_additive_not_summed_over_non_additive_dim():
    """Semi-additive metric rejects query attempting to aggregate over its non-additive dimension."""
    dom = DomainOntology(
        domain="SEMI_ADD_TEST",
        semantic_view_name="DB.SC.SEMI_ADD_SV",
        concepts=[
            DomainConcept(name="FACT_BALANCES", attributes=[
                DomainAttribute(name="ID", data_type="INTEGER", is_primary_key=True),
                DomainAttribute(name="AS_OF_DATE", data_type="DATE"),
                DomainAttribute(name="BRANCH_CODE", data_type="VARCHAR"),
                DomainAttribute(name="BALANCE_AMT", data_type="FLOAT"),
            ]),
        ],
        metrics=[
            DomainMetric(
                name="ENDING_BALANCE",
                label="Ending Balance",
                sql_expression="SUM(FACT_BALANCES.BALANCE_AMT) /* NON ADDITIVE BY (FACT_BALANCES.AS_OF_DATE) */",
                concept="FACT_BALANCES",
            ),
        ],
    )
    engine = _build_test_engine(dom)

    # Querying Ending Balance without filtering or grouping by AS_OF_DATE must be rejected
    plan = engine.resolve_query(
        "What is the Ending Balance by BRANCH_CODE?",
        semantic_view="DB.SC.SEMI_ADD_SV",
        dialect="SNOWFLAKE",
    )
    assert not plan.answerable
    assert not plan.plan_checks_passed
    assert "semi-additive" in plan.explanation.lower()
    assert "as_of_date" in plan.explanation.lower()


# ============================================================================
# Class 6: NULL metric not used
# ============================================================================
def test_class6_null_metric_not_used():
    """Metric deployed as NULL or marked unanswerable is refused by plan checks."""
    dom = DomainOntology(
        domain="NULL_METRIC_TEST",
        semantic_view_name="DB.SC.NULL_SV",
        concepts=[
            DomainConcept(name="FACT_ITEMS", attributes=[
                DomainAttribute(name="ID", data_type="INTEGER", is_primary_key=True),
            ]),
        ],
        metrics=[
            DomainMetric(
                name="NULL_MEASURE",
                label="Null Measure",
                sql_expression="CAST(NULL AS FLOAT)",
                concept="FACT_ITEMS",
            ),
        ],
    )
    engine = _build_test_engine(dom)

    plan = engine.resolve_query(
        "What is the Null Measure?",
        semantic_view="DB.SC.NULL_SV",
        dialect="SNOWFLAKE",
    )
    assert not plan.answerable
    assert not plan.plan_checks_passed
    assert "unanswerable" in plan.explanation.lower() or "null" in plan.explanation.lower()


# ============================================================================
# Class 7: Breakdown exceeding total flagged
# ============================================================================
def test_class7_breakdown_exceeding_total_flagged():
    """Result checks flag breakdown total exceeding card value as possible double counting."""
    columns = ["CATEGORY", "VAL"]
    # Grouped rows summing to 1500 when card total is 1000 (> 1% tolerance)
    rows = [
        ["Cat A", 600.0],
        ["Cat B", 900.0],
    ]
    warns, is_valid = check_query_results(columns, rows, card_total=1000.0)
    assert not is_valid
    assert any("possible double counting" in w.lower() for w in warns)

    # Also test high percentage of NULL groups (>= 30%)
    null_rows = [
        [None, 100.0],
        [None, 200.0],
        ["Cat C", 300.0],
    ]
    warns2, is_valid2 = check_query_results(columns, null_rows, card_total=600.0)
    assert any("null" in w.lower() for w in warns2)


# ============================================================================
# Class 8: Certified only with governed source and passing checks
# ============================================================================
def test_class8_certified_only_with_governed_source_and_passing_checks():
    """Certified status is granted ONLY when governed source used and all checks pass."""
    fake_response = ExecuteQueryResponse(
        success=True,
        target="SNOWFLAKE",
        columns=["CAT", "VAL"],
        rows=[["Total", 100.0]],
        row_count=1,
        execution_time_ms=10,
        executed_sql="SELECT 'Total' AS CAT, 100.0 AS VAL",
        message="Query executed successfully",
        dialect="SNOWFLAKE",
        warnings=[],
    )

    with patch("semabridge.api.routers.ai_router._execute_snowflake", return_value=fake_response):
        # 1. Governed semantic view query with matching totals -> Certified
        req_pass = ExecuteQueryRequest(
            sql="SELECT 'Total' AS CAT, 100.0 AS VAL",
            query_path="semantic_view",
            card_total=100.0,
        )
        resp_pass = execute_semantic_query(req_pass)
        assert resp_pass.certified is True
        assert resp_pass.query_path == "semantic_view"
        assert not resp_pass.warnings

        # 2. Free-form SQL query -> Never Certified
        req_free = ExecuteQueryRequest(
            sql="SELECT 'Total' AS CAT, 100.0 AS VAL",
            query_path="free-form SQL",
            card_total=100.0,
        )
        resp_free = execute_semantic_query(req_free)
        assert resp_free.certified is False
        assert resp_free.query_path == "free-form SQL"

        # 3. Governed query that fails double counting check -> Certification blocked
        fake_double_rows = ExecuteQueryResponse(
            success=True,
            target="SNOWFLAKE",
            columns=["CAT", "VAL"],
            rows=[["A", 80.0], ["B", 80.0]],  # Sum is 160 vs card_total 100
            row_count=2,
            execution_time_ms=10,
            executed_sql="SELECT CAT, VAL FROM t",
            message="Query executed successfully",
            dialect="SNOWFLAKE",
            warnings=[],
        )
        with patch("semabridge.api.routers.ai_router._execute_snowflake", return_value=fake_double_rows):
            req_double_count = ExecuteQueryRequest(
                sql="SELECT CAT, VAL FROM t",
                query_path="semantic_view",
                card_total=100.0,
                is_breakdown=True,
            )
            resp_double_count = execute_semantic_query(req_double_count)
            assert resp_double_count.certified is False
            assert any("possible double counting" in w.lower() for w in resp_double_count.warnings)
