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

def test_synthetic_unreachable_metric_dropped_with_reason():
    """Verifies that if a metric references disconnected tables with no common root, it is dropped."""
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
    # Must be dropped from metrics list
    assert m_cross not in model.metrics
    # Dropped in ledger with explicit reason
    drops = [d for d in ledger.records if d.entity_name == "CrossMetric"]
    assert len(drops) == 1
    assert "unreachable" in drops[0].reason.lower()


def test_synthetic_fact_detection_from_metadata():
    """Verifies that a table is identified as a fact if it is on the many side
    of relationships or has no verified-unique key, completely free of hardcoded names."""
    from semabridge.connectors.schema_manager import SnowflakeSchemaManager
    
    mock_cursor = MagicMock()
    
    schema_mgr = SnowflakeSchemaManager(
        config=MagicMock(schema_name="SC", database="DB"),
        behavior=MagicMock(),
        identifier_sanitizer=MagicMock(),
        connection_manager=MagicMock(),
    )
    
    def mock_cur_execute(sql, *args, **kwargs):
        pass
        
    mock_cursor.execute.side_effect = mock_cur_execute
    
    # Test ORDERS (no column has count(distinct) == total_count)
    mock_cursor.fetchall.return_value = [("DB", "SC", "ORDER_ID"), ("DB", "SC", "CUSTOMER_ID"), ("DB", "SC", "AMOUNT")]
    # total_count = 100, distinct counts = 50, 50, 20 (none unique)
    mock_cursor.fetchone.side_effect = [(100,), (50,), (50,), (20,)]
    orders_unique = schema_mgr.get_verified_unique_columns("ORDERS", cursor=mock_cursor)
    assert len(orders_unique) == 0  # 0 unique columns -> fact table!
    
    # Test CUSTOMERS (CUSTOMER_ID has count(distinct) == total_count = 50)
    mock_cursor.fetchall.return_value = [("DB", "SC", "CUSTOMER_ID"), ("DB", "SC", "NAME")]
    mock_cursor.fetchone.side_effect = [(50,), (50,), (40,)]
    cust_unique = schema_mgr.get_verified_unique_columns("CUSTOMERS", cursor=mock_cursor)
    assert "CUSTOMER_ID" in cust_unique  # CUSTOMER_ID is verified unique -> Dimension table!


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

