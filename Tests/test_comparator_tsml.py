import pytest
from semabridge.api.routers.comparator_router import parse_yaml_to_normalized, compare_entities

TSML_BASE = """
model:
  name: "SalesModel"
  tables:
    - name: "sales"
      columns:
        - name: "order_id"
          dataType: "int64"
          isKey: true
        - name: "product_id"
          dataType: "int64"
        - name: "amount"
          dataType: "double"
      measures:
        - name: "total_sales"
          expression: "SUM(sales[amount])"
          description: "Total sales amount"
  relationships:
    - name: "sales_products"
      fromTable: "sales"
      toTable: "products"
      fromColumn: "product_id"
      toColumn: "product_id"
      crossFilteringBehavior: "bothDirections"
"""

TSML_MODIFIED = """
model:
  name: "SalesModel"
  tables:
    - name: "sales"
      columns:
        - name: "order_id"
          dataType: "int64"
          isKey: true
        - name: "product_id"
          dataType: "int64"
        - name: "amount"
          dataType: "double"
        - name: "discount"
          dataType: "double"
      measures:
        - name: "total_sales"
          expression: "SUM(sales[amount]) - SUM(sales[discount])"
          description: "Net sales amount after discount"
        - name: "average_sale"
          expression: "AVERAGE(sales[amount])"
  relationships:
    - name: "sales_products"
      fromTable: "sales"
      toTable: "products"
      fromColumn: "product_id"
      toColumn: "product_id"
      crossFilteringBehavior: "bothDirections"
"""

def test_tsml_parsing():
    norm = parse_yaml_to_normalized(TSML_BASE)
    
    assert norm["format"] == "TSML"
    assert "sales" in norm["tables"]
    assert norm["tables"]["sales"]["column_count"] == 3
    assert norm["tables"]["sales"]["metric_count"] == 1
    assert norm["tables"]["sales"]["relationship_count"] == 1
    
    assert "sales.order_id" in norm["columns"]
    assert norm["columns"]["sales.order_id"]["is_key"] is True
    assert norm["columns"]["sales.order_id"]["type"] == "int64"
    
    assert "sales.total_sales" in norm["metrics"]
    assert norm["metrics"]["sales.total_sales"]["definition"] == "SUM(sales[amount])"
    
    assert "sales_products" in norm["relationships"]
    assert norm["relationships"]["sales_products"]["left_table"] == "sales"
    assert norm["relationships"]["sales_products"]["right_table"] == "products"

def test_tsml_diffing():
    norm1 = parse_yaml_to_normalized(TSML_BASE)
    norm2 = parse_yaml_to_normalized(TSML_MODIFIED)
    
    # Compare Columns
    cols_diff = compare_entities(norm1["columns"], norm2["columns"], "columns")
    
    # "sales.order_id", "sales.product_id", "sales.amount" should be identical
    identical_cols = [c for c in cols_diff if c.get("_diff_status") == "identical"]
    assert len(identical_cols) == 3
    
    # "sales.discount" should be only_in_2
    added_cols = [c for c in cols_diff if c.get("_diff_status") == "only_in_2"]
    assert len(added_cols) == 1
    assert added_cols[0]["name"] == "discount"
    
    # Compare Metrics
    metrics_diff = compare_entities(norm1["metrics"], norm2["metrics"], "metrics")
    
    # "average_sale" should be only_in_2
    added_metrics = [m for m in metrics_diff if m.get("_diff_status") == "only_in_2"]
    assert len(added_metrics) == 1
    assert added_metrics[0]["name"] == "average_sale"
    
    # "total_sales" should be modified, generating 2 rows: modified_in_1 and modified_in_2
    modified_m1 = [m for m in metrics_diff if m.get("_diff_status") == "modified_in_1"]
    modified_m2 = [m for m in metrics_diff if m.get("_diff_status") == "modified_in_2"]
    assert len(modified_m1) == 1
    assert len(modified_m2) == 1
    assert modified_m1[0]["name"] == "total_sales"
    assert modified_m2[0]["name"] == "total_sales"
    
    # Ensure changes are tracked
    assert len(modified_m1[0]["_changes"]) > 0
    change_def = next(c for c in modified_m1[0]["_changes"] if c["field"] == "definition")
    assert change_def["old_value"] == "SUM(sales[amount])"
    assert change_def["new_value"] == "SUM(sales[amount]) - SUM(sales[discount])"
