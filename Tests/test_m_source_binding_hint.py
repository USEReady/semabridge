from __future__ import annotations

import pytest

from semabridge.connectors.table_binding_resolver import (
    LiveSchemaMetadata,
    resolve_dataset_to_physical_table,
)
from semabridge.converter.m_source import snowflake_binding_hint


def test_m_source_navigation_parsing():
    partitions = [
        {
            "name": "Partition",
            "source": {
                "type": "m",
                "expression": [
                    "let",
                    '    Source = Snowflake.Databases("myaccount.snowflakecomputing.com", "COMPUTE_WH"),',
                    '    DB = Source{[Name="SALES_DB",Kind="Database"]}[Data],',
                    '    Schema = DB{[Name="PUBLIC",Kind="Schema"]}[Data],',
                    '    Table = Schema{[Name="FACT_ORDERS",Kind="Table"]}[Data]',
                    "in",
                    "    Table",
                ],
            },
        }
    ]
    hint = snowflake_binding_hint(partitions)
    assert hint is not None
    assert hint["database"] == "SALES_DB"
    assert hint["schema"] == "PUBLIC"
    assert hint["table"] == "FACT_ORDERS"
    assert hint["native_sql"] is None


def test_m_source_native_query_parsing():
    partitions = [
        {
            "name": "Partition",
            "source": {
                "type": "m",
                "expression": [
                    "let",
                    '    Source = Snowflake.Databases("host", "wh"),',
                    '    DB = Source{[Name="ANALYTICS",Kind="Database"]}[Data],',
                    '    Query = Value.NativeQuery(DB, "SELECT * FROM ""PUBLIC"".""DIM_CUSTOMERS"" WHERE id > 0", null, [EnableFolding=true])',
                    "in",
                    "    Query",
                ],
            },
        }
    ]
    hint = snowflake_binding_hint(partitions)
    assert hint is not None
    assert hint["database"] == "ANALYTICS"
    assert hint["schema"] == "PUBLIC"
    assert hint["table"] == "DIM_CUSTOMERS"
    assert 'SELECT * FROM "PUBLIC"."DIM_CUSTOMERS" WHERE id > 0' in hint["native_sql"]


def test_m_source_non_snowflake():
    partitions = [
        {
            "name": "Partition",
            "source": {
                "type": "m",
                "expression": [
                    "let",
                    '    Source = Sql.Databases("sqlserver.local"),',
                    '    DB = Source{[Name="LocalDb"]}[Data]',
                    "in",
                    "    DB",
                ],
            },
        }
    ]
    hint = snowflake_binding_hint(partitions)
    assert hint is None


def test_binding_resolver_m_source_flag():
    live_meta = LiveSchemaMetadata({
        "PHYSICAL_FACT_ORDERS": {"ORDER_ID", "AMOUNT", "CUSTOMER_ID"},
        "ORDERS": {"ORDER_ID", "AMOUNT", "CUSTOMER_ID"},
    })

    hint = {"database": "SALES_DB", "schema": "PUBLIC", "table": "PHYSICAL_FACT_ORDERS"}
    cols = ["ORDER_ID", "AMOUNT", "CUSTOMER_ID"]

    # Flag OFF: matches "ORDERS" by exact name
    res_off = resolve_dataset_to_physical_table(
        "Orders",
        cols,
        live_meta,
        source_hint=hint,
        bind_from_m_source=False,
    )
    assert res_off == "ORDERS"

    # Flag ON: uses hinted table "PHYSICAL_FACT_ORDERS"
    res_on = resolve_dataset_to_physical_table(
        "Orders",
        cols,
        live_meta,
        source_hint=hint,
        bind_from_m_source=True,
    )
    assert res_on == "PHYSICAL_FACT_ORDERS"

    # Flag ON but hinted table not in live schema: falls back to regular matching
    hint_missing = {"database": "SALES_DB", "schema": "PUBLIC", "table": "NONEXISTENT_TABLE"}
    res_fallback = resolve_dataset_to_physical_table(
        "Orders",
        cols,
        live_meta,
        source_hint=hint_missing,
        bind_from_m_source=True,
    )
    assert res_fallback == "ORDERS"


def test_hint_case_insensitive_unquoted_matching():
    from semabridge.connectors.table_binding_resolver import (
        identifier_matches,
        match_hint_to_live_table,
    )

    # Unquoted identifiers match case-insensitively (uppercase semantics)
    assert identifier_matches("orders", "ORDERS") is True
    assert identifier_matches("Sales_Fact", "SALES_FACT") is True
    assert identifier_matches("SALES_FACT", "sales_fact") is True

    # Multi-part matching
    hint = {"database": "sales_db", "schema": "public", "table": "orders"}
    assert match_hint_to_live_table(hint, "ORDERS") is True
    assert match_hint_to_live_table(hint, "orders") is True
    assert match_hint_to_live_table(hint, "PUBLIC.ORDERS") is True
    assert match_hint_to_live_table(hint, "SALES_DB.PUBLIC.ORDERS") is True

    # Live schema binding resolution with unquoted lower-case hint
    live_meta = LiveSchemaMetadata({
        "PHYSICAL_FACT_ORDERS": {"ORDER_ID", "AMOUNT"},
    })
    res = resolve_dataset_to_physical_table(
        "Orders",
        ["ORDER_ID", "AMOUNT"],
        live_meta,
        source_hint={"database": "sales_db", "schema": "public", "table": "physical_fact_orders"},
        bind_from_m_source=True,
    )
    assert res == "PHYSICAL_FACT_ORDERS"


def test_hint_exact_case_quoted_matching():
    from semabridge.connectors.table_binding_resolver import (
        identifier_matches,
        match_hint_to_live_table,
    )

    # Quoted identifier requires exact case
    assert identifier_matches('"Special_Orders"', "Special_Orders") is True
    assert identifier_matches('"Special_Orders"', "SPECIAL_ORDERS") is False
    assert identifier_matches('"Special_Orders"', "special_orders") is False

    hint_quoted = {"schema": '"Audit"', "table": '"Special_Events"'}
    assert match_hint_to_live_table(hint_quoted, '"Audit"."Special_Events"') is True
    assert match_hint_to_live_table(hint_quoted, 'Audit.Special_Events') is True
    # Different case on quoted table fails
    assert match_hint_to_live_table(hint_quoted, 'Audit.SPECIAL_EVENTS') is False
    # Different schema fails
    assert match_hint_to_live_table(hint_quoted, 'PUBLIC.Special_Events') is False

    # Resolution in live schema: distinguishes exact case quoted identifier
    live_meta = LiveSchemaMetadata({
        "Special_Orders": {"ID"},
        "SPECIAL_ORDERS": {"ID"},
    })
    res = resolve_dataset_to_physical_table(
        "MyOrders",
        ["ID"],
        live_meta,
        source_hint={"table": '"Special_Orders"'},
        bind_from_m_source=True,
    )
    assert res == "Special_Orders"


