"""Regression (proj-nat1): a structure-only stub of a snowflake-shaped model
(fact -> product -> manufacturer/category) failed with
INVALID_EXTRACT_BASE_FIELD_TYPE "Can't extract a value from product": the
rewrite kept references like product.product and product.category.channel
after removing the product join. Every reference through a removed join, at
any depth, must be flattened (root missing) or NULL (root kept)."""
import re

import yaml

from semabridge.connectors.databricks_publisher import DatabricksPublisher
from semabridge.core.behavior import ConnectorBehavior, DatabricksBehavior
from semabridge.core.settings import DatabricksConfig
from semabridge.sml.models import SMLModel

YAML_BODY = {
    "version": "1.1",
    "comment": "Semabridge: Competitive - Model Metric View",
    "source": "semabridge.public.fact",
    "joins": [{
        "name": "product",
        "source": "semabridge.public.product",
        "using": ["productid"],
        "joins": [
            {"name": "manufacturer", "source": "workspace.default.Manufacturer", "using": ["manufacturerid"]},
            {"name": "category", "source": "workspace.default.Category", "using": ["segment"]},
        ],
    }],
    "dimensions": [
        {"name": "Product", "expr": "product.product_name"},
        {"name": "Manufacturer_Manufacturer", "expr": "product.manufacturer.manufacturer"},
        {"name": "Channel", "expr": "product.category.channel"},
    ],
    "measures": [
        {"name": "Count_of_Product", "expr": "count(product.product)"},
        {"name": "VanArsdel_Units", "expr": "sum(case when product.isvanarsdel = 'Yes.Really' then sale_units else null end)"},
        {"name": "Ratio", "expr": "sum(sale_units) * 1.5"},
    ],
}


def _pub():
    return DatabricksPublisher(
        DatabricksConfig(host="x.cloud.databricks.com", token="t", warehouse_id="w", catalog="workspace", schema_name="default"),
        behavior=ConnectorBehavior(databricks=DatabricksBehavior(measure_view_type="metric_view")),
    )


def _rewrite(body, missing):
    sql = ("CREATE OR REPLACE VIEW `workspace`.`default`.`mv` WITH METRICS LANGUAGE YAML AS $$\n"
           + yaml.safe_dump(body, sort_keys=False) + "\n$$")
    out = _pub()._rewrite_metric_view_sql_for_missing_sources(sql, SMLModel(unique_name="m", datasets=[]), missing)
    return yaml.safe_load(out.split("$$")[1])


def _exprs(doc):
    return [d["expr"] for d in doc.get("dimensions", [])] + [m["expr"] for m in doc.get("measures", [])]


def test_all_missing_flattens_every_reference_through_removed_joins():
    doc = _rewrite(YAML_BODY, ["semabridge.public.fact", "semabridge.public.product",
                               "workspace.default.Manufacturer", "workspace.default.Category"])
    assert "joins" not in doc
    joined = " ".join(_exprs(doc))
    assert not re.search(r"(?<![A-Za-z0-9_`])(product|manufacturer|category)\.", joined)
    measures = {m["name"]: m["expr"] for m in doc["measures"]}
    assert measures["Count_of_Product"] == "count(`product_product`)"
    assert "'Yes.Really'" in measures["VanArsdel_Units"]  # string literal untouched
    assert measures["Ratio"] == "sum(sale_units) * 1.5"  # decimal literal untouched
    assert "`product_product`" in doc["source"] and "WHERE 1=0" in doc["source"]


def test_missing_nested_table_under_a_kept_parent():
    # fact and product exist; only manufacturer is missing.
    doc = _rewrite(YAML_BODY, ["workspace.default.Manufacturer"])
    assert doc["source"] == "semabridge.public.fact"
    product = doc["joins"][0]
    assert [j["name"] for j in product["joins"]] == ["category"]
    dims = {d["name"]: d["expr"] for d in doc["dimensions"]}
    assert dims["Manufacturer_Manufacturer"].startswith("CAST(NULL AS ")
    assert dims["Channel"] == "product.category.channel"
    assert dims["Product"] == "product.product_name"

