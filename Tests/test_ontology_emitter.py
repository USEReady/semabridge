import pytest
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSICardinality,
    OSIColumn,
    OSIDataset,
    OSIDataType,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)
from semabridge.connectors.ontology_emitter import OntologyEmitter


def test_ontology_emitter_generation():
    dataset_customer = OSIDataset(
        unique_name="DIM_CUSTOMER",
        label="Customer Dimension",
        description="Master Customer Account Records",
        source_table="PROD_CUSTOMER",
        columns=[
            OSIColumn(unique_name="CUSTOMER_ID", label="Customer ID", data_type=OSIDataType.INTEGER, is_key=True),
            OSIColumn(unique_name="CUSTOMER_NAME", label="Customer Name", data_type=OSIDataType.STRING),
        ],
    )
    dataset_sales = OSIDataset(
        unique_name="FACT_SALES",
        label="Sales Transactions",
        description="Completed orders and bookings",
        source_table="PROD_SALES",
        is_fact=True,
        columns=[
            OSIColumn(unique_name="ORDER_ID", label="Order ID", data_type=OSIDataType.INTEGER, is_key=True),
            OSIColumn(unique_name="CUSTOMER_ID", label="Customer ID", data_type=OSIDataType.INTEGER),
            OSIColumn(unique_name="AMOUNT", label="Amount", data_type=OSIDataType.DECIMAL),
        ],
    )
    metric_sales = OSIMetric(
        unique_name="TOTAL_SALES",
        label="Total Sales",
        dataset="FACT_SALES",
        source_column="AMOUNT",
        aggregation=OSIAggregationType.SUM,
        description="Total recognized revenue in USD",
        synonyms=["Revenue", "Topline"],
        business_owner="Finance Ops",
    )
    rel = OSIRelationship(
        unique_name="REL_FACT_SALES_CUSTOMER_ID__DIM_CUSTOMER_CUSTOMER_ID",
        from_dataset="FACT_SALES",
        from_columns=["CUSTOMER_ID"],
        to_dataset="DIM_CUSTOMER",
        to_columns=["CUSTOMER_ID"],
        cardinality=OSICardinality.MANY_TO_ONE,
    )

    model = OSIModel(
        unique_name="RETAIL_SALES",
        label="Retail Sales Model",
        datasets=[dataset_customer, dataset_sales],
        metrics=[metric_sales],
        relationships=[rel],
    )

    emitter = OntologyEmitter(
        target_database="ENTERPRISE_DW",
        target_schema="ANALYTICS",
        ontology_schema="_SEMABRIDGE_ONTOLOGY",
        warehouse="ANALYST_WH",
    )

    ddl = emitter.emit_ontology_ddl(
        model,
        ontology_overrides={
            ("retail_sales", "fact_sales", "total_sales"): {
                "synonyms": ["Gross Revenue", "Bookings"],
                "description": "Total booked and confirmed sales revenue",
                "business_rules": ["Currency normalized to USD", "Excludes test transactions"],
                "data_steward": "stewards@company.com",
            }
        },
    )

    assert "CREATE SCHEMA IF NOT EXISTS ENTERPRISE_DW._SEMABRIDGE_ONTOLOGY;" in ddl
    assert "CREATE TABLE IF NOT EXISTS ENTERPRISE_DW._SEMABRIDGE_ONTOLOGY.ONTOLOGY_CONCEPTS" in ddl
    assert "CREATE TABLE IF NOT EXISTS ENTERPRISE_DW._SEMABRIDGE_ONTOLOGY.ONTOLOGY_RELATIONSHIPS" in ddl
    assert "CREATE TABLE IF NOT EXISTS ENTERPRISE_DW._SEMABRIDGE_ONTOLOGY.ONTOLOGY_METRIC_BINDINGS" in ddl
    assert "CREATE OR REPLACE CORTEX SEARCH SERVICE ENTERPRISE_DW._SEMABRIDGE_ONTOLOGY.RETAIL_SALES_CONCEPT_SEARCH" in ddl
    assert "CREATE OR REPLACE CORTEX SEARCH SERVICE ENTERPRISE_DW._SEMABRIDGE_ONTOLOGY.RETAIL_SALES_METRIC_SEARCH" in ddl
    assert "stewards@company.com" in ddl
    assert "Currency normalized to USD" in ddl
    assert "Gross Revenue" in ddl
    # Point 5: Verify column-level ATTRIBUTE concepts are emitted
    assert "'DIM_CUSTOMER.CUSTOMER_ID' AS CONCEPT_ID" in ddl
    assert "'DIM_CUSTOMER.CUSTOMER_NAME' AS CONCEPT_ID" in ddl
    assert "'FACT_SALES.AMOUNT' AS CONCEPT_ID" in ddl
    assert "'ATTRIBUTE' AS CONCEPT_TYPE" in ddl


def test_ontology_emitter_attributes_lineage_and_filters():
    dataset = OSIDataset(
        unique_name="FACT_ORDERS",
        label="Orders",
        source_table="RAW_ORDERS",
        columns=[
            OSIColumn(
                unique_name="CUST_SEG_CD",
                label="Customer Segment Code",
                data_type=OSIDataType.STRING,
                description="Segment code for customer tier",
                synonyms=["Customer Tier", "Segment"],
            )
        ],
    )
    metric_gross = OSIMetric(
        unique_name="GROSS_REV",
        label="Gross Revenue",
        dataset="FACT_ORDERS",
        expression="SUM(AMOUNT)",
        aggregation=OSIAggregationType.SUM,
    )
    metric_net = OSIMetric(
        unique_name="NET_REV",
        label="Net Revenue",
        dataset="FACT_ORDERS",
        expression="SUM(AMOUNT) - SUM(DISCOUNT)",
        aggregation=OSIAggregationType.SUM,
    )
    model = OSIModel(
        unique_name="FIN_MODEL",
        datasets=[dataset],
        metrics=[metric_gross, metric_net],
    )
    emitter = OntologyEmitter(target_database="DW", ontology_schema="ONTO")
    ddl = emitter.emit_ontology_ddl(
        model,
        ontology_overrides={
            ("fin_model", "fact_orders", "cust_seg_cd"): {
                "synonyms": ["Tier Code"],
                "data_steward": "revops@corp.com",
            },
            ("fin_model", "fact_orders", "net_rev"): {
                "derives_from": ["GROSS_REV"],
                "filter_expression": "order_status = 'COMPLETED'",
            },
        },
    )
    # Column-level attribute emitted with override synonyms and steward
    assert "'FACT_ORDERS.CUST_SEG_CD' AS CONCEPT_ID" in ddl
    assert "'Tier Code'" in ddl
    assert "'Customer Tier'" in ddl
    assert "'revops@corp.com' AS DATA_STEWARD" in ddl
    # Metric lineage emitted in ONTOLOGY_METRIC_BINDINGS and ONTOLOGY_RELATIONSHIPS
    assert "ARRAY_CONSTRUCT('GROSS_REV') AS DERIVES_FROM" in ddl
    assert "'order_status = ''COMPLETED''' AS FILTER_EXPRESSION" in ddl
    assert "'DERIVES_FROM' AS RELATIONSHIP_TYPE" in ddl
    assert "'DERIVES_NET_REV__GROSS_REV' AS RELATIONSHIP_ID" in ddl
