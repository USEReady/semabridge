import os
import shutil
import tempfile
import pytest
from semabridge.connectors.tmdl_emitter import FabricTmdlEmitter
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


def test_fabric_tmdl_emission():
    dataset_orders = OSIDataset(
        unique_name="ORDERS",
        label="Orders",
        description="Completed orders",
        columns=[
            OSIColumn(unique_name="ORDER_ID", label="Order ID", data_type=OSIDataType.INTEGER, is_key=True),
            OSIColumn(unique_name="CUSTOMER_ID", label="Customer ID", data_type=OSIDataType.INTEGER),
        ],
    )
    metric_rev = OSIMetric(
        unique_name="TOTAL_REVENUE",
        label="Total Revenue",
        dataset="ORDERS",
        expression="SUM('Orders'[Amount])",
        aggregation=OSIAggregationType.SUM,
        description="Gross sales amount",
        format_string="$#,0.00",
    )
    rel = OSIRelationship(
        unique_name="REL_ORDERS_CUSTOMER_ID__CUSTOMERS_CUSTOMER_ID",
        from_dataset="ORDERS",
        from_columns=["CUSTOMER_ID"],
        to_dataset="CUSTOMERS",
        to_columns=["CUSTOMER_ID"],
        cardinality=OSICardinality.MANY_TO_ONE,
    )
    model = OSIModel(
        unique_name="SALES_MODEL",
        label="Sales Model",
        datasets=[dataset_orders],
        metrics=[metric_rev],
        relationships=[rel],
    )

    tmp_dir = tempfile.mkdtemp()
    try:
        emitter = FabricTmdlEmitter()
        files = emitter.emit_tmdl_files(model, tmp_dir)

        assert "model.tmdl" in files
        assert "tables/Orders.tmdl" in files
        assert "relationships.tmdl" in files

        with open(os.path.join(tmp_dir, "model.tmdl"), "r") as f:
            content = f.read()
            assert "model Sales Model" in content

        with open(os.path.join(tmp_dir, "tables/Orders.tmdl"), "r") as f:
            content = f.read()
            assert "table 'Orders'" in content
            assert "column 'Order ID'" in content
            assert "measure 'Total Revenue' = SUM('Orders'[Amount])" in content
            assert "formatString: $#,0.00" in content

        with open(os.path.join(tmp_dir, "relationships.tmdl"), "r") as f:
            content = f.read()
            assert "relationship REL_ORDERS_CUSTOMER_ID__CUSTOMERS_CUSTOMER_ID" in content
    finally:
        shutil.rmtree(tmp_dir)
