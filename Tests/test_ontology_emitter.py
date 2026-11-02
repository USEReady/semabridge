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
