import pytest
from semabridge.api.routers.comparator_router import parse_yaml_to_normalized, compare_entities

SNOWFLAKE_BASE = """
semantic_model:
  name: "ecommerce_model"
  tables:
    - name: "orders"
      base_table: "prod.public.orders"
      dimensions:
        - name: "order_id"
          expr: "order_id"
          data_type: "number"
        - name: "status"
          expr: "status"
          data_type: "varchar"
      measures:
        - name: "total_orders"
          expr: "COUNT(order_id)"
          description: "Count of all orders"
  relationships:
    - name: "orders_customers"
      left_table: "orders"
      right_table: "customers"
      left_columns:
        - "customer_id"
      right_columns:
        - "id"
      relationship_type: "many_to_one"
"""

SNOWFLAKE_MODIFIED = """
semantic_model:
  name: "ecommerce_model"
  tables:
    - name: "orders"
      base_table: "prod.public.orders"
      dimensions:
        - name: "order_id"
          expr: "order_id"
          data_type: "number"
        - name: "status"
          expr: "status"
          data_type: "varchar"
        - name: "region"
          expr: "region_code"
          data_type: "varchar"
      measures:
        - name: "total_orders"
          expr: "COUNT(order_id) IFF(status != 'CANCELLED')"
          description: "Count of completed orders"
        - name: "total_revenue"
          expr: "SUM(amount)"
  relationships:
    - name: "orders_customers"
      left_table: "orders"
      right_table: "customers"
      left_columns:
        - "customer_id"
      right_columns:
        - "id"
      relationship_type: "many_to_one"
"""

def test_snowflake_parsing():
    norm = parse_yaml_to_normalized(SNOWFLAKE_BASE)
    
    assert norm["format"] == "SNOWFLAKE"
    assert "orders" in norm["tables"]
    assert norm["tables"]["orders"]["column_count"] == 2
    assert norm["tables"]["orders"]["metric_count"] == 1
    assert norm["tables"]["orders"]["relationship_count"] == 1
    
    assert "orders.order_id" in norm["columns"]
    assert norm["columns"]["orders.order_id"]["type"] == "number"
    
    assert "orders.total_orders" in norm["metrics"]
    assert norm["metrics"]["orders.total_orders"]["definition"] == "COUNT(order_id)"
    
    assert "orders_customers" in norm["relationships"]
    assert norm["relationships"]["orders_customers"]["left_table"] == "orders"
    assert norm["relationships"]["orders_customers"]["right_table"] == "customers"
    assert norm["relationships"]["orders_customers"]["cardinality"] == "many_to_one"

def test_snowflake_diffing():
    norm1 = parse_yaml_to_normalized(SNOWFLAKE_BASE)
    norm2 = parse_yaml_to_normalized(SNOWFLAKE_MODIFIED)
    
    # Compare Columns
    cols_diff = compare_entities(norm1["columns"], norm2["columns"], "columns")
    
    # "orders.order_id", "orders.status" should be identical
    identical_cols = [c for c in cols_diff if c.get("_diff_status") == "identical"]
    assert len(identical_cols) == 2
    
    # "orders.region" should be only_in_2
    added_cols = [c for c in cols_diff if c.get("_diff_status") == "only_in_2"]
    assert len(added_cols) == 1
    assert added_cols[0]["name"] == "region"
    
    # Compare Metrics
    metrics_diff = compare_entities(norm1["metrics"], norm2["metrics"], "metrics")
    
    # "total_revenue" should be only_in_2
    added_metrics = [m for m in metrics_diff if m.get("_diff_status") == "only_in_2"]
    assert len(added_metrics) == 1
    assert added_metrics[0]["name"] == "total_revenue"
    
    # "total_orders" should be modified, generating 2 rows: modified_in_1 and modified_in_2
    modified_m1 = [m for m in metrics_diff if m.get("_diff_status") == "modified_in_1"]
    modified_m2 = [m for m in metrics_diff if m.get("_diff_status") == "modified_in_2"]
    assert len(modified_m1) == 1
    assert len(modified_m2) == 1
    assert modified_m1[0]["name"] == "total_orders"
    assert modified_m2[0]["name"] == "total_orders"
    
    # Ensure changes are tracked
    assert len(modified_m1[0]["_changes"]) > 0
    change_def = next(c for c in modified_m1[0]["_changes"] if c["field"] == "definition")
    assert change_def["old_value"] == "COUNT(order_id)"
    assert change_def["new_value"] == "COUNT(order_id) IFF(status != 'CANCELLED')"
