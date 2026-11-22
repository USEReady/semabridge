"""Databricks metric views publish the names the dry run shows.

Regression (proj-dbt5, Adventure Works): fields deployed as
``sale_salesorderlinekey`` -- a 4-letter table prefix plus the lowercased
column -- while the dry run promised ``SalesOrderLineKey``. Hidden surrogate
keys were published as fields, nothing carried a description / display name,
and a root table named ``orders`` got 8 hard-coded TPC-H sample dimensions.
"""

from __future__ import annotations

from unittest.mock import patch

import yaml

from semabridge.connectors.databricks_publisher import VIEW_TYPE_METRIC, DatabricksPublisher
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
    SMLRelationship,
)
from semabridge.utils.identifiers import databricks_target_name


def _publisher() -> DatabricksPublisher:
    cfg = DatabricksConfig(host="offline.databricks.local", token="x", warehouse_id="wh", catalog="workspace", schema_name="default")
    behavior = ConnectorBehavior(
        databricks=DatabricksBehavior(
            model_artifact_mode="per_model",
            measure_view_type="metric_view",
            enable_metric_view_joins=True,
            enable_cross_table_joins=True,
        )
    )
    return DatabricksPublisher(cfg, behavior=behavior)


def _model() -> SMLModel:
    return SMLModel(
        unique_name="Adventure Works",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                is_fact=True,
                columns=[
                    SMLColumn(unique_name="SalesOrderLineKey", data_type=DataType.INTEGER, is_key=True, is_hidden=True),
                    SMLColumn(unique_name="CustomerKey", data_type=DataType.INTEGER, is_hidden=True),
                    SMLColumn(unique_name="Order Quantity", label="Order Quantity", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Name", data_type=DataType.STRING),
                ],
            ),
            SMLDataset(
                unique_name="Customer",
                columns=[
                    SMLColumn(unique_name="CustomerKey", data_type=DataType.INTEGER, is_key=True, is_hidden=True),
                    SMLColumn(
                        unique_name="Country_Region",
                        label="Country-Region",
                        description="Customer country or region",
                        synonyms=["Country", "Nation", "country-region"],
                        data_type=DataType.STRING,
                    ),
                    SMLColumn(unique_name="Name", data_type=DataType.STRING),
                ],
            ),
        ],
        relationships=[
            SMLRelationship(
                unique_name="sales_customer",
                from_dataset="Sales",
                from_columns=["CustomerKey"],
                to_dataset="Customer",
                to_columns=["CustomerKey"],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Quantity",
                label="Total Quantity",
                description="Units sold",
                dataset="Sales",
                source_column="Order Quantity",
                aggregation=AggregationType.SUM,
            )
        ],
    )


def _yaml(model: SMLModel) -> dict:
    pub = _publisher()
    with patch.object(pub, "_resolve_existing_source_for_dataset", side_effect=lambda d, e: e), patch.object(
        pub, "execute_statements", return_value=[]
    ):
        stmts, created, _, _ = pub.generate_measure_view_statements(model, view_type_override=VIEW_TYPE_METRIC)
    assert created == 1
    body = stmts[0].split("$$\n", 1)[1].rsplit("\n$$", 1)[0]
    return yaml.safe_load(body)


def _dims(doc: dict) -> dict:
    return {d["name"]: d for d in doc["dimensions"]}


def test_dimension_names_follow_the_dry_run_rule():
    dims = _dims(_yaml(_model()))
    assert databricks_target_name("Order Quantity", entity_kind="column") in dims  # Order_Quantity
    assert "Country_Region" in dims
    assert not any(name.startswith(("sale_", "cust_")) for name in dims)


def test_hidden_surrogate_keys_are_not_published_as_fields():
    names = {n.lower() for n in _dims(_yaml(_model()))}
    assert not any("customerkey" in n or "salesorderlinekey" in n for n in names)


def test_same_column_in_two_tables_is_qualified_in_both():
    dims = _dims(_yaml(_model()))
    assert {"Sales_Name", "Customer_Name"} <= set(dims)
    assert "Name" not in dims


def test_business_metadata_is_carried_over():
    doc = _yaml(_model())
    country = _dims(doc)["Country_Region"]
    assert country["display_name"] == "Country-Region"
    assert country["comment"] == "Customer country or region"
    # Label duplicates are dropped; real synonyms kept.
    assert country["synonyms"] == ["Country", "Nation"]
    measure = doc["measures"][0]
    assert measure["name"] == "Total_Quantity"
    assert measure["display_name"] == "Total Quantity"
    assert measure["comment"] == "Units sold"
    # No description -> no invented comment (same as Snowflake).
    assert "comment" not in _dims(doc)["Order_Quantity"]


def test_measure_named_like_a_dimension_gets_msr_prefix():
    model = _model()
    model.metrics[0].unique_name = "Order Quantity"
    doc = _yaml(model)
    assert "Order_Quantity" in _dims(doc)
    assert doc["measures"][0]["name"] == "MSR_Order_Quantity"


def test_orders_root_gets_no_hard_coded_sample_dimensions():
    model = _model()
    model.datasets[0].unique_name = "orders"
    model.relationships[0].from_dataset = "orders"
    model.metrics[0].dataset = "orders"
    dims = _dims(_yaml(model))
    assert not {"order_date", "order_status", "customer_name", "market_segment"} & set(dims)


def test_dry_run_note_states_the_databricks_name():
    from semabridge.api.services.project_mapping_engine import _flag_hidden_collisions

    rows = [
        {"entity_kind": "column", "model_name": "AW", "parent_source_path": "datasets.Sales", "target_name": "Name"},
        {"entity_kind": "column", "model_name": "AW", "parent_source_path": "datasets.Customer", "target_name": "Name"},
    ]
    _flag_hidden_collisions(rows, "databricks")
    assert rows[0]["databricks_deployed_name"] == "Sales_Name"
    assert rows[1]["databricks_deployed_name"] == "Customer_Name"
    assert "published as Customer_Name" in rows[1]["advisory_notes"][0]
    assert DatabricksPublisher.shared_column_names(_model()) >= {"NAME"}

    snow = [dict(r, advisory_notes=[]) for r in rows]
    for r in snow:
        r.pop("databricks_deployed_name", None)
    _flag_hidden_collisions(snow, "snowflake")
    assert "databricks_deployed_name" not in snow[0]
