"""Name clashes the per-table / per-model check could not see before.

1. Same name in different tables (CUSTOMER.NAME / PRODUCT.NAME): deploys
   fine, so NOT a collision -- only an advisory (bare NAME is ambiguous).
2. Two columns of one table that become the same Snowflake name
   ('Order Date' / 'Order-Date'): conversion renames the second to _2;
   the dry run now shows it as a collision so the user picks the name.
3. A metric named like a column of its own table: the emitter would drop
   the column; the metric is now a collision.
4. Suggestions never repeat the table name, and Resolve All (first
   suggestion of every collision) leaves no collision and keeps every
   column in the deployed view.
"""
from __future__ import annotations

import logging
from pathlib import Path

from semabridge.api.services.project_mapping_engine import build_entity_mappings
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.config import _apply_mapping_overrides_from_config
from semabridge.core.settings import SnowflakeConfig
from semabridge.intermediate.models import (
    OSIAggregationType, OSIColumn, OSIDataset, OSIDataType, OSIMetric, OSIModel,
)

S, F = OSIDataType.STRING, OSIDataType.FLOAT


def _model():
    return OSIToSMLConverter().from_osi(OSIModel(
        unique_name="Demo", label="Demo", source_platform="pbix",
        datasets=[
            OSIDataset(unique_name="Customer", columns=[OSIColumn(unique_name="Name", data_type=S)]),
            OSIDataset(unique_name="Product", columns=[
                OSIColumn(unique_name="Name", data_type=S),
                OSIColumn(unique_name="Order Date", data_type=S),
                OSIColumn(unique_name="Order-Date", data_type=S),
            ]),
            OSIDataset(unique_name="Sales", columns=[
                OSIColumn(unique_name="Amount", data_type=F),
                OSIColumn(unique_name="Region", data_type=S),
            ]),
        ],
        metrics=[
            OSIMetric(unique_name="Sales %", dataset="Sales", source_column="Amount", aggregation=OSIAggregationType.SUM),
            OSIMetric(unique_name="Sales_%", dataset="Sales", source_column="Amount", aggregation=OSIAggregationType.SUM),
            OSIMetric(unique_name="Region", dataset="Sales", source_column="Amount", aggregation=OSIAggregationType.SUM),
        ],
        relationships=[],
    ))


def _rows(existing=None):
    out = build_entity_mappings(
        project_id="p", model=_model().model_dump(mode="json"),
        target_connector="snowflake", existing_mappings=existing,
    )
    return {r["source_path"]: r for r in out["mappings"]}


def test_same_name_in_different_tables_is_advisory_not_collision():
    rows = _rows()
    for path in ("datasets.Customer.columns.Name", "datasets.Product.columns.Name"):
        assert rows[path]["collision_detected"] is False
        assert rows[path]["shared_name_tables"] == ["Customer", "Product"]
        assert any("ambiguous" in n for n in rows[path]["advisory_notes"])
        assert rows[path]["validation_code"] == "OK"  # never blocks


def test_column_renamed_during_conversion_is_a_collision():
    row = _rows()["datasets.Product.columns.Order-Date_2"]
    assert row["collision_detected"] is True and row["validation_code"] == "NAME_COLLISION"
    assert "'Order-Date'" in row["validation_message"]
    # Suggested from the real name, not the generated ORDER_DATE_2.
    assert row["resolution_suggestions"][0] == "PRODUCT_ORDER_DATE"


def test_metric_hiding_a_column_of_its_table_is_a_collision():
    row = _rows()["metrics.Region"]
    assert row["collision_detected"] is True
    assert "column would be left out" in row["validation_message"]
    assert "REGION" not in [s.upper() for s in row["resolution_suggestions"]]


def test_suggestions_do_not_repeat_the_table_name():
    suggestions = _rows()["metrics.Sales %"]["resolution_suggestions"]
    assert not any(s.startswith("SALES_SALES") for s in suggestions)


def test_resolve_all_clears_every_collision_and_keeps_every_column():
    rows = _rows()
    overrides = [
        {"source_path": p, "target_name": r["resolution_suggestions"][0]}
        for p, r in rows.items() if r["collision_detected"]
    ]
    assert len(overrides) == 4
    existing = {o["source_path"]: {"target_name": o["target_name"], "is_user_edited": True} for o in overrides}
    assert not [p for p, r in _rows(existing).items() if r["collision_detected"]]

    logging.disable(logging.WARNING)
    try:
        model = _model()
        _apply_mapping_overrides_from_config(
            model, Path("/nonexistent"), {"mappings_overrides": overrides, "target": {"type": "snowflake"}},
        )
        cfg = SnowflakeConfig(account="t.local", user="u", password="p", warehouse="w",
                              database="DB", schema_name="SCH", role="r")
        ddl = next(d for d in SnowflakeEmitter(cfg, ConnectorBehavior()).generate_ddls(model) if "SEMANTIC VIEW" in d.upper())
    finally:
        logging.disable(logging.NOTSET)
    dims = ddl[ddl.upper().find("DIMENSIONS"):ddl.upper().find("METRICS")]
    assert 'SALES."REGION"' in dims                  # the column is no longer dropped
    assert 'PRODUCT."PRODUCT_ORDER_DATE"' in dims    # the user's name is deployed
