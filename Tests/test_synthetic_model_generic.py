"""Tests verifying that table binding, fact detection, PK resolution,
and metric re-anchoring are completely generic using a synthetic model
(ORDERS, CUSTOMERS, STORES, RETURNS).
"""

import pytest
from unittest.mock import MagicMock
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship
)
from semabridge.connectors.table_binding_resolver import rank_table_candidates
from semabridge.connectors.ddl_builder import SemanticViewBuilder
from semabridge.core.drop_ledger import DropLedger

def test_synthetic_table_binding_column_compatibility():
    """Verifies candidate ranking purely by column compatibility and normalized name."""
    modeled_cols = ["ORDER_ID", "CUSTOMER_ID", "STORE_ID", "ORDER_DATE", "TOTAL_AMOUNT", "DISCOUNT"]
    live_meta = {
        "ORDERS_V1": {"ORDER_ID", "CUSTOMER_ID", "STORE_ID", "ORDER_DATE"},  # 4/6 = 0.667 compat
        "ORDER_FACT": {"ORDER_ID", "CUSTOMER_ID", "STORE_ID", "ORDER_DATE", "TOTAL_AMOUNT", "DISCOUNT", "STATUS"}, # 6/6 = 1.0 compat
        "ORDERS": {"ORDER_ID", "CUSTOMER_ID"}, # 2/6 = 0.333 compat
    }
    
    ranked = rank_table_candidates("Orders", modeled_cols, live_meta)
    assert len(ranked) >= 2
    # ORDER_FACT has 1.0 column compatibility and wins over ORDERS (0.333 compat)
    assert ranked[0]["table_name"] == "ORDER_FACT"
    assert ranked[0]["col_compat"] == 1.0

def test_synthetic_generic_reachability_reanchor():
    """Verifies metric re-anchoring via many-to-one reachability on ORDERS, CUSTOMERS, STORES, RETURNS."""
    # Relationships:
    # ORDERS (many) -> CUSTOMERS (one)
    # ORDERS (many) -> STORES (one)
    # RETURNS (many) -> ORDERS (one)
    
    orders = SMLDataset(unique_name="Orders", columns=[SMLColumn(unique_name="OrderID"), SMLColumn(unique_name="Amount")])
    customers = SMLDataset(unique_name="Customers", columns=[SMLColumn(unique_name="CustomerID"), SMLColumn(unique_name="CustomerName")])
    stores = SMLDataset(unique_name="Stores", columns=[SMLColumn(unique_name="StoreID"), SMLColumn(unique_name="City")])
    returns = SMLDataset(unique_name="Returns", columns=[SMLColumn(unique_name="ReturnID"), SMLColumn(unique_name="ReturnReason")])
    
    rel_orders_cust = SMLRelationship(
        unique_name="Rel_Orders_Cust",
        from_dataset="Orders", from_columns=["CustomerID"],
        to_dataset="Customers", to_columns=["CustomerID"],
        cardinality="many-to-one", is_active=True
    )
    rel_orders_stores = SMLRelationship(
        unique_name="Rel_Orders_Stores",
        from_dataset="Orders", from_columns=["StoreID"],
        to_dataset="Stores", to_columns=["StoreID"],
        cardinality="many-to-one", is_active=True
    )
    rel_returns_orders = SMLRelationship(
        unique_name="Rel_Returns_Orders",
        from_dataset="Returns", from_columns=["OrderID"],
        to_dataset="Orders", to_columns=["OrderID"],
        cardinality="many-to-one", is_active=True
    )
    
    # Metric 1: Placed on Customers, but references Orders.Amount and Customers.CustomerName.
    # Orders can reach Customers via many-to-one. Customers cannot reach Orders.
    # So metric must be re-anchored to Orders!
    m1 = SMLMetric(
        unique_name="CustomerOrderSpend",
        dataset="Customers",
        sql_expression="SUM(Orders.Amount)"
    )
    
    # Metric 2: References Returns and Stores.
    # Returns can reach Orders -> Stores. So Returns can reach both Returns and Stores!
    # Placed on Stores -> should re-anchor to Returns!
    m2 = SMLMetric(
        unique_name="StoreReturnRate",
        dataset="Stores",
        sql_expression="COUNT(Returns.ReturnID) / COUNT(Stores.StoreID)"
    )

    ledger = DropLedger()
    builder = SemanticViewBuilder(
        config=MagicMock(database="DB", schema_name="SC"),
        behavior=MagicMock(),
        identifier_sanitizer=MagicMock(),
        live_schema_metadata={},
        drop_ledger=ledger
    )
    
    model = SMLModel(
        unique_name="RetailModel",
        datasets=[orders, customers, stores, returns],
        relationships=[rel_orders_cust, rel_orders_stores, rel_returns_orders],
        metrics=[m1, m2]
    )
    
    builder._reanchor_disconnected_fact_metrics(model)
    
    # Check that m1 re-anchored to Orders
    assert m1.dataset == "Orders"
    # Check that m2 re-anchored to Returns
    assert m2.dataset == "Returns"

def test_synthetic_unreachable_metric_emitted_as_placeholder_with_reason():
    """Verifies that if a metric references disconnected tables with no common root, it is emitted as a placeholder."""
    ds_a = SMLDataset(unique_name="TableA", columns=[SMLColumn(unique_name="ColA")])
    ds_b = SMLDataset(unique_name="TableB", columns=[SMLColumn(unique_name="ColB")])
    
    # Disconnected tables (no relationships between them)
    m_cross = SMLMetric(
        unique_name="CrossMetric",
        dataset="TableA",
        sql_expression="SUM(TableA.ColA) + SUM(TableB.ColB)"
    )
    
    ledger = DropLedger()
    builder = SemanticViewBuilder(
        config=MagicMock(database="DB", schema_name="SC"),
        behavior=MagicMock(),
        identifier_sanitizer=MagicMock(),
        live_schema_metadata={},
        drop_ledger=ledger
    )
    
    model = SMLModel(
        unique_name="DisconnectedModel",
        datasets=[ds_a, ds_b],
        relationships=[],
        metrics=[m_cross]
    )
    
    builder._reanchor_disconnected_fact_metrics(model)
    # Emitted as placeholder in surviving metrics
    assert m_cross in model.metrics
    assert m_cross.is_placeholder is True
    assert m_cross.sql_expression == "CAST(NULL AS DOUBLE)"
    # Dropped in ledger with explicit placeholder reason
    drops = [d for d in ledger.records if d.entity_name == "CrossMetric"]
    assert len(drops) == 1
    assert "placeholder" in drops[0].reason.lower()


def test_synthetic_keys_come_from_schema_metadata_never_from_data(caplog):
    """Declared Snowflake primary keys are read from metadata (SHOW PRIMARY
    KEYS); a key that isn't unique in the data only logs a warning -- the
    data never decides a key, completely free of hardcoded names."""
    import logging
    from semabridge.connectors.schema_manager import SnowflakeSchemaManager

    mock_cursor = MagicMock()
    schema_mgr = SnowflakeSchemaManager(
        config=MagicMock(schema_name="SC", database="DB"),
        behavior=MagicMock(),
        identifier_sanitizer=MagicMock(),
        connection_manager=MagicMock(),
    )

    mock_cursor.description = [("created_on",), ("database_name",), ("schema_name",), ("table_name",),
                               ("column_name",), ("key_sequence",)]
    mock_cursor.fetchall.return_value = [
        (None, "DB", "SC", "CUSTOMERS", "CUSTOMER_ID", 1),
        (None, "DB", "SC", "ORDER_LINES", "LINE_NO", 2),
        (None, "DB", "SC", "ORDER_LINES", "ORDER_ID", 1),
    ]
    keys = schema_mgr.declared_primary_keys(cursor=mock_cursor)
    assert keys == {"CUSTOMERS": ["CUSTOMER_ID"], "ORDER_LINES": ["ORDER_ID", "LINE_NO"]}

    mock_cursor.fetchone.return_value = (100, 40)
    with caplog.at_level(logging.WARNING):
        assert schema_mgr.warn_if_key_not_unique("CUSTOMERS", ["CUSTOMER_ID"], cursor=mock_cursor) is None
    assert "not unique in the data" in caplog.text


def test_join_keys_must_come_only_from_model_relationships_not_shared_non_key_columns():
    """Verify that when two tables share non-key columns (e.g. DATE, ZIP, STATUS),
    join keys are NEVER guessed from shared column intersection or fallback dummy ID.
    They must come strictly from model relationships or return None to drop/skip.
    """
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.sml.models import SMLModel, SMLDataset, SMLRelationship
    
    cfg = SnowflakeConfig(
        account="acc", user="usr", warehouse="wh",
        database="TEST_DB", schema_name="TEST_SCHEMA"
    )
    emitter = SnowflakeEmitter(cfg)
    
    # Two tables that share non-key columns: DATE, ZIP, STATUS
    ds_orders = SMLDataset(
        unique_name="ORDERS",
        columns=[
            SMLColumn(unique_name="ORDER_ID", is_key=True),
            SMLColumn(unique_name="DATE"),
            SMLColumn(unique_name="ZIP"),
            SMLColumn(unique_name="STATUS"),
            SMLColumn(unique_name="STORE_ID"),
        ]
    )
    ds_stores = SMLDataset(
        unique_name="STORES",
        columns=[
            SMLColumn(unique_name="STORE_ID", is_key=True),
            SMLColumn(unique_name="DATE"),
            SMLColumn(unique_name="ZIP"),
            SMLColumn(unique_name="STATUS"),
        ]
    )
    
    # Case 1: NO relationship defined between ORDERS and STORES
    model_without_rel = SMLModel(
        unique_name="TestSharedColsModel",
        datasets=[ds_orders, ds_stores],
        relationships=[],
        metrics=[]
    )
    emitter._model = model_without_rel
    
    # Must NOT join on shared non-key columns (DATE, ZIP, STATUS) and must NOT return dummy "ID"
    assert emitter._get_join_key("ORDERS", "STORES") is None
    assert emitter._get_directional_join_key("ORDERS", "STORES", from_side="ORDERS") is None
    assert emitter._get_directional_join_key("ORDERS", "STORES", from_side="STORES") is None
    
    # Case 2: Explicit relationship defined on bound column STORE_ID
    rel = SMLRelationship(
        unique_name="Rel_Orders_Stores",
        from_dataset="ORDERS",
        to_dataset="STORES",
        from_columns=["STORE_ID"],
        to_columns=["STORE_ID"],
        is_active=True
    )
    model_with_rel = SMLModel(
        unique_name="TestSharedColsModel",
        datasets=[ds_orders, ds_stores],
        relationships=[rel],
        metrics=[]
    )
    emitter._model = model_with_rel
    
    # Must return the bound relationship column STORE_ID
    assert emitter._get_join_key("ORDERS", "STORES") == "STORE_ID"
    assert emitter._get_directional_join_key("ORDERS", "STORES", from_side="ORDERS") == "STORE_ID"
    assert emitter._get_directional_join_key("ORDERS", "STORES", from_side="STORES") == "STORE_ID"

