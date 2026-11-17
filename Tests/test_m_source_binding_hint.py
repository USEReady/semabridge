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

