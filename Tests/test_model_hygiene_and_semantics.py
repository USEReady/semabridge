"""Tests for model hygiene and semantic view emission.

1. SummarizeBy carried through, implicit metrics only for visible numeric columns
   on fact tables with that aggregation (none for none, keys, hidden, or covered measures),
   numeric fact columns declared as FACTS.
2. No invented tables (no date table injection when none in model).
3. No guessed primary keys (tables without relationships get no PRIMARY KEY).
4. Measure-only tables omitted from tables, enriched views, and dimensions; measures re-anchored.
5. Fact tables: every table on the many side of a relationship is a fact (multiple allowed, no keyword matching, no 'most relationships').
6. Binding: incompatible columns (< 80%) is a blocker (cannot bind by name only).
7. Inactive relationships: omitted (and no role alias) unless a measure uses USERELATIONSHIP.
8. One-to-one relationships: emitted from the table holding metrics to the other.
"""

from __future__ import annotations
import pytest
from unittest.mock import MagicMock

from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
    Cardinality,
    DataType,
    AggregationType,
)
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
    OSIRelationship,
    OSIAggregationType,
    OSIDataType,
)
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.table_binding_resolver import (
    resolve_dataset_to_physical_table,
    TableBindingIncompatibleColumnsError,
)
from semabridge.core.settings import SnowflakeConfig


def _make_config():
    return SnowflakeConfig(
        account="test_acct", user="test_user", database="TEST_DB", schema_name="TEST_SCHEMA"
    )


def test_summarize_by_and_facts_clause():
    """Item 1: SummarizeBy carried to OSI; implicit metrics only for visible numeric columns on fact tables;
    numeric fact columns declared as FACTS in DDL."""
    tmsl = {
        "model": {
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        # 1. Visible numeric on fact table with summarizeBy="sum" -> gets implicit metric
                        {"name": "Revenue", "dataType": "decimal", "summarizeBy": "sum", "isHidden": False},
                        # 2. summarizeBy="none" -> no implicit metric
                        {"name": "ZipCode", "dataType": "int64", "summarizeBy": "none", "isHidden": False},
                        # 3. Key column -> no implicit metric
                        {"name": "SalesID", "dataType": "int64", "isKey": True, "summarizeBy": "sum", "isHidden": False},
                        # 4. Hidden column -> no implicit metric
                        {"name": "HiddenCost", "dataType": "decimal", "summarizeBy": "sum", "isHidden": True},
                        # 5. Covered by existing DAX measure with SUM -> no duplicate implicit metric
                        {"name": "Quantity", "dataType": "int64", "summarizeBy": "sum", "isHidden": False},
                    ],
                    "measures": [
                        {
                            "name": "TotalQty",
                            "expression": "SUM('Sales'[Quantity])",
                        }
                    ],
                },
                {
                    "name": "Customer",
                    "columns": [
                        {"name": "CustID", "dataType": "int64", "isKey": True},
                        # Dimension table numeric with summarizeBy -> NO implicit metric because Customer is dimension!
                        {"name": "CustomerAge", "dataType": "int64", "summarizeBy": "sum", "isHidden": False},
                    ],
                },
            ],
            "relationships": [
                {
                    "fromTable": "Sales",
                    "fromColumn": "CustID",
                    "toTable": "Customer",
                    "toColumn": "CustID",
                }
            ],
        }
    }

    converter = TMSLToOSIConverter()
    osi = converter.to_osi({"tmsl": tmsl, "dataset_id": "test_ds", "workspace_id": "test_ws"})

    # Verify extraction carried SummarizeBy & isHidden
    sales_ds = next(d for d in osi.datasets if d.unique_name == "Sales")
    rev_col = next(c for c in sales_ds.columns if c.unique_name == "Revenue")
    assert rev_col.default_aggregation == OSIAggregationType.SUM
    assert rev_col.is_hidden is False

    hidden_col = next(c for c in sales_ds.columns if c.unique_name == "HiddenCost")
    assert hidden_col.is_hidden is True

    # Implicit metrics check:
    # Only Revenue should get an implicit metric!
    # ZipCode (none), SalesID (key), HiddenCost (hidden), Quantity (covered), CustomerAge (dimension) must NOT!
    metric_names = [m.unique_name for m in osi.metrics]
    assert "Revenue" in metric_names
    assert "TotalQty" in metric_names
    assert "ZipCode" not in metric_names
    assert "SalesID" not in metric_names
    assert "HiddenCost" not in metric_names
    assert "Quantity" not in metric_names
    assert "CustomerAge" not in metric_names

    # Verify DDL: Numeric fact column (Revenue, Quantity, SalesID) declared in FACTS clause
    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls_from_osi(osi)
    assert len(ddls) >= 1
    ddl = ddls[0]

    assert "FACTS (" in ddl
    assert 'SALES."REVENUE" AS' in ddl
    # FACTS block must precede DIMENSIONS block
    facts_idx = ddl.index("FACTS (")
    dims_idx = ddl.index("DIMENSIONS (")
    assert facts_idx < dims_idx


def test_no_invented_date_table_when_none_in_pbix():
    """Item 2: If the PBIX has no date table, the view has none."""
    tmsl = {
        "model": {
            "tables": [
                {
                    "name": "Transactions",
                    "columns": [
                        {"name": "TxnId", "dataType": "int64"},
                        {"name": "Amount", "dataType": "decimal"},
                    ],
                }
            ],
            "relationships": [],
        }
    }
    # TMSL to SML
    sml = TMSLTransformer().transform(tmsl, workspace_id="ws_test", dataset_id="ds_test")
    table_names = [d.unique_name for d in sml.datasets]
    assert "Date" not in table_names
    assert "DIM_DATE" not in [getattr(d, "source_table", "") for d in sml.datasets]

    # Generate DDL
    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(sml)
    ddl = ddls[0]
    assert "DIM_DATE" not in ddl
    assert 'DATE AS "TEST_DB"."TEST_SCHEMA"."DIM_DATE"' not in ddl


def test_no_guessed_primary_keys_for_table_without_relationships():
    """Item 3: Tables without relationships get no PRIMARY KEY on every path."""
    model = SMLModel(
        unique_name="StandaloneModel",
        datasets=[
            SMLDataset(
                unique_name="StandaloneReport",
                columns=[
                    SMLColumn(unique_name="ArbitraryFirstCol", data_type=DataType.STRING),
                    SMLColumn(unique_name="ValueCol", data_type=DataType.INTEGER),
                ],
            )
        ],
        relationships=[],
    )
    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(model)
    ddl = ddls[0]

    # Must NOT guess ArbitraryFirstCol as PRIMARY KEY
    assert "PRIMARY KEY" not in ddl


def test_measure_only_table_omitted_and_measures_reanchored():
    """Item 4: Measure-only tables (no columns or dummy {1}) are omitted; measures re-anchored to table."""
    model = SMLModel(
        unique_name="MeasureOnlyModel",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="SalesID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Amount", data_type=DataType.FLOAT),
                ],
            ),
            # Measure container table with only dummy column {1}
            SMLDataset(
                unique_name="KeyMeasures",
                is_measure_table=True,
                columns=[
                    SMLColumn(unique_name="Column1", data_type=DataType.INTEGER),
                ],
            ),
        ],
        relationships=[],
        metrics=[
            SMLMetric(
                unique_name="TotalAmount",
                dataset="KeyMeasures",
                expression="SUM('Sales'[Amount])",
                sql_expression='SUM(SALES."AMOUNT")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            )
        ],
    )
    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(model)
    ddl = ddls[0]

    # KeyMeasures must not be in TABLES or DIMENSIONS
    assert 'KEYMEASURES AS' not in ddl
    assert 'KEYMEASURES."COLUMN1"' not in ddl
    # Measure TotalAmount should be placed on SALES
    assert 'SALES."TOTALAMOUNT" AS' in ddl or 'SALES."TotalAmount" AS' in ddl


def test_two_fact_tables_on_many_side_without_keyword_matching():
    """Item 5: Every table on the many side is a fact (several allowed, no keyword matching)."""
    model = SMLModel(
        unique_name="MultiFactModel",
        datasets=[
            # Many-side table 1 (no "FACT" in name)
            SMLDataset(
                unique_name="Orders",
                columns=[
                    SMLColumn(unique_name="OrderID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="CustFK", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Amount", data_type=DataType.FLOAT),
                ],
            ),
            # Many-side table 2 (no "FACT" in name)
            SMLDataset(
                unique_name="Shipments",
                columns=[
                    SMLColumn(unique_name="ShipmentID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="CustFK", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Weight", data_type=DataType.FLOAT),
                ],
            ),
            # One-side dimension table
            SMLDataset(
                unique_name="Customer",
                columns=[
                    SMLColumn(unique_name="CustPK", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="CustName", data_type=DataType.STRING),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="rel_orders_cust",
                from_dataset="Orders",
                from_columns=["CustFK"],
                to_dataset="Customer",
                to_columns=["CustPK"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
            SMLRelationship(
                unique_name="rel_shipments_cust",
                from_dataset="Shipments",
                from_columns=["CustFK"],
                to_dataset="Customer",
                to_columns=["CustPK"],
                cardinality=Cardinality.MANY_TO_ONE,
                is_active=True,
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TotalOrders",
                dataset="Orders",
                expression="SUM('Orders'[Amount])",
                sql_expression='SUM(ORDERS."AMOUNT")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            ),
            SMLMetric(
                unique_name="TotalWeight",
                dataset="Shipments",
                expression="SUM('Shipments'[Weight])",
                sql_expression='SUM(SHIPMENTS."WEIGHT")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            ),
        ],
    )
    emitter = SnowflakeEmitter(_make_config())
    # Neither fact table needs helper columns -> enriched views list must be empty
    needing_enrichment = emitter._get_fact_tables_needing_enrichment(model)
    assert needing_enrichment == []

    ddls = emitter.generate_ddls(model)
    ddl = ddls[0]

    # Both Orders and Shipments numeric columns should be under FACTS
    assert "FACTS (" in ddl
    assert 'ORDERS."AMOUNT" AS' in ddl
    assert 'SHIPMENTS."WEIGHT" AS' in ddl


def test_binding_incompatible_columns_is_blocker():
    """Item 6: Binding by name only when columns differ (< 80%) is a blocker (raises error)."""
    live_schema = {
        "ORDERS": {"COL_X", "COL_Y", "COL_Z"},  # Incompatible with PBIX Orders columns
    }
    pbix_cols = ["ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "TOTAL_AMOUNT"]

    with pytest.raises(TableBindingIncompatibleColumnsError) as exc_info:
        resolve_dataset_to_physical_table("Orders", pbix_cols, live_schema)

    assert "Table binding blocker for 'Orders'" in str(exc_info.value)
    assert "columns do not match" in str(exc_info.value)


def test_inactive_relationship_without_userelationship_omitted():
    """Item 7: Inactive relationships without USERELATIONSHIP are not emitted and get no role alias."""
    model = SMLModel(
        unique_name="InactiveRelModel",
        datasets=[
            SMLDataset(
                unique_name="Orders",
                columns=[
                    SMLColumn(unique_name="OrderID", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="OrderDateKey", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="ShipDateKey", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Amount", data_type=DataType.FLOAT),
                ],
            ),
            SMLDataset(
                unique_name="DimDate",
                columns=[
                    SMLColumn(unique_name="DateKey", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="DateVal", data_type=DataType.DATE),
                ],
            ),
        ],
        relationships=[
            # Active relationship
            SMLRelationship(
                unique_name="rel_orders_dimdate_order",
                from_dataset="Orders",
                from_columns=["OrderDateKey"],
                to_dataset="DimDate",
                to_columns=["DateKey"],
                is_active=True,
            ),
            # Inactive relationship (ShipDate)
            SMLRelationship(
                unique_name="rel_orders_dimdate_ship",
                from_dataset="Orders",
                from_columns=["ShipDateKey"],
                to_dataset="DimDate",
                to_columns=["DateKey"],
                is_active=False,
            ),
        ],
        metrics=[
            # Normal metric not using USERELATIONSHIP
            SMLMetric(
                unique_name="TotalOrders",
                dataset="Orders",
                expression="SUM('Orders'[Amount])",
                sql_expression='SUM(ORDERS."AMOUNT")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            )
        ],
    )
    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(model)
    ddl = ddls[0]

    # Inactive relationship must NOT be emitted
    assert 'ORDERS ("SHIPDATEKEY") REFERENCES DIMDATE' not in ddl.upper()
    # No role-playing alias table in TABLES
    assert 'DIMDATE_SHIPDATEKEY' not in ddl.upper()

    # Now add a measure using USERELATIONSHIP
    model.metrics.append(
        SMLMetric(
            unique_name="ShippedOrders",
            dataset="Orders",
            expression="CALCULATE(SUM('Orders'[Amount]), USERELATIONSHIP('Orders'[ShipDateKey], 'DimDate'[DateKey]))",
            sql_expression="SUM(ORDERS.AMOUNT)",
            aggregation=AggregationType.SUM,
            sync_enabled=True,
        )
    )
    ddls_with_userel = emitter.generate_ddls(model)
    ddl_with_userel = ddls_with_userel[0]
    # Now that USERELATIONSHIP is present, it is emitted
    assert 'DIMDATE_SHIPDATEKEY' in ddl_with_userel.upper()


def test_one_to_one_relationship_emitted_from_table_holding_metrics():
    """Item 8: 1:1 relationships are emitted from the table holding metrics to the other table."""
    model = SMLModel(
        unique_name="OneToOneModel",
        datasets=[
            # Header table holding metrics
            SMLDataset(
                unique_name="AccountHeader",
                columns=[
                    SMLColumn(unique_name="AccountID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="Balance", data_type=DataType.FLOAT),
                ],
            ),
            # Detail table with no metrics
            SMLDataset(
                unique_name="AccountDetail",
                columns=[
                    SMLColumn(unique_name="AccountID", data_type=DataType.INTEGER, is_key=True),
                    SMLColumn(unique_name="ExtraNotes", data_type=DataType.STRING),
                ],
            ),
        ],
        relationships=[
            # Originally defined as AccountDetail -> AccountHeader
            SMLRelationship(
                unique_name="rel_detail_header",
                from_dataset="AccountDetail",
                from_columns=["AccountID"],
                to_dataset="AccountHeader",
                to_columns=["AccountID"],
                cardinality=Cardinality.ONE_TO_ONE,
                is_active=True,
            ),
        ],
        metrics=[
            # Metrics are on AccountHeader
            SMLMetric(
                unique_name="TotalBalance",
                dataset="AccountHeader",
                expression="SUM('AccountHeader'[Balance])",
                sql_expression='SUM(ACCOUNTHEADER."BALANCE")',
                aggregation=AggregationType.SUM,
                sync_enabled=True,
            )
        ],
    )
    emitter = SnowflakeEmitter(_make_config())
    ddls = emitter.generate_ddls(model)
    ddl = ddls[0]

    # Must be emitted FROM AccountHeader (which holds metrics) TO AccountDetail
    assert 'ACCOUNTHEADER ("ACCOUNTID") REFERENCES ACCOUNTDETAIL ("ACCOUNTID")' in ddl
