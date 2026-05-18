from semabridge.converter.tmdl_to_osi import TMDLToOSIConverter


def test_tmdl_to_osi_simple_tables():
    # Simulate collected tmdl mapping with two table JSON files
    tmdl = {
        "tables/customers.json": {"columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "string"}]},
        "tables/orders.json": {"columns": [{"name": "order_id", "type": "int"}, {"name": "customer_id", "type": "int"}, {"name": "total", "type": "decimal"}]},
    }

    converter = TMDLToOSIConverter()
    osi = converter.to_osi({"tmdl": tmdl, "workspace_id": "ws-1", "dataset_id": "ds-1", "display_name": "TmdlModel"})

    assert osi.source_platform == "tmdl"
    assert len(osi.datasets) == 2
    names = {d.unique_name for d in osi.datasets}
    assert "customers" in names
    assert "orders" in names
    cust = next(d for d in osi.datasets if d.unique_name == "customers")
    assert any(c.unique_name == "id" for c in cust.columns)
    assert any(c.unique_name == "name" for c in cust.columns)


def test_tmdl_measures_and_relationships():
    tmdl = {
        "tables/customers.json": {"columns": [{"name": "id", "type": "int"}, {"name": "name", "type": "string"}], "measures": [{"name": "TotalCustomers", "expression": "COUNTROWS(customers)"}]},
        "tables/orders.json": {"columns": [{"name": "order_id", "type": "int"}, {"name": "customer_id", "type": "int"}, {"name": "total", "type": "decimal"}], "measures": [{"name": "SumTotal", "expression": "SUM(orders[total])"}]},
        "model.json": {"relationships": [{"fromTable": "orders", "fromColumn": "customer_id", "toTable": "customers", "toColumn": "id", "cardinality": "ManyToOne"}]}
    }

    converter = TMDLToOSIConverter()
    osi = converter.to_osi({"tmdl": tmdl, "workspace_id": "ws-1", "dataset_id": "ds-1", "display_name": "TmdlModel"})

    assert len(osi.metrics) == 2
    names = {m.unique_name for m in osi.metrics}
    assert "TotalCustomers" in names
    assert "SumTotal" in names

    assert len(osi.relationships) == 1
    rel = osi.relationships[0]
    assert rel.from_dataset == "orders"
    assert rel.to_dataset == "customers"
